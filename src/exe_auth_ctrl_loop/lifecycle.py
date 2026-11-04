"""
Intent: Make "is this partition allowed to run unattended" an explicit state with recorded
        transitions, instead of an inference from evidence counts at each call
Context: Implements the transition contract in docs/v2/spec.md section 9 over the evidence
        that shadow.py admits. AuthorityController still makes the per-operation decision;
        this layer decides whether the partition may be autonomous at all.
Pattern: A table of legal (state, event) pairs. Anything not in the table raises, so no
        code path can move a partition to AUTONOMOUS except QUALIFYING on GATE_MET.
Future: Durable since v3 Step 02: each partition's record is a row of the `lifecycle`
        table, and every public method runs as one transaction with the ledger events it
        writes, so a restart keeps suspensions and lineage flags (closes L4). As a
        RedemptionGuard this manager also refuses an already-issued token once its
        partition leaves AUTONOMOUS, is retired, or inherits a suspension; the gateway asks
        it inside the consume transaction (docs/v3/spec.md §3).
"""

from __future__ import annotations

import json
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
from .principals import Principals, release_payload
from .revocation import LINEAGE_DESCENDANTS, revoke, successor_key
from .shadow import Oracle, Provenance, ShadowEvidenceLog
from .store import partition_text


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
    ORACLE_CHANGED = "ORACLE_CHANGED"


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
    # v3 Step 06 (RV-04, O-2/O-9): a new oracle opens a new epoch with counts reset, so an
    # autonomous partition goes back to qualifying; the others keep their state
    (S.UNESTABLISHED, E.ORACLE_CHANGED): S.UNESTABLISHED,
    (S.QUALIFYING, E.ORACLE_CHANGED): S.QUALIFYING,
    (S.AUTONOMOUS, E.ORACLE_CHANGED): S.QUALIFYING,
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
        principals: Principals | None = None,
    ) -> None:
        # constraint: one database for the record, the evidence it publishes, and the
        # ledger events it writes, so a transition and its record commit together or not
        if shadow.store is not store.store:
            raise ValueError("the shadow log and the evidence store must share one Store")
        self.shadow = shadow
        self.ledger = shadow.ledger
        self.store = store
        self.db = store.store
        self.policy = policy
        self.controller = controller
        self.clock = clock
        # constraint: release() verifies a human signature through this; without it,
        # release() refuses, so an unauthenticated release cannot happen by omission
        self.principals = principals
        # effect: a label admitted by the shadow log moves this partition's state in the
        # label's own transaction, so a bound that falls revokes at once (RV-03)
        self._in_label = False
        self._deferred: set[PartitionKey] = set()
        shadow.listeners.append(self._on_label)

    def record(self, key: PartitionKey) -> PartitionRecord | None:
        """A copy of the stored record; changing it changes nothing in the database."""
        return self._load(key)

    def state(self, key: PartitionKey) -> LifecycleState:
        record = self._load(key)
        return record.state if record else LifecycleState.UNESTABLISHED

    def evaluate(self, proposal: Proposal) -> Decision:
        """
        intent: One decision per operation, with the lifecycle state enforced on top
        constraint: Non-autonomous states can only add reason codes and narrow the route
        constraint: Decisions are only half of it. Register this manager as a gateway
                    guard -- ExecutionGateway(guards=(manager, ...)) -- or a token issued
                    here outlives a later loss of autonomy or an inherited lineage
                    suspension until its TTL. Suspension, declared invalidation, and
                    retirement are also written to the store, so a gateway holding only
                    the store still refuses those; QUALIFYING and LINEAGE_SUSPENDED are
                    known to the guard alone.
        """
        key = proposal.partition
        with self.db.transaction():
            state = self.refresh(key)
            decision = self.controller.evaluate(proposal)
            record = self._require(key)
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
        with self.db.transaction():
            return self._refresh(key)

    def _refresh(self, key: PartitionKey) -> LifecycleState:
        record = self._load(key)
        if record is None:
            record = PartitionRecord(TRANSITIONS[(None, E.FIRST_PROPOSAL)])
            self._save(key, record)
            # O-4 (issue #43): registration order is the k of online α spending, and the
            # first epoch's allocation is on the record from the start
            self._record_alpha(key, record, self.shadow.epoch_floor(key), "REGISTERED")
            self._log(key, record, None, E.FIRST_PROPOSAL, ())
            # constraint: a successor of a suspended (or lineage-suspended) partition, by
            # any edge -- key churn or a policy change -- starts lineage-suspended, so no
            # version edit clears a suspension (closes L12)
            inherited = self._suspended_predecessors(key)
            if inherited:
                self._suspend_lineage(key, record, inherited)
        if not record.retired and key.policy_version != self.policy.version:
            self._retire(key, record, "POLICY_CHANGE")
        if record.retired or record.state == LifecycleState.SUSPENDED:
            self._sync_store(key, record)
            return record.state

        self.shadow.expire_unlabeled()
        snapshot = self.shadow.snapshot(key, self.clock(), pending=self.policy.pending)
        # method: publish before moving, so the transition record names the evidence
        # version the gate actually relied on
        self._sync_store(key, record)
        # effect: these are `if`s, not `elif`s, so a partition first seen with enough
        # admitted evidence walks UNESTABLISHED -> QUALIFYING -> AUTONOMOUS in this one
        # call, logging TRIAL_ADMITTED then GATE_MET. Both records name the version
        # published just above, which is the evidence the gate read; the republish at the
        # end carries the same counts under the new state.
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
        with self.db.transaction():
            self._record_severe(key, provenance, reason)

    def _record_severe(self, key: PartitionKey, provenance: Provenance, reason: str) -> None:
        self.refresh(key)
        record = self._require(key)
        reasons = (reason, f"PROVENANCE_{provenance.value.upper()}")
        if record.retired:
            # effect: a retired partition routes nothing and cannot be released, so
            # suspending it would create a state no call can leave. Record and return.
            self._log(key, record, record.state, None, (*reasons, "PARTITION_RETIRED"))
            return
        if record.state == LifecycleState.SUSPENDED:
            # effect: a second severe failure while suspended is recorded, not a crash,
            # and does not restart the clock on the first one; the original reason stands
            self._log(key, record, record.state, None, (*reasons, "ALREADY_SUSPENDED"))
            return
        record.suspension_reason = reason
        self._move(key, record, E.SEVERE_FAILURE, reasons)
        self._sync_store(key, record)

    def release(
        self, key: PartitionKey, actor: str, reason: str, mac: str, *, oracle: Oracle,
    ) -> None:
        """
        intent: The documented restoration step: a verified human, a stated reason, a new
                epoch
        effect: SUSPENDED goes to QUALIFYING, never AUTONOMOUS. Evidence counting restarts
                from zero, so requalification needs fresh admissible trials.
        constraint: Also the only way to clear lineage_suspended on a successor partition.
        constraint: `actor` must be an active HUMAN principal and `mac` that human's
                    signature over principals.release_payload(key, epoch, reason,
                    oracle_id, oracle_version) for the partition's current epoch, so a
                    signed release cannot be replayed at a later suspension (spec §8; closes
                    L9's "any non-empty actor name").
        effect: The new epoch is bound to `oracle` in the same transaction (spec §7 rule 11,
                issue #41): the human who releases chooses who labels next, and no label
                can bind the epoch by arriving first.
        """
        if not actor or not reason:
            raise ValueError("release requires a named actor and a reason")
        with self.db.transaction():
            self._release(key, actor, reason, mac, oracle)

    def _release(
        self, key: PartitionKey, actor: str, reason: str, mac: str, oracle: Oracle,
    ) -> None:
        record = self._load(key)
        if record is None or (
            record.state != LifecycleState.SUSPENDED and not record.lineage_suspended
        ):
            raise ValueError("partition is not suspended")
        if record.retired:
            raise ValueError("retired partition cannot be released")
        if self.principals is None or not self.principals.verify_human(
            actor,
            release_payload(key, record.epoch, reason, oracle.oracle_id, oracle.oracle_version),
            mac,
        ):
            raise PermissionError("release needs a valid signature from an active human")
        floor = self.shadow.open_epoch(key)
        self.shadow.bind_oracle(key, oracle)
        record.epoch += 1
        self._record_alpha(key, record, floor, "HUMAN_RELEASE")
        record.lineage_suspended = False
        if record.state == LifecycleState.SUSPENDED:
            record.suspension_reason = None
            self._move(key, record, E.HUMAN_RELEASE, (
                f"RELEASED_BY:{actor}", reason,
                f"ORACLE:{oracle.oracle_id}@{oracle.oracle_version}",
            ))
        else:
            self._save(key, record)
            self._log(key, record, record.state, None, (
                "LINEAGE_RELEASED", actor, reason,
                f"ORACLE:{oracle.oracle_id}@{oracle.oracle_version}",
            ))
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
        with self.db.transaction() as conn:
            self.refresh(old)
            previous = self._require(old)
            self._retire(old, previous, "KEY_FIELD_CHURN")
            # effect: the edge is recorded for every churn, suspended or not, so lineage
            # is a durable fact rather than only a flag on the successor
            conn.execute(
                "INSERT OR IGNORE INTO lineage VALUES (?, ?, ?, ?)",
                (partition_text(old), partition_text(new), "KEY_FIELD_CHURN",
                 self.clock().isoformat()),
            )
            self.refresh(new)
            successor = self._require(new)
            if ((previous.state == LifecycleState.SUSPENDED or previous.lineage_suspended)
                    and not successor.lineage_suspended):
                # a successor that already existed: refresh() did not create it, so it
                # did not inherit at creation; flag it now
                self._suspend_lineage(new, successor, [partition_text(old)])

    def change_oracle(self, key: PartitionKey, oracle: Oracle, *, by: str) -> None:
        """
        intent: Move a partition to a new oracle without pooling its labels with the old
                one's (O-2, O-9; RV-04)
        effect: Opens a new epoch (counts reset to zero), takes an autonomous partition back
                to QUALIFYING, and revokes its evidence-based tokens in the same transaction.
                Approval-based tokens are kept: an approval does not rest on the counts.
        constraint: A suspended or retired partition is refused: release it first, or use
                    its successor. Binding labels to the epoch's oracle is v3 Step 08.
        """
        with self.db.transaction():
            self.refresh(key)
            record = self._require(key)
            if record.retired or record.state == LifecycleState.SUSPENDED:
                raise ValueError("a retired or suspended partition cannot change oracle")
            floor = self.shadow.open_epoch(key)
            record.epoch += 1
            # spec §7 rule 11: the new epoch is bound to the new oracle at its opening
            self.shadow.bind_oracle(key, oracle)
            self._record_alpha(key, record, floor, "ORACLE_CHANGED")
            self._move(key, record, E.ORACLE_CHANGED,
                       (f"ORACLE:{oracle.oracle_id}@{oracle.oracle_version}", f"BY:{by}"))
            self._sync_store(key, record)

    def _retire_policy_version(self, old_version: str, policy: Policy) -> list[str]:
        """
        intent: Retire every partition on a superseded policy version at once (RV-07)
        constraint: Private to revocation.PolicyAuthority.change(), which revokes in the same
                    transaction. Called alone it would retire partitions and leave their
                    tokens issued.
        effect: Each is retired and its evidence invalidated, and a lineage edge is recorded
                to the key it becomes under the new version, so a suspension passes on.
                Revocation is the caller's single POLICY_CHANGED event
                (revocation.PolicyAuthority), not one event per partition.
        """
        retired: list[str] = []
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT partition FROM lifecycle WHERE retired = 0"
            ).fetchall()
            for (text,) in rows:
                key = PartitionKey(**json.loads(text))
                if key.policy_version != old_version:
                    continue
                self._retire(key, self._require(key), "POLICY_CHANGE")
                conn.execute(
                    "INSERT OR IGNORE INTO lineage VALUES (?, ?, ?, ?)",
                    (text, successor_key(text, policy.version), "POLICY_CHANGE",
                     self.clock().isoformat()),
                )
                retired.append(text)
        return retired

    def commit(self, token: AuthorizationToken, proposal: Proposal) -> None:
        """RedemptionGuard: the lifecycle charges nothing at redemption."""

    def redemption_blockers(
        self, token: AuthorizationToken, proposal: Proposal
    ) -> tuple[str, ...]:
        """
        intent: Veto a token whose partition has lost authority since it was issued
        method: Read-only (RedemptionGuard). Reads the current record and, for an
                evidence-based token on an AUTONOMOUS partition, recomputes the gate from
                the current counts and clock (v3 Step 06, spec §5): a bound that fell or
                evidence that went stale since the last evaluate() refuses the token now.
        effect: An evidence-based token needs AUTONOMOUS and a gate that still holds; a
                human-approved one survives a non-autonomous state and the statistical gate
                but not retirement, lineage suspension, or suspension
        """
        record = self._load(token.partition)
        if record is None:
            return ()
        reasons = list(self._blockers(token.partition, record))
        if not token.human_approved:
            if record.state != LifecycleState.AUTONOMOUS:
                reasons.append(f"LIFECYCLE_{record.state.value}")
            else:
                snapshot = self.shadow.snapshot(token.partition, self.clock(),
                                                pending=self.policy.pending)
                reasons.extend(self._gate_reasons(token.partition, record, snapshot))
        return tuple(dict.fromkeys(reasons))

    def _on_label(self, key: PartitionKey) -> None:
        """Shadow-log listener: re-walk this partition's state inside the label's transaction."""
        # constraint: a re-walk can resolve other partitions' trials (expire_unlabeled
        # censors overdue trials of every partition), and those callbacks arrive here while
        # the walk is in progress. They are deferred, not dropped, and walked before this
        # call returns, still inside the label's transaction, so each partition's GATE_LOST
        # revocation is as synchronous as the first's (PR #35 review).
        if self._in_label:
            self._deferred.add(key)
            return
        self._in_label = True
        try:
            self._deferred.add(key)
            while self._deferred:
                pending = self._deferred.pop()
                record = self._load(pending)
                if (record is not None and not record.retired
                        and record.state != LifecycleState.SUSPENDED):
                    self._refresh(pending)
        finally:
            self._in_label = False
            self._deferred.clear()

    def _record_alpha(
        self, key: PartitionKey, record: PartitionRecord, floor: int, reason: str
    ) -> None:
        """
        intent: Every epoch opening is on the record with the α it starts with (spec §7 rule
                12), so a reset of counts is never a silent reset of the error budget
        method: The α is the policy's allocation for this partition's registration index k
                (O-4) and this epoch e = record.epoch + 1 (O-8): Policy.allocated_alpha. A
                REGISTERED row takes the next k; every later row reuses the partition's.
        constraint: Joins the caller's transaction (the epoch opening), so the row and the
                    opening commit or roll back together; the table is append-only, so a
                    row committed on its own could never be withdrawn (PR #39 review)
        """
        with self.db.transaction() as conn:
            if reason == "REGISTERED":
                (index,) = conn.execute(
                    "SELECT COUNT(*) + 1 FROM alpha_ledger WHERE reason = 'REGISTERED'"
                ).fetchone()
            else:
                index = self._partition_index(key)
            alpha = self.policy.allocated_alpha(index, record.epoch + 1)
            conn.execute(
                "INSERT INTO alpha_ledger (partition, epoch, floor_sequence, alpha, reason,"
                " opened_at, partition_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (partition_text(key), record.epoch, floor, alpha, reason,
                 self.clock().isoformat(), index),
            )

    def _partition_index(self, key: PartitionKey) -> int:
        """The partition's registration index k, from its REGISTERED row."""
        row = self.db.connection().execute(
            "SELECT partition_index FROM alpha_ledger WHERE partition = ?"
            " AND reason = 'REGISTERED'", (partition_text(key),),
        ).fetchone()
        if row is None:
            raise LookupError("partition has no registration in alpha_ledger")
        return int(row[0])

    def _alpha(self, key: PartitionKey) -> float:
        """
        intent: The α the gate uses for the partition's current epoch: the one recorded when
                that epoch opened, so what alpha_ledger says is what the gate applied
        """
        row = self.db.connection().execute(
            "SELECT alpha FROM alpha_ledger WHERE partition = ?"
            " ORDER BY entry_id DESC LIMIT 1", (partition_text(key),),
        ).fetchone()
        if row is None:
            raise LookupError("partition has no α allocation in alpha_ledger")
        return float(row[0])

    def _suspended_predecessors(self, key: PartitionKey) -> list[str]:
        rows = self.db.connection().execute(
            "SELECT predecessor FROM lineage WHERE successor = ?", (partition_text(key),)
        ).fetchall()
        found = []
        for (text,) in rows:
            record = self._load(PartitionKey(**json.loads(text)))
            if record is not None and (
                record.state == LifecycleState.SUSPENDED or record.lineage_suspended
            ):
                found.append(text)
        return found

    def _suspend_lineage(
        self, key: PartitionKey, record: PartitionRecord, predecessors: list[str]
    ) -> None:
        """Flag a successor lineage-suspended and revoke its tokens (RV-06)."""
        record.lineage_suspended = True
        self._save(key, record)
        self._log(key, record, record.state, None, ("LINEAGE_SUSPENDED",))
        revoke(self.db, "LINEAGE_SUSPENDED", "partition = ?", (partition_text(key),),
               details={"predecessors": predecessors})

    def _gate_reasons(
        self, key: PartitionKey, record: PartitionRecord, snapshot: EvidenceSnapshot | None
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if snapshot is None or snapshot.n < self.policy.n_min:
            reasons.append("EVIDENCE_IMMATURE")
        stored = self.store.get(key)
        if stored is not None and not stored.valid:
            # a declared invalidation blocks the gate until whoever declared it lifts it
            reasons.append("EVIDENCE_INVALID")
        if snapshot is not None:
            if self.clock() - snapshot.collected_until > self.policy.max_evidence_age:
                reasons.append("EVIDENCE_STALE")
            required = self.policy.minimum_success_by_risk.get(key.risk_class, 1.0)
            lower = self.policy.lower_bound(snapshot.successes, snapshot.failures,
                                            self._alpha(key))
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
        """
        effect: Besides marking the record, declares the stored evidence invalid. The
                shadow-derived evidence_id is stable per partition, so the gateway's
                EVIDENCE_WITHDRAWN check cannot notice a retirement; EVIDENCE_INVALID can,
                and it is checked by a gateway holding only the store. T-19 therefore
                holds whether or not this manager is also registered as a guard.
        """
        record.retired = True
        self._save(key, record)
        current = self.store.get(key)
        if current is not None and current.valid:
            # constraint: the retirement is the revoking event, not the invalidation
            self.store.invalidate(key, f"PARTITION_RETIRED:{reason}", revoke_tokens=False)
        self._log(key, record, record.state, None, ("PARTITION_RETIRED", reason))
        if reason == "KEY_FIELD_CHURN":
            # RV-05. A policy-version retirement is revoked once by POLICY_CHANGED
            # (revocation.PolicyAuthority) for every partition together
            revoke(self.db, "PARTITION_RETIRED", "partition = ?", (partition_text(key),),
                   details={"reason": reason})

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
        self._save(key, record)
        self._log(key, record, previous, event, reasons)
        # constraint: the transitions that take authority away revoke in this same
        # transaction (spec §5). Evidence expiry is a time condition, not an event: the
        # gateway checks it at redemption.
        text = partition_text(key)
        if event is E.SEVERE_FAILURE:  # RV-01: this partition and every successor
            revoke(self.db, "SEVERE_FAILURE", LINEAGE_DESCENDANTS, (text,),
                   details={"reasons": list(reasons)})
        elif event is E.GATE_LOST_OUTCOME:  # RV-03: approvals do not rest on the gate
            revoke(self.db, "GATE_LOST", "partition = ?", (text,), evidence_only=True,
                   details={"reasons": list(reasons)})
        elif event is E.ORACLE_CHANGED:  # RV-04: counts were reset under a new oracle
            revoke(self.db, "ORACLE_CHANGED", "partition = ?", (text,), evidence_only=True,
                   details={"reasons": list(reasons)})

    def _load(self, key: PartitionKey) -> PartitionRecord | None:
        row = self.db.connection().execute(
            "SELECT state, epoch, retired, lineage_suspended, suspension_reason"
            " FROM lifecycle WHERE partition = ?",
            (partition_text(key),),
        ).fetchone()
        if row is None:
            return None
        state, epoch, retired, lineage_suspended, suspension_reason = row
        return PartitionRecord(
            LifecycleState(state), epoch, bool(retired), bool(lineage_suspended),
            suspension_reason,
        )

    def _require(self, key: PartitionKey) -> PartitionRecord:
        record = self._load(key)
        if record is None:
            raise KeyError("partition has no lifecycle record")
        return record

    def _save(self, key: PartitionKey, record: PartitionRecord) -> None:
        self.db.connection().execute(
            "INSERT INTO lifecycle VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (partition) DO UPDATE SET state = excluded.state,"
            " epoch = excluded.epoch, retired = excluded.retired,"
            " lineage_suspended = excluded.lineage_suspended,"
            " suspension_reason = excluded.suspension_reason",
            (partition_text(key), record.state.value, record.epoch, int(record.retired),
             int(record.lineage_suspended), record.suspension_reason),
        )

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
        constraint: Republishing bumps the version and keeps the evidence_id, which is
                    stable per partition for shadow evidence. So a bump here never trips
                    the gateway's EVIDENCE_WITHDRAWN; that check is for a record replaced
                    from outside. Retirement is enforced by _retire's invalidation and by
                    this manager as a guard, not by versioning.
        """
        current = self.store.get(key)
        suspended = record.state == LifecycleState.SUSPENDED
        base = current if suspended else self.shadow.snapshot(
            key, self.clock(), pending=self.policy.pending)
        if base is None:
            base = current and self._empty(key, current.evidence_id)
        if suspended and base is None:
            base = self._empty(key, f"lifecycle-{digest(key.__dict__)[:16]}")
        if base is None:
            return
        # constraint: `valid` belongs to whoever declared the invalidation (EvidenceStore
        # .invalidate), not to the lifecycle. It is carried forward, never reset here, so a
        # republish of fresh counts cannot quietly un-invalidate a record.
        valid = current.valid if current is not None else True
        reason = current.invalidation_reason if current is not None else None
        desired = replace(
            base,
            valid=valid,
            suspended=suspended,
            invalidation_reason=record.suspension_reason if suspended else reason,
            # the epoch's allocated α travels with the evidence, so a controller reading
            # the store directly applies it too (it can only tighten the gate)
            alpha=self._alpha(key),
        )
        # constraint: evidence_id is part of the comparison so a hand-seeded record with
        # the same counts as the first shadow snapshot is still replaced, and tokens bind
        # to the shadow-derived id rather than a stale one
        if current is not None and (
            (current.evidence_id, current.successes, current.failures, current.collected_until,
             current.suspended, current.valid, current.alpha)
            == (desired.evidence_id, desired.successes, desired.failures, desired.collected_until,
                desired.suspended, desired.valid, desired.alpha)
        ):
            return
        self.store.put(replace(desired, version=current.version + 1 if current else 1))

    def _empty(self, key: PartitionKey, evidence_id: str) -> EvidenceSnapshot:
        now = self.clock()
        return EvidenceSnapshot(evidence_id, 1, key, 0, 0, now, now)
