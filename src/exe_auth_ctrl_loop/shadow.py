"""
Intent: Let a partition earn evidence from zero without letting a human reviewer shape it
Context: v1 counts only autonomous outcomes, but autonomy needs evidence, so an empty store
        can never qualify (docs/v2/baseline.md, gap 1). This module records autonomous-path
        candidates as frozen trials, labels them with an independent oracle, and exposes
        the counts as an EvidenceSnapshot the existing controller already understands.
Pattern: Freeze, then label. The candidate's digest and a ledger record exist before any
        human sees it, and every later record refers to that digest rather than replacing it.
Future: Durable since v3 Step 02, in the ledger's database. Since Step 08 a trial's
        provenance is derived from its committed decision, labels are signed by a
        registered ORACLE principal bound to the trial's epoch, the deadline runs on the
        host's receipt time, and a reviewer cannot also be the labeller (spec §7). Labels
        from an oracle estimate oracle-adjudicated acceptability, not live side-effect
        reliability (docs/v2/spec.md section 5).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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
from .principals import PrincipalKind, Principals
from .store import Store, delta_us, partition_text, time_text, time_us


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
    # v3 Step 08 (spec §7 rules 7, 8)
    NOT_COMMITTED = "NOT_COMMITTED"
    NOT_TRIALABLE = "NOT_TRIALABLE"
    DUPLICATE_PROPOSAL = "DUPLICATE_PROPOSAL"
    ORACLE_NOT_REGISTERED = "ORACLE_NOT_REGISTERED"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    WRONG_ORACLE = "WRONG_ORACLE"
    REVIEWER_IS_ORACLE = "REVIEWER_IS_ORACLE"


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
class SignedLabel:
    """
    intent: One oracle's verdict on one frozen trial, signed so the host can check who gave it
    constraint: The MAC (label_payload) covers the trial, its partition and frozen digest,
                the verdict, the oracle's identity and version, and the oracle's own claimed
                time. The host records claimed_at and never uses it for admission.
    """

    trial_id: str
    partition: PartitionKey
    proposal_digest: str
    label: TrialLabel
    oracle: Oracle
    claimed_at: datetime
    mac: str

    def payload(self) -> dict[str, object]:
        return label_payload(self.trial_id, self.partition, self.proposal_digest, self.label,
                             self.oracle, self.claimed_at)


def label_payload(
    trial_id: str, partition: PartitionKey, proposal_digest: str, label: TrialLabel,
    oracle: Oracle, claimed_at: datetime,
) -> dict[str, object]:
    return {
        "action": "label", "trial_id": trial_id, "partition": asdict(partition),
        "proposal_digest": proposal_digest, "label": TrialLabel(label).value,
        "oracle_id": oracle.oracle_id, "oracle_version": oracle.oracle_version,
        "oracle_kind": OracleKind(oracle.kind).value, "validity_scope": oracle.validity_scope,
        "claimed_at": time_text(claimed_at),
    }


@dataclass(frozen=True)
class Result:
    """accepted=False means the request was quarantined and changed no evidence."""

    accepted: bool
    reason: Reason | None = None
    provenance: Provenance | None = None


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
    method: Every state change is appended to the ledger first; counts derive from trials.
            Each change and its ledger record are one transaction, so neither can exist
            without the other.
    constraint: Human review records are appended but never touch trial state. The only
                ways a trial leaves PENDING are an oracle label for its exact digest or
                the label deadline, so neither approval nor rejection can discard it.
    effect: Rejected requests are quarantined with a reason code and logged, so a replay
            of the ledger reproduces both the counts and what was refused.
    context: Trials, labels, and epoch floors live in the ledger's database (store.py), in
             append-only tables, so a restart keeps every pending trial and its deadline.
    """

    def __init__(
        self,
        ledger: EventLedger,
        label_deadline: timedelta = timedelta(days=1),
        clock: Callable[[], datetime] = utcnow,
        *,
        principals: Principals,
    ) -> None:
        """
        constraint: `principals` verifies oracle signatures (keys stay in its Keyring) and
                    must share the ledger's Store
        """
        if principals.store is not ledger.store:
            raise ValueError("the shadow log and its principals must share one Store")
        self.ledger = ledger
        self.principals = principals
        self.store = ledger.store
        self.label_deadline = label_deadline
        self.clock = clock
        # effect: called with a trial's partition inside the transaction that resolves it
        # (label or censor), so state that depends on the counts moves with them
        # (LifecycleManager registers itself; v3 Step 06, RV-03)
        self.listeners: list[Callable[[PartitionKey], None]] = []

    def open_epoch(self, key: PartitionKey) -> int:
        """
        intent: Start a partition's evidence over, e.g. after a human releases a suspension
        effect: Trials frozen before this ledger event stop counting, so data gathered
                around the incident cannot requalify the partition on the same day
        effect: Returns the new epoch's floor sequence (the opening event's), which the
                lifecycle records in alpha_ledger
        """
        with self.store.transaction() as conn:
            event = self.ledger.append(
                "shadow.epoch_opened", digest(_partition_payload(key))[:16], "controller",
                {"partition": _partition_payload(key)},
            )
            conn.execute(
                "INSERT INTO epochs VALUES (?, ?)", (partition_text(key), event.sequence)
            )
        return event.sequence

    def freeze(self, decision_id: str, proposal: Proposal) -> Result:
        """
        intent: Commit to a candidate before anyone can review it
        method: The trial is the committed decision: its id is the decision id, and its
                provenance is derived from the decision's route and approval record (spec
                §7 rule 7), never passed in. AUDIT and AUTONOMOUS give themselves;
                HUMAN_APPROVAL gives SHADOW; any decision whose token was issued on an
                approval gives HUMAN_APPROVED (recorded, never counted).
        effect: The ledger event is the pre-review commitment; frozen_sequence orders it
                before any human_review or label event for the same trial
        constraint: One counting trial per proposal per epoch: a proposal frozen again (an
                    approved re-run makes a new decision for the same proposal) is refused as
                    DUPLICATE_PROPOSAL, so a proposal is never counted twice
        """
        trial_id = decision_id
        with self.store.transaction() as conn:
            if self.trial(trial_id) is not None:
                return self._quarantine(trial_id, Reason.DUPLICATE_TRIAL)
            committed = conn.execute(
                "SELECT proposal_digest, route FROM decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if committed is None:
                return self._quarantine(trial_id, Reason.NOT_COMMITTED)
            if committed[0] != proposal.proposal_digest:
                return self._quarantine(trial_id, Reason.DIGEST_MISMATCH)
            provenance = self._provenance(conn, decision_id, committed[1])
            if provenance is None:
                return self._quarantine(trial_id, Reason.NOT_TRIALABLE)
            if provenance in COUNTING_PROVENANCE and conn.execute(
                "SELECT 1 FROM trials WHERE partition = ? AND proposal_digest = ?"
                " AND frozen_sequence > ? AND provenance != 'human_approved'",
                (partition_text(proposal.partition), proposal.proposal_digest,
                 self._floor(proposal.partition)),
            ).fetchone():
                return self._quarantine(trial_id, Reason.DUPLICATE_PROPOSAL)
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
            conn.execute(
                "INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trial_id, provenance.value, partition_text(proposal.partition),
                 proposal.proposal_digest, time_text(now), time_us(now), event.sequence),
            )
        return Result(True, provenance=provenance)

    @staticmethod
    def _provenance(conn, decision_id: str, route: str) -> Provenance | None:
        approved = conn.execute(
            "SELECT 1 FROM tokens WHERE decision_id = ? AND approval_id IS NOT NULL",
            (decision_id,),
        ).fetchone()
        if approved:
            return Provenance.HUMAN_APPROVED
        return {"audit": Provenance.AUDIT, "autonomous": Provenance.AUTONOMOUS,
                "human_approval": Provenance.SHADOW}.get(route)

    def bind_oracle(self, key: PartitionKey, oracle: Oracle) -> None:
        """
        intent: Bind the partition's current epoch to one oracle (spec §7 rule 11, O-2)
        constraint: An epoch is bound once. A different oracle needs a new epoch
                    (LifecycleManager.change_oracle); the first admitted label binds an
                    unbound epoch to its own oracle.
        """
        with self.store.transaction():
            floor = self._floor(key)
            bound = self._bound_oracle(key, floor)
            if bound is not None:
                if bound != (oracle.oracle_id, oracle.oracle_version):
                    raise ValueError("this epoch is bound to another oracle; open a new epoch")
                return
            self._bind(key, floor, oracle)

    def adjudicate(self, signed: SignedLabel) -> Result:
        """
        intent: Accept one oracle label for one frozen candidate, once
        method: In order: the trial exists and the label names its partition and frozen
                digest; the labeller is an active ORACLE principal and the MAC verifies
                under its key; the trial is still pending; the labeller did not review this
                trial; the oracle is the one bound to the trial's epoch (the first admitted
                label binds an unbound one); the label arrived (host clock) inside the
                deadline. Any failure is quarantined and ledgered (spec §7 rules 8, 9).
        effect: A late label also censors the trial, so a favourable label cannot be held
                back until it is convenient. The labeller's claimed_at is recorded and never
                used: a backdated claim cannot make a late label admissible.
        """
        trial_id = signed.trial_id
        oracle = signed.oracle
        with self.store.transaction() as conn:
            received = self.clock()
            trial = self.trial(trial_id)
            if trial is None:
                return self._quarantine(trial_id, Reason.UNKNOWN_TRIAL)
            if signed.partition != trial.partition:
                return self._quarantine(trial_id, Reason.WRONG_PARTITION)
            if signed.proposal_digest != trial.proposal_digest:
                return self._quarantine(trial_id, Reason.DIGEST_MISMATCH)
            labeller = self.principals.principal(oracle.oracle_id)
            if (labeller is None or not labeller.active
                    or labeller.kind is not PrincipalKind.ORACLE or labeller.key_id is None):
                return self._quarantine(trial_id, Reason.ORACLE_NOT_REGISTERED)
            if not self.principals.keyring.verify(labeller.key_id, signed.payload(), signed.mac):
                return self._quarantine(trial_id, Reason.BAD_SIGNATURE)
            if self.status(trial_id) != TrialStatus.PENDING:
                return self._quarantine(trial_id, Reason.ALREADY_ADJUDICATED)
            if conn.execute(
                "SELECT 1 FROM ledger WHERE event_type = 'shadow.human_review'"
                " AND aggregate_id = ? AND actor = ?",
                (trial_id, oracle.oracle_id),
            ).fetchone():
                return self._quarantine(trial_id, Reason.REVIEWER_IS_ORACLE)
            floor = self._trial_floor(trial)
            bound = self._bound_oracle(trial.partition, floor)
            if bound is not None and bound != (oracle.oracle_id, oracle.oracle_version):
                return self._quarantine(trial_id, Reason.WRONG_ORACLE)
            if received < trial.frozen_at:
                return self._quarantine(trial_id, Reason.LABEL_BEFORE_FREEZE)
            if received > trial.frozen_at + self.label_deadline:
                self._censor(trial_id, Reason.LATE_LABEL)
                return self._quarantine(trial_id, Reason.LATE_LABEL)
            if bound is None:
                self._bind(trial.partition, floor, oracle)
            self.ledger.append(
                "shadow.labeled",
                trial_id,
                oracle.oracle_id,
                {
                    "label": TrialLabel(signed.label).value,
                    "proposal_digest": signed.proposal_digest,
                    "oracle_id": oracle.oracle_id,
                    "oracle_version": oracle.oracle_version,
                    "oracle_kind": OracleKind(oracle.kind).value,
                    "validity_scope": oracle.validity_scope,
                    "claimed_at": time_text(signed.claimed_at),
                    "received_at": time_text(received),
                    "mac": signed.mac,
                },
                received,
            )
            conn.execute(
                "INSERT INTO labels (trial_id, label, censored, resolved_at, resolved_us,"
                " claimed_at) VALUES (?, ?, 0, ?, ?, ?)",
                (trial_id, TrialLabel(signed.label).value, time_text(received),
                 time_us(received), time_text(signed.claimed_at)),
            )
            self._notify(trial.partition)
        return Result(True)

    def _trial_floor(self, trial: ShadowTrial) -> int:
        """The epoch floor in force when the trial was frozen."""
        row = self.store.connection().execute(
            "SELECT MAX(floor_sequence) FROM epochs WHERE partition = ? AND floor_sequence < ?",
            (partition_text(trial.partition), trial.frozen_sequence),
        ).fetchone()
        return -1 if row[0] is None else int(row[0])

    def _bound_oracle(self, key: PartitionKey, floor: int) -> tuple[str, str] | None:
        row = self.store.connection().execute(
            "SELECT oracle_id, oracle_version FROM epoch_oracles"
            " WHERE partition = ? AND floor_sequence = ?",
            (partition_text(key), floor),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def _bind(self, key: PartitionKey, floor: int, oracle: Oracle) -> None:
        with self.store.transaction() as conn:
            event = self.ledger.append(
                "shadow.oracle_bound", digest(_partition_payload(key))[:16], "controller",
                {"partition": _partition_payload(key), "floor_sequence": floor,
                 "oracle_id": oracle.oracle_id, "oracle_version": oracle.oracle_version,
                 "oracle_kind": OracleKind(oracle.kind).value,
                 "validity_scope": oracle.validity_scope},
            )
            conn.execute(
                "INSERT INTO epoch_oracles VALUES (?, ?, ?, ?, ?, ?, ?)",
                (partition_text(key), floor, oracle.oracle_id, oracle.oracle_version,
                 OracleKind(oracle.kind).value, oracle.validity_scope, event.sequence),
            )

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
        trial = self.trial(trial_id)
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
        constraint: "Past the deadline" is strict, `now > frozen_at + deadline`, compared
                    on integer microseconds so the comparison is by instant
        """
        with self.store.transaction() as conn:
            cutoff = time_us(self.clock()) - delta_us(self.label_deadline)
            expired = [
                row[0] for row in conn.execute(
                    "SELECT t.trial_id FROM trials t LEFT JOIN labels l USING (trial_id)"
                    " WHERE l.trial_id IS NULL AND t.frozen_us < ?"
                    " ORDER BY t.frozen_sequence",
                    (cutoff,),
                )
            ]
            for trial_id in expired:
                self._censor(trial_id, Reason.LABEL_DEADLINE)
        return expired

    def status(self, trial_id: str) -> TrialStatus:
        row = self.store.connection().execute(
            "SELECT censored FROM labels WHERE trial_id = ?", (trial_id,)
        ).fetchone()
        if row is None:
            return TrialStatus.PENDING
        return TrialStatus.CENSORED if row[0] else TrialStatus.LABELED

    def trial(self, trial_id: str) -> ShadowTrial | None:
        row = self.store.connection().execute(
            "SELECT provenance, partition, proposal_digest, frozen_at, frozen_sequence"
            " FROM trials WHERE trial_id = ?",
            (trial_id,),
        ).fetchone()
        if row is None:
            return None
        provenance, partition, proposal_digest, frozen_at, sequence = row
        return ShadowTrial(
            trial_id, Provenance(provenance), PartitionKey(**json.loads(partition)),
            proposal_digest, datetime.fromisoformat(frozen_at), sequence,
        )

    def counts(self, key: PartitionKey, provenance: Provenance) -> Counts:
        return self._tally(key).get(provenance, Counts())

    def _tally(self, key: PartitionKey) -> dict[Provenance, Counts]:
        """
        intent: Every provenance's counts for the current epoch, in one query
        method: A trial with no labels row is pending; a censored row or an INCONCLUSIVE
                label is inconclusive, exactly as v0.3.1 classified them
        """
        rows = self.store.connection().execute(
            "SELECT t.provenance, COUNT(*),"
            " SUM(l.trial_id IS NULL),"
            " SUM(l.label = 'positive'),"
            " SUM(l.label = 'negative'),"
            " SUM(l.censored = 1 OR l.label = 'inconclusive')"
            " FROM trials t LEFT JOIN labels l USING (trial_id)"
            " WHERE t.partition = ? AND t.frozen_sequence > ?"
            " GROUP BY t.provenance",
            (partition_text(key), self._floor(key)),
        ).fetchall()
        return {
            Provenance(p): Counts(frozen, positive or 0, negative or 0, inconclusive or 0,
                                  pending or 0)
            for p, frozen, pending, positive, negative, inconclusive in rows
        }

    def _floor(self, key: PartitionKey) -> int:
        row = self.store.connection().execute(
            "SELECT MAX(floor_sequence) FROM epochs WHERE partition = ?",
            (partition_text(key),),
        ).fetchone()
        return -1 if row[0] is None else int(row[0])

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
        constraint: evidence_id is a digest of the partition key, so every version of a
                    partition's shadow evidence carries the same id. The gateway's
                    EVIDENCE_WITHDRAWN check is therefore blind to shadow republishes by
                    design; what withdraws a token is the snapshot's suspended/valid
                    flags, which retirement, suspension, and declared invalidation set.
        """
        # constraint: the counts and the evidence time are read in one transaction, so a
        # label committed between the two reads cannot make collected_until describe a
        # trial the counts do not include
        with self.store.transaction():
            tally = self._tally(key)
            parts = [tally.get(p, Counts()) for p in COUNTING_PROVENANCE]
            frozen = sum(c.frozen for c in parts)
            if frozen == 0:
                return None
            collected_until = self._evidence_time(key)
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
            collected_until=collected_until,
        )

    def _evidence_time(self, key: PartitionKey) -> datetime:
        """Latest label or censor time among counting trials, so re-publishing never freshens."""
        marks = ",".join("?" for _ in COUNTING_PROVENANCE)
        row = self.store.connection().execute(
            "SELECT COALESCE(l.resolved_at, t.frozen_at)"
            " FROM trials t LEFT JOIN labels l USING (trial_id)"
            f" WHERE t.partition = ? AND t.frozen_sequence > ? AND t.provenance IN ({marks})"
            " ORDER BY COALESCE(l.resolved_us, t.frozen_us) DESC, t.frozen_sequence LIMIT 1",
            (partition_text(key), self._floor(key), *(p.value for p in COUNTING_PROVENANCE)),
        ).fetchone()
        if row is None:
            # constraint: a precondition, not a state: snapshot() returns None before
            # asking when there are no counting trials. Raised, not asserted, so -O
            # cannot turn a wrong call into a TypeError.
            raise LookupError("no counting trials in the current epoch")
        return datetime.fromisoformat(row[0])

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
        into: Store,
        label_deadline: timedelta = timedelta(days=1),
        clock: Callable[[], datetime] = utcnow,
        *,
        principals: Principals,
    ) -> ShadowEvidenceLog:
        """
        intent: Rebuild the log from the ledger alone, to show counts and refusals are
                reproducible
        constraint: Refuses a ledger whose chain does not verify. Trials, labels, censoring,
                    epochs, oracle bindings, and quarantines are rebuilt; review events are
                    skipped because they never changed evidence. `principals` is the rebuilt
                    log's, on `into`.
        constraint: `into` must be a different, empty database: the rebuilt trials keep the
                    source ledger's sequence numbers, and mixing them with another log's
                    rows would pool two histories. The rebuilt log's own ledger is `into`'s.
        """
        if not ledger.verify():
            raise ValueError("ledger chain does not verify")
        log = cls(EventLedger(into), label_deadline, clock, principals=principals)
        with into.transaction() as conn:
            # constraint: checked under the same write lock as the inserts, so two
            # replays into one database cannot both find it empty
            if any(
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in ("trials", "labels", "epochs", "ledger")
            ):
                raise ValueError("replay needs an empty database")
            for event in ledger.events:
                payload = event.payload
                if event.event_type == "shadow.frozen":
                    frozen_at = datetime.fromisoformat(payload["frozen_at"])
                    key = PartitionKey(**payload["partition"])
                    conn.execute(
                        "INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (event.aggregate_id, payload["provenance"], partition_text(key),
                         payload["proposal_digest"], time_text(frozen_at), time_us(frozen_at),
                         event.sequence),
                    )
                elif event.event_type == "shadow.labeled":
                    when = datetime.fromisoformat(payload["received_at"])
                    conn.execute(
                        "INSERT INTO labels (trial_id, label, censored, resolved_at,"
                        " resolved_us, claimed_at) VALUES (?, ?, 0, ?, ?, ?)",
                        (event.aggregate_id, payload["label"], time_text(when), time_us(when),
                         payload["claimed_at"]),
                    )
                elif event.event_type == "shadow.censored":
                    conn.execute(
                        "INSERT INTO labels (trial_id, label, censored, resolved_at,"
                        " resolved_us) VALUES (?, NULL, 1, ?, ?)",
                        (event.aggregate_id, time_text(event.occurred_at),
                         time_us(event.occurred_at)),
                    )
                elif event.event_type == "shadow.epoch_opened":
                    key = PartitionKey(**payload["partition"])
                    conn.execute(
                        "INSERT INTO epochs VALUES (?, ?)", (partition_text(key), event.sequence)
                    )
                elif event.event_type == "shadow.oracle_bound":
                    key = PartitionKey(**payload["partition"])
                    conn.execute(
                        "INSERT INTO epoch_oracles VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (partition_text(key), payload["floor_sequence"], payload["oracle_id"],
                         payload["oracle_version"], payload["oracle_kind"],
                         payload["validity_scope"], event.sequence),
                    )
                elif event.event_type == "shadow.quarantined":
                    conn.execute("INSERT INTO quarantines VALUES (?, ?, ?)",
                                 (event.sequence, event.aggregate_id, payload["reason"]))
        return log

    def _censor(self, trial_id: str, reason: Reason) -> None:
        with self.store.transaction() as conn:
            event = self.ledger.append(
                "shadow.censored", trial_id, "controller", {"reason": reason.value}, self.clock()
            )
            conn.execute(
                "INSERT INTO labels (trial_id, label, censored, resolved_at, resolved_us)"
                " VALUES (?, NULL, 1, ?, ?)",
                (trial_id, time_text(event.occurred_at), time_us(event.occurred_at)),
            )
            trial = self.trial(trial_id)
            if trial is not None:
                self._notify(trial.partition)

    def _notify(self, key: PartitionKey) -> None:
        for listener in self.listeners:
            listener(key)

    def _quarantine(self, trial_id: str, reason: Reason) -> Result:
        """Refuse, and keep the refusal: a ledger event and a quarantines row together."""
        with self.store.transaction() as conn:
            event = self.ledger.append(
                "shadow.quarantined", trial_id, "controller", {"reason": reason.value}
            )
            conn.execute("INSERT INTO quarantines VALUES (?, ?, ?)",
                         (event.sequence, trial_id, reason.value))
        return Result(False, reason)

    def quarantines(self) -> list[tuple[str, str]]:
        """Every refused request as (trial_id, reason), in ledger order."""
        return [(row[0], row[1]) for row in self.store.connection().execute(
            "SELECT trial_id, reason FROM quarantines ORDER BY sequence"
        )]
