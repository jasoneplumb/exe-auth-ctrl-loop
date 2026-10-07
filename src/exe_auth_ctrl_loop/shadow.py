"""
Intent: Let a partition earn evidence from zero without letting a human reviewer shape it
Context: v1 counts only autonomous outcomes, but autonomy needs evidence, so an empty store
        can never qualify (docs/v2/baseline.md, gap 1). This module records autonomous-path
        candidates as frozen trials, labels them with an independent oracle, and exposes
        the counts as an EvidenceSnapshot the existing controller already understands.
Pattern: Freeze, then label. The candidate's digest and a ledger record exist before any
        human sees it, and every later record refers to that digest rather than replacing it.
Future: In-memory and single-process, like the ledger it writes to. Labels from an oracle
        estimate oracle-adjudicated acceptability, not live side-effect reliability
        (docs/v2/spec.md section 5); the bound is still the v1 Wilson bound until Step 04.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable

from .authority import (
    EvidenceSnapshot,
    EvidenceStore,
    PartitionKey,
    Proposal,
    digest,
    utcnow,
)
from .ledger import EventLedger


class Provenance(str, Enum):
    SHADOW = "shadow"
    AUTONOMOUS = "autonomous"
    AUDIT = "audit"
    HUMAN_APPROVED = "human_approved"


# constraint: HUMAN_APPROVED is recorded for observability and never counted. A reviewer
# removes exactly the failures the autonomous path would have committed (spec section 3).
COUNTING_PROVENANCE = frozenset({Provenance.SHADOW, Provenance.AUTONOMOUS, Provenance.AUDIT})


class TrialLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"


class TrialStatus(str, Enum):
    PENDING = "pending"
    LABELED = "labeled"
    CENSORED = "censored"


class OracleKind(str, Enum):
    CONSTRAINT_CHECK = "constraint_check"
    SANDBOX_REPLAY = "sandbox_replay"
    INDEPENDENT_REVIEW = "independent_review"


class Reason(str, Enum):
    DUPLICATE_TRIAL = "DUPLICATE_TRIAL"
    UNKNOWN_TRIAL = "UNKNOWN_TRIAL"
    WRONG_PARTITION = "WRONG_PARTITION"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    ALREADY_ADJUDICATED = "ALREADY_ADJUDICATED"
    LABEL_BEFORE_FREEZE = "LABEL_BEFORE_FREEZE"
    LATE_LABEL = "LATE_LABEL"
    LABEL_DEADLINE = "LABEL_DEADLINE"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REWRITE = "rewrite"


@dataclass(frozen=True)
class Oracle:
    oracle_id: str
    oracle_version: str
    kind: OracleKind
    validity_scope: str


@dataclass(frozen=True)
class ShadowTrial:
    trial_id: str
    provenance: Provenance
    partition: PartitionKey
    proposal_digest: str
    frozen_at: datetime
    frozen_sequence: int


@dataclass(frozen=True)
class Result:
    """accepted=False means the request was quarantined and changed no evidence."""

    accepted: bool
    reason: Reason | None = None


@dataclass(frozen=True)
class Counts:
    frozen: int = 0
    positive: int = 0
    negative: int = 0
    inconclusive: int = 0
    pending: int = 0

    @property
    def decisive(self) -> int:
        return self.positive + self.negative

    @property
    def coverage(self) -> float:
        """Decisive labels over frozen trials; pending and censored trials lower it."""
        return self.decisive / self.frozen if self.frozen else 0.0

    @property
    def censored_fraction(self) -> float:
        return self.inconclusive / self.frozen if self.frozen else 0.0


def _partition_payload(key: PartitionKey) -> dict[str, str]:
    return asdict(key)


class ShadowEvidenceLog:
    """
    intent: Hold the frozen trials and their labels, and nothing a reviewer can edit
    method: Every state change is appended to the ledger first; counts derive from trials
    constraint: Human review records are appended but never touch trial state. The only
                ways a trial leaves PENDING are an oracle label for its exact digest or
                the label deadline, so neither approval nor rejection can discard it.
    effect: Rejected requests are quarantined with a reason code and logged, so a replay
            of the ledger reproduces both the counts and what was refused.
    """

    def __init__(
        self,
        ledger: EventLedger,
        label_deadline: timedelta = timedelta(days=1),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.ledger = ledger
        self.label_deadline = label_deadline
        self.clock = clock
        self._trials: dict[str, ShadowTrial] = {}
        self._labels: dict[str, TrialLabel] = {}
        self._censored: set[str] = set()
        self._resolved_at: dict[str, datetime] = {}
        self._epoch_floor: dict[PartitionKey, int] = {}

    def open_epoch(self, key: PartitionKey) -> None:
        """
        intent: Start a partition's evidence over, e.g. after a human releases a suspension
        effect: Trials frozen before this ledger event stop counting, so data gathered
                around the incident cannot requalify the partition on the same day
        """
        event = self.ledger.append(
            "shadow.epoch_opened", digest(_partition_payload(key))[:16], "controller",
            {"partition": _partition_payload(key)},
        )
        self._epoch_floor[key] = event.sequence

    def freeze(
        self,
        trial_id: str,
        proposal: Proposal,
        provenance: Provenance = Provenance.SHADOW,
    ) -> Result:
        """
        intent: Commit to a candidate before anyone can review it
        effect: The ledger event is the pre-review commitment; frozen_sequence orders it
                before any human_review or label event for the same trial
        """
        if trial_id in self._trials:
            return self._quarantine(trial_id, Reason.DUPLICATE_TRIAL)
        now = self.clock()
        event = self.ledger.append(
            "shadow.frozen",
            trial_id,
            "controller",
            {
                "provenance": provenance.value,
                "partition": _partition_payload(proposal.partition),
                "proposal_digest": proposal.proposal_digest,
                "frozen_at": now.isoformat(),
            },
            now,
        )
        self._trials[trial_id] = ShadowTrial(
            trial_id, provenance, proposal.partition, proposal.proposal_digest, now,
            event.sequence,
        )
        return Result(True)

    def adjudicate(
        self,
        trial_id: str,
        partition: PartitionKey,
        proposal_digest: str,
        label: TrialLabel,
        oracle: Oracle,
        labeled_at: datetime | None = None,
    ) -> Result:
        """
        intent: Accept one oracle label for one frozen candidate, once
        constraint: The oracle must name the digest it labeled and the partition it
                    belongs to. A label for a reviewer's rewrite, another configuration,
                    a second label for the same trial, or one outside the deadline is
                    quarantined; a late label also censors the trial so a favorable
                    label cannot be held back until it is convenient.
        """
        trial = self._trials.get(trial_id)
        when = labeled_at or self.clock()
        if trial is None:
            return self._quarantine(trial_id, Reason.UNKNOWN_TRIAL)
        if partition != trial.partition:
            return self._quarantine(trial_id, Reason.WRONG_PARTITION)
        if proposal_digest != trial.proposal_digest:
            return self._quarantine(trial_id, Reason.DIGEST_MISMATCH)
        if self.status(trial_id) != TrialStatus.PENDING:
            return self._quarantine(trial_id, Reason.ALREADY_ADJUDICATED)
        if when < trial.frozen_at:
            return self._quarantine(trial_id, Reason.LABEL_BEFORE_FREEZE)
        if when > trial.frozen_at + self.label_deadline:
            self._censor(trial_id, Reason.LATE_LABEL)
            return self._quarantine(trial_id, Reason.LATE_LABEL)
        self.ledger.append(
            "shadow.labeled",
            trial_id,
            oracle.oracle_id,
            {
                "label": label.value,
                "proposal_digest": proposal_digest,
                "oracle_id": oracle.oracle_id,
                "oracle_version": oracle.oracle_version,
                "oracle_kind": oracle.kind.value,
                "validity_scope": oracle.validity_scope,
                "labeled_at": when.isoformat(),
            },
            when,
        )
        self._labels[trial_id] = label
        self._resolved_at[trial_id] = when
        return Result(True)

    def record_human_review(
        self,
        trial_id: str,
        reviewer: str,
        action: ReviewAction,
        rewritten: Proposal | None = None,
    ) -> Result:
        """
        intent: Keep what a human did with the candidate without letting it act on evidence
        effect: Appends a record and returns. The trial, its digest, and its label slot are
                untouched, and a rewrite is only a digest in the record, never a trial.
        """
        trial = self._trials.get(trial_id)
        if trial is None:
            return self._quarantine(trial_id, Reason.UNKNOWN_TRIAL)
        self.ledger.append(
            "shadow.human_review",
            trial_id,
            reviewer,
            {
                "action": action.value,
                "frozen_digest": trial.proposal_digest,
                "rewritten_digest": rewritten.proposal_digest if rewritten else None,
                "provenance": Provenance.HUMAN_APPROVED.value,
            },
        )
        return Result(True)

    def expire_unlabeled(self) -> list[str]:
        """
        intent: Turn missing labels into visible censoring instead of silent drops
        effect: Pending trials past the deadline become inconclusive, which counts in n
                and never as a success, so non-response cannot raise the bound
        """
        now = self.clock()
        expired = [
            t.trial_id
            for t in self._trials.values()
            if self.status(t.trial_id) == TrialStatus.PENDING
            and now > t.frozen_at + self.label_deadline
        ]
        for trial_id in expired:
            self._censor(trial_id, Reason.LABEL_DEADLINE)
        return expired

    def status(self, trial_id: str) -> TrialStatus:
        if trial_id in self._labels:
            return TrialStatus.LABELED
        if trial_id in self._censored:
            return TrialStatus.CENSORED
        return TrialStatus.PENDING

    def trial(self, trial_id: str) -> ShadowTrial | None:
        return self._trials.get(trial_id)

    def counts(self, key: PartitionKey, provenance: Provenance) -> Counts:
        counts = Counts()
        for trial in self._counted_trials(key, provenance):
            counts = replace(counts, frozen=counts.frozen + 1)
            status = self.status(trial.trial_id)
            label = self._labels.get(trial.trial_id)
            if status == TrialStatus.PENDING:
                counts = replace(counts, pending=counts.pending + 1)
            elif label == TrialLabel.POSITIVE:
                counts = replace(counts, positive=counts.positive + 1)
            elif label == TrialLabel.NEGATIVE:
                counts = replace(counts, negative=counts.negative + 1)
            else:
                counts = replace(counts, inconclusive=counts.inconclusive + 1)
        return counts

    def _counted_trials(self, key: PartitionKey, provenance: Provenance) -> list[ShadowTrial]:
        floor = self._epoch_floor.get(key, -1)
        return [
            t for t in self._trials.values()
            if t.partition == key and t.provenance == provenance and t.frozen_sequence > floor
        ]

    def snapshot(
        self,
        key: PartitionKey,
        collected_from: datetime,
        version: int = 1,
    ) -> EvidenceSnapshot | None:
        """
        intent: Express admissible evidence in the shape the existing controller reads
        method: successes are positives; failures are negatives plus inconclusive, because
                an unlabeled or undecidable trial must not look better than a failure
        effect: Returns None while no counting trial exists, so an unknown partition stays
                unknown rather than becoming an empty-but-present record
        """
        parts = [self.counts(key, p) for p in COUNTING_PROVENANCE]
        frozen = sum(c.frozen for c in parts)
        if frozen == 0:
            return None
        successes = sum(c.positive for c in parts)
        failures = sum(c.negative + c.inconclusive for c in parts)
        # constraint: pending trials are excluded from n until labeled or expired; they stay
        # visible through counts()/coverage. publish() expires overdue ones first, so
        # withholding a label cannot keep a bad trial out of the bound.
        return EvidenceSnapshot(
            evidence_id=f"shadow-{digest(_partition_payload(key))[:16]}",
            version=version,
            key=key,
            successes=successes,
            failures=failures,
            collected_from=collected_from,
            collected_until=self._evidence_time(key),
        )

    def _evidence_time(self, key: PartitionKey) -> datetime:
        """Latest label or censor time among counting trials, so re-publishing never freshens."""
        times = [
            self._resolved_at.get(t.trial_id, t.frozen_at)
            for p in COUNTING_PROVENANCE
            for t in self._counted_trials(key, p)
        ]
        return max(times)

    def publish(self, store: EvidenceStore, key: PartitionKey, collected_from: datetime) -> bool:
        """
        intent: Hand the controller the current counting evidence for one partition
        constraint: Replaces the partition's snapshot with the log-derived counts at the next
                    version. The log is the source of truth in v2 flows; mixing it with
                    a hand-seeded or v1-adjudicated snapshot would pool evidence.
        """
        self.expire_unlabeled()
        current = store.get(key)
        snapshot = self.snapshot(key, collected_from, current.version + 1 if current else 1)
        if snapshot is None:
            return False
        store.put(snapshot)
        return True

    @classmethod
    def replay(
        cls,
        ledger: EventLedger,
        label_deadline: timedelta = timedelta(days=1),
        clock: Callable[[], datetime] = utcnow,
    ) -> ShadowEvidenceLog:
        """
        intent: Rebuild the log from the ledger alone, to show counts are reproducible
        constraint: Refuses a ledger whose chain does not verify. Quarantine and review
                    events are skipped because they never changed evidence.
        """
        if not ledger.verify():
            raise ValueError("ledger chain does not verify")
        log = cls(EventLedger(), label_deadline, clock)
        for event in ledger.events:
            payload = event.payload
            if event.event_type == "shadow.frozen":
                key = PartitionKey(**payload["partition"])
                log._trials[event.aggregate_id] = ShadowTrial(
                    event.aggregate_id,
                    Provenance(payload["provenance"]),
                    key,
                    payload["proposal_digest"],
                    datetime.fromisoformat(payload["frozen_at"]),
                    event.sequence,
                )
            elif event.event_type == "shadow.labeled":
                log._labels[event.aggregate_id] = TrialLabel(payload["label"])
                log._resolved_at[event.aggregate_id] = datetime.fromisoformat(payload["labeled_at"])
            elif event.event_type == "shadow.censored":
                log._censored.add(event.aggregate_id)
                log._resolved_at[event.aggregate_id] = event.occurred_at
            elif event.event_type == "shadow.epoch_opened":
                log._epoch_floor[PartitionKey(**payload["partition"])] = event.sequence
        return log

    def _censor(self, trial_id: str, reason: Reason) -> None:
        event = self.ledger.append(
            "shadow.censored", trial_id, "controller", {"reason": reason.value}, self.clock()
        )
        self._censored.add(trial_id)
        self._resolved_at[trial_id] = event.occurred_at

    def _quarantine(self, trial_id: str, reason: Reason) -> Result:
        self.ledger.append("shadow.quarantined", trial_id, "controller", {"reason": reason.value})
        return Result(False, reason)
