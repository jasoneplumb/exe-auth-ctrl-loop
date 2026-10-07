"""
Intent: Make "is this partition allowed to run unattended" an explicit state with recorded
        transitions, instead of an inference from evidence counts at each call
Context: Implements the transition contract in docs/v2/spec.md section 9 over the evidence
        that shadow.py admits. AuthorityController still makes the per-operation decision;
        this layer decides whether the partition may be autonomous at all.
Pattern: A table of legal (state, event) pairs. Anything not in the table raises, so no
        code path can move a partition to AUTONOMOUS except QUALIFYING on GATE_MET.
Future: In-memory. Token invalidation on suspension is Step 05, so an already-issued
        token still outlives a suspension here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Callable

from .authority import (
    AuthorityController,
    AuthorizationToken,
    Decision,
    EvidenceSnapshot,
    EvidenceStore,
    PartitionKey,
    Policy,
    Proposal,
    Route,
    digest,
    utcnow,
)
from .shadow import Provenance, ShadowEvidenceLog


class LifecycleState(str, Enum):
    UNESTABLISHED = "UNESTABLISHED"
    QUALIFYING = "QUALIFYING"
    AUTONOMOUS = "AUTONOMOUS"
    SUSPENDED = "SUSPENDED"


class LifecycleEvent(str, Enum):
    FIRST_PROPOSAL = "FIRST_PROPOSAL"
    TRIAL_ADMITTED = "TRIAL_ADMITTED"
    GATE_MET = "GATE_MET"
    GATE_LOST_OUTCOME = "GATE_LOST_OUTCOME"
    EVIDENCE_EXPIRED = "EVIDENCE_EXPIRED"
    SEVERE_FAILURE = "SEVERE_FAILURE"
    HUMAN_RELEASE = "HUMAN_RELEASE"


S = LifecycleState
E = LifecycleEvent

# constraint: the only entry to AUTONOMOUS is (QUALIFYING, GATE_MET). SUSPENDED leaves only
# through HUMAN_RELEASE, and only to QUALIFYING.
TRANSITIONS: dict[tuple[LifecycleState | None, LifecycleEvent], LifecycleState] = {
    (None, E.FIRST_PROPOSAL): S.UNESTABLISHED,
    (S.UNESTABLISHED, E.TRIAL_ADMITTED): S.QUALIFYING,
    (S.QUALIFYING, E.GATE_MET): S.AUTONOMOUS,
    (S.AUTONOMOUS, E.GATE_LOST_OUTCOME): S.QUALIFYING,
    (S.AUTONOMOUS, E.EVIDENCE_EXPIRED): S.QUALIFYING,
    (S.UNESTABLISHED, E.SEVERE_FAILURE): S.SUSPENDED,
    (S.QUALIFYING, E.SEVERE_FAILURE): S.SUSPENDED,
    (S.AUTONOMOUS, E.SEVERE_FAILURE): S.SUSPENDED,
    (S.SUSPENDED, E.HUMAN_RELEASE): S.QUALIFYING,
}


@dataclass
class PartitionRecord:
    state: LifecycleState
    epoch: int = 0
    retired: bool = False
    lineage_suspended: bool = False
    suspension_reason: str | None = None


class LifecycleManager:
    """
    intent: Own each partition's lifecycle state and gate the controller's autonomy by it
    method: refresh() reads admissible shadow evidence, walks the legal events, publishes
            the resulting snapshot to the store, and records every transition in the ledger
    effect: A snapshot placed in the store by hand cannot grant autonomy through evaluate():
            a decision that reaches AUTONOMOUS or AUDIT while the state is anything else is
            downgraded to HUMAN_APPROVAL with reason codes.
    """

    def __init__(
        self,
        shadow: ShadowEvidenceLog,
        store: EvidenceStore,
        policy: Policy,
        controller: AuthorityController,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.shadow = shadow
        self.ledger = shadow.ledger
        self.store = store
        self.policy = policy
        self.controller = controller
        self.clock = clock
        self._records: dict[PartitionKey, PartitionRecord] = {}

    def record(self, key: PartitionKey) -> PartitionRecord | None:
        return self._records.get(key)

    def state(self, key: PartitionKey) -> LifecycleState:
        record = self._records.get(key)
        return record.state if record else LifecycleState.UNESTABLISHED

    def evaluate(self, proposal: Proposal) -> Decision:
        """
        intent: One decision per operation, with the lifecycle state enforced on top
        constraint: Non-autonomous states can only add reason codes and narrow the route
        """
        key = proposal.partition
        state = self.refresh(key)
        decision = self.controller.evaluate(proposal)
        record = self._records[key]
        extra = self._blockers(key, record)
        # constraint: a retired or lineage-suspended partition keeps its old state but has
        # no authority, so the state alone is not the test
        if state != LifecycleState.AUTONOMOUS or extra:
            extra = (f"LIFECYCLE_{state.value}", *extra)
            if decision.route in {Route.AUTONOMOUS, Route.AUDIT}:
                decision = replace(
                    decision,
                    route=Route.HUMAN_APPROVAL,
                    audit_probability=0.0,
                    audit_draw=None,
                    reason_codes=tuple(
                        r for r in decision.reason_codes
                        if r not in {"AUTHORITY_SUFFICIENT", "RANDOM_AUDIT"}
                    ),
                )
        if extra:
            decision = replace(
                decision,
                reason_codes=decision.reason_codes
                + tuple(r for r in extra if r not in decision.reason_codes),
            )
        return decision

    def refresh(self, key: PartitionKey) -> LifecycleState:
        """
        intent: Bring one partition's state up to date with its evidence and the policy
        effect: Registers the partition on first sight (T-01), retires it when its policy
                version no longer matches (T-19), and otherwise moves only along TRANSITIONS
        """
        record = self._records.get(key)
        if record is None:
            record = PartitionRecord(TRANSITIONS[(None, E.FIRST_PROPOSAL)])
            self._records[key] = record
            self._log(key, record, None, E.FIRST_PROPOSAL, ())
        if not record.retired and key.policy_version != self.policy.version:
            self._retire(key, record, "POLICY_CHANGE")
        if record.retired or record.state == LifecycleState.SUSPENDED:
            self._sync_store(key, record)
            return record.state

        self.shadow.expire_unlabeled()
        snapshot = self.shadow.snapshot(key, self.clock())
        # method: publish before moving, so the transition record names the evidence
        # version the gate actually relied on
        self._sync_store(key, record)
        if record.state == LifecycleState.UNESTABLISHED and snapshot and snapshot.n >= 1:
            self._move(key, record, E.TRIAL_ADMITTED, ())
        if record.state == LifecycleState.QUALIFYING:
            reasons = self._gate_reasons(key, record, snapshot)
            if not reasons:
                self._move(key, record, E.GATE_MET, ())
        elif record.state == LifecycleState.AUTONOMOUS:
            reasons = self._gate_reasons(key, record, snapshot)
            if "EVIDENCE_STALE" in reasons:
                self._move(key, record, E.EVIDENCE_EXPIRED, reasons)
            elif reasons:
                self._move(key, record, E.GATE_LOST_OUTCOME, reasons)
        self._sync_store(key, record)
        return record.state

    def record_severe(self, key: PartitionKey, provenance: Provenance, reason: str) -> None:
        """
        intent: Withdraw autonomy at once on a severe failure, from any provenance
        constraint: Human-approved executions are excluded from evidence but not from this
                    safety signal. Suspension persists until release().
        """
        self.refresh(key)
        record = self._records[key]
        record.suspension_reason = reason
        self._move(
            key, record, E.SEVERE_FAILURE, (reason, f"PROVENANCE_{provenance.value.upper()}")
        )
        self._sync_store(key, record)

    def release(self, key: PartitionKey, actor: str, reason: str) -> None:
        """
        intent: The documented restoration step: a named human, a stated reason, a new epoch
        effect: SUSPENDED goes to QUALIFYING, never AUTONOMOUS. Evidence counting restarts
                from zero, so requalification needs fresh admissible trials.
        constraint: Also the only way to clear lineage_suspended on a successor partition.
        """
        if not actor or not reason:
            raise ValueError("release requires a named actor and a reason")
        record = self._records.get(key)
        if record is None or (
            record.state != LifecycleState.SUSPENDED and not record.lineage_suspended
        ):
            raise ValueError("partition is not suspended")
        if record.retired:
            raise ValueError("retired partition cannot be released")
        self.shadow.open_epoch(key)
        record.epoch += 1
        record.lineage_suspended = False
        if record.state == LifecycleState.SUSPENDED:
            record.suspension_reason = None
            self._move(key, record, E.HUMAN_RELEASE, (f"RELEASED_BY:{actor}", reason))
        else:
            self._log(key, record, record.state, None, ("LINEAGE_RELEASED", actor, reason))
        self._sync_store(key, record)

    def on_churn(self, old: PartitionKey, new: PartitionKey) -> None:
        """
        intent: Record that `new` replaces `old` after a config change (T-19, T-20)
        constraint: Nothing carries over except suspension: a successor of a SUSPENDED
                    partition is lineage_suspended, so editing a version string cannot
                    clear a suspension. The old partition is retired and never routes again.
        """
        if old == new:
            raise ValueError("churn requires a different partition key")
        self.refresh(old)
        previous = self._records[old]
        self._retire(old, previous, "KEY_FIELD_CHURN")
        self.refresh(new)
        if previous.state == LifecycleState.SUSPENDED or previous.lineage_suspended:
            successor = self._records[new]
            successor.lineage_suspended = True
            self._log(new, successor, successor.state, None, ("LINEAGE_SUSPENDED",))

    def commit(self, token: AuthorizationToken, proposal: Proposal) -> None:
        """RedemptionGuard: the lifecycle charges nothing at redemption."""

    def redemption_blockers(
        self, token: AuthorizationToken, proposal: Proposal
    ) -> tuple[str, ...]:
        """
        intent: Veto a token whose partition has lost authority since it was issued
        method: Read-only view of the current record (RedemptionGuard). No refresh: a
                state change caused by ordinary evidence shows up at the next evaluate(),
                which is the documented boundary for what invalidates a token.
        effect: An evidence-based token needs AUTONOMOUS; a human-approved one survives a
                non-autonomous state but not retirement, lineage suspension, or suspension
        """
        record = self._records.get(token.partition)
        if record is None:
            return ()
        reasons = list(self._blockers(token.partition, record))
        if not token.human_approved and record.state != LifecycleState.AUTONOMOUS:
            reasons.append(f"LIFECYCLE_{record.state.value}")
        return tuple(dict.fromkeys(reasons))

    def _gate_reasons(
        self, key: PartitionKey, record: PartitionRecord, snapshot: EvidenceSnapshot | None
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if snapshot is None or snapshot.n < self.policy.n_min:
            reasons.append("EVIDENCE_IMMATURE")
        if snapshot is not None:
            if self.clock() - snapshot.collected_until > self.policy.max_evidence_age:
                reasons.append("EVIDENCE_STALE")
            required = self.policy.minimum_success_by_risk.get(key.risk_class, 1.0)
            lower = self.policy.lower_bound(snapshot.successes, snapshot.failures)
            if lower < required:
                reasons.append("BOUND_BELOW_POLICY")
        reasons.extend(self._blockers(key, record))
        return tuple(reasons)

    @staticmethod
    def _blockers(key: PartitionKey, record: PartitionRecord) -> tuple[str, ...]:
        reasons: list[str] = []
        if record.lineage_suspended:
            reasons.append("LINEAGE_SUSPENDED")
        if record.retired:
            reasons.append("PARTITION_RETIRED")
        if record.state == LifecycleState.SUSPENDED:
            reasons.append("PARTITION_SUSPENDED")
        return tuple(reasons)

    def _retire(self, key: PartitionKey, record: PartitionRecord, reason: str) -> None:
        record.retired = True
        self._log(key, record, record.state, None, ("PARTITION_RETIRED", reason))

    def _move(
        self,
        key: PartitionKey,
        record: PartitionRecord,
        event: LifecycleEvent,
        reasons: tuple[str, ...],
    ) -> None:
        target = TRANSITIONS.get((record.state, event))
        if target is None:
            raise ValueError(f"illegal lifecycle transition: {record.state.value} on {event.value}")
        previous = record.state
        record.state = target
        self._log(key, record, previous, event, reasons)

    def _log(
        self,
        key: PartitionKey,
        record: PartitionRecord,
        previous: LifecycleState | None,
        event: LifecycleEvent | None,
        reasons: tuple[str, ...],
    ) -> None:
        current = self.store.get(key)
        self.ledger.append(
            "lifecycle.transition",
            digest(key.__dict__)[:16],
            "controller",
            {
                "partition": dict(key.__dict__),
                "from": previous.value if previous else None,
                "to": record.state.value,
                "event": event.value if event else None,
                "reasons": list(reasons),
                "epoch": record.epoch,
                "retired": record.retired,
                "lineage_suspended": record.lineage_suspended,
                "policy_version": self.policy.version,
                "evidence_id": current.evidence_id if current else None,
                "evidence_version": current.version if current else None,
            },
            self.clock(),
        )

    def _sync_store(self, key: PartitionKey, record: PartitionRecord) -> None:
        """
        intent: Keep the v1 controller's view consistent with the lifecycle's
        effect: A suspended partition shows suspended=True, so even a caller that bypasses
                the manager and uses AuthorityController directly still fails closed.
        method: Counting evidence comes from the shadow log. After an epoch reset it is
                empty, and an existing snapshot is zeroed rather than left stale.
        """
        current = self.store.get(key)
        suspended = record.state == LifecycleState.SUSPENDED
        base = current if suspended else self.shadow.snapshot(key, self.clock())
        if base is None:
            base = current and self._empty(key, current.evidence_id)
        if suspended and base is None:
            base = self._empty(key, f"lifecycle-{digest(key.__dict__)[:16]}")
        if base is None:
            return
        desired = replace(
            base,
            suspended=suspended,
            invalidation_reason=record.suspension_reason if suspended else None,
        )
        if current is not None and (
            (current.successes, current.failures, current.collected_until, current.suspended)
            == (desired.successes, desired.failures, desired.collected_until, desired.suspended)
        ):
            return
        self.store.put(replace(desired, version=current.version + 1 if current else 1))

    def _empty(self, key: PartitionKey, evidence_id: str) -> EvidenceSnapshot:
        now = self.clock()
        return EvidenceSnapshot(evidence_id, 1, key, 0, 0, now, now)
