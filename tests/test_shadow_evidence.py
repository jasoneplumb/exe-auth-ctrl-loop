import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from exe_auth_ctrl_loop import (
    AuthorityController,
    EventLedger,
    EvidenceStore,
    Oracle,
    OracleKind,
    PartitionKey,
    Policy,
    Proposal,
    Provenance,
    Reason,
    ReviewAction,
    Route,
    ShadowEvidenceLog,
    TrialLabel,
    TrialStatus,
)

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class ShadowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ledger = EventLedger()
        self.log = ShadowEvidenceLog(self.ledger, timedelta(hours=1), self.clock)
        self.key = PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
        )

    def proposal(self, n, key=None, usd=24):
        return Proposal(
            f"p-{n}", "refund order", "create_refund", {"order_id": n, "usd": usd},
            .96, frozenset({"refund:write"}), key or self.key, "openai",
        )

    def trial(self, n, label=TrialLabel.POSITIVE, key=None):
        proposal = self.proposal(n, key)
        self.assertTrue(self.log.freeze(f"t-{n}", proposal).accepted)
        return self.log.adjudicate(
            f"t-{n}", proposal.partition, proposal.proposal_digest, label, ORACLE,
            self.clock.now + timedelta(minutes=1),
        )

    def counts(self, provenance=Provenance.SHADOW):
        return self.log.counts(self.key, provenance)

    def test_accumulates_from_zero_with_successes_and_failures(self):
        self.assertIsNone(self.log.snapshot(self.key, T0))
        for n in range(5):
            self.assertTrue(self.trial(n).accepted)
        self.assertTrue(self.trial(5, TrialLabel.NEGATIVE).accepted)
        counts = self.counts()
        self.assertEqual((counts.frozen, counts.positive, counts.negative), (6, 5, 1))
        snapshot = self.log.snapshot(self.key, T0)
        self.assertEqual((snapshot.successes, snapshot.failures), (5, 1))

    def test_empty_store_qualifies_from_shadow_evidence_alone(self):
        policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        store = EvidenceStore()
        controller = AuthorityController(store, policy, random.Random(1), self.clock)
        proposal = self.proposal(0)
        self.assertEqual(controller.evaluate(proposal).route, Route.HUMAN_APPROVAL)
        for n in range(40):
            self.trial(n)
        self.assertTrue(self.log.publish(store, self.key, T0))
        self.assertEqual(controller.evaluate(proposal).route, Route.AUTONOMOUS)

    def test_human_rejection_cannot_discard_a_negative_trial(self):
        proposals = [self.proposal(n) for n in range(3)]
        for n, p in enumerate(proposals):
            self.log.freeze(f"t-{n}", p)
        # cherry-picking attempt: reviewer rejects the candidate the oracle will fail
        self.assertTrue(
            self.log.record_human_review("t-2", "rev", ReviewAction.REJECT).accepted
        )
        for n, p in enumerate(proposals):
            label = TrialLabel.NEGATIVE if n == 2 else TrialLabel.POSITIVE
            self.log.adjudicate(
                f"t-{n}", self.key, p.proposal_digest, label, ORACLE,
                self.clock.now + timedelta(minutes=1),
            )
        counts = self.counts()
        self.assertEqual((counts.frozen, counts.positive, counts.negative), (3, 2, 1))

    def test_human_rewrite_cannot_inflate_a_label(self):
        bad = self.proposal(1, usd=9000)
        good = self.proposal(1, usd=24)
        self.log.freeze("t-1", bad)
        self.log.record_human_review("t-1", "rev", ReviewAction.REWRITE, good)
        result = self.log.adjudicate(
            "t-1", self.key, good.proposal_digest, TrialLabel.POSITIVE, ORACLE,
            self.clock.now + timedelta(minutes=1),
        )
        self.assertEqual(result.reason, Reason.DIGEST_MISMATCH)
        self.assertEqual(self.log.trial("t-1").proposal_digest, bad.proposal_digest)
        self.assertEqual(self.counts().positive, 0)
        self.assertEqual(self.log.status("t-1"), TrialStatus.PENDING)

    def test_review_never_creates_or_counts_evidence(self):
        self.log.freeze("t-1", self.proposal(1))
        before = self.counts()
        for action in ReviewAction:
            self.log.record_human_review("t-1", "rev", action, self.proposal(2))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.counts(Provenance.HUMAN_APPROVED).frozen, 0)

    def test_human_approved_provenance_is_recorded_but_never_counted(self):
        proposal = self.proposal(1)
        self.log.freeze("h-1", proposal, Provenance.HUMAN_APPROVED)
        self.log.adjudicate(
            "h-1", self.key, proposal.proposal_digest, TrialLabel.POSITIVE, ORACLE,
            self.clock.now + timedelta(minutes=1),
        )
        self.assertEqual(self.counts(Provenance.HUMAN_APPROVED).positive, 1)
        self.assertIsNone(self.log.snapshot(self.key, T0))

    def test_streams_are_counted_separately(self):
        for n, prov in enumerate((Provenance.SHADOW, Provenance.AUDIT, Provenance.AUTONOMOUS)):
            p = self.proposal(n)
            self.log.freeze(f"t-{n}", p, prov)
            self.log.adjudicate(
                f"t-{n}", self.key, p.proposal_digest, TrialLabel.POSITIVE, ORACLE,
                self.clock.now,
            )
        for prov in (Provenance.SHADOW, Provenance.AUDIT, Provenance.AUTONOMOUS):
            self.assertEqual(self.counts(prov).positive, 1)
        self.assertEqual(self.log.snapshot(self.key, T0).successes, 3)

    def test_duplicate_trial_id_is_quarantined(self):
        self.assertTrue(self.log.freeze("t-1", self.proposal(1)).accepted)
        result = self.log.freeze("t-1", self.proposal(2))
        self.assertEqual(result.reason, Reason.DUPLICATE_TRIAL)
        self.assertEqual(self.counts().frozen, 1)
        self.assertEqual(self.log.trial("t-1").proposal_digest, self.proposal(1).proposal_digest)

    def test_wrong_partition_label_is_rejected(self):
        other = replace(self.key, tool_version="refund-api-v3")
        proposal = self.proposal(1)
        self.log.freeze("t-1", proposal)
        result = self.log.adjudicate(
            "t-1", other, proposal.proposal_digest, TrialLabel.POSITIVE, ORACLE, self.clock.now,
        )
        self.assertEqual(result.reason, Reason.WRONG_PARTITION)
        self.assertEqual(self.counts().positive, 0)
        self.assertEqual(self.log.counts(other, Provenance.SHADOW).frozen, 0)

    def test_evidence_does_not_pool_across_partitions(self):
        other = replace(self.key, policy_version="pol2")
        self.trial(1)
        self.trial(2, key=other)
        self.assertEqual(self.counts().frozen, 1)
        self.assertEqual(self.log.counts(other, Provenance.SHADOW).frozen, 1)

    def test_replayed_label_is_rejected(self):
        proposal = self.proposal(1)
        self.assertTrue(self.trial(1).accepted)
        result = self.log.adjudicate(
            "t-1", self.key, proposal.proposal_digest, TrialLabel.NEGATIVE, ORACLE, self.clock.now,
        )
        self.assertEqual(result.reason, Reason.ALREADY_ADJUDICATED)
        self.assertEqual((self.counts().positive, self.counts().negative), (1, 0))

    def test_label_before_freeze_is_rejected(self):
        proposal = self.proposal(1)
        self.log.freeze("t-1", proposal)
        result = self.log.adjudicate(
            "t-1", self.key, proposal.proposal_digest, TrialLabel.POSITIVE, ORACLE,
            T0 - timedelta(minutes=1),
        )
        self.assertEqual(result.reason, Reason.LABEL_BEFORE_FREEZE)

    def test_late_label_is_quarantined_and_censors_the_trial(self):
        proposal = self.proposal(1)
        self.log.freeze("t-1", proposal)
        result = self.log.adjudicate(
            "t-1", self.key, proposal.proposal_digest, TrialLabel.POSITIVE, ORACLE,
            T0 + timedelta(hours=2),
        )
        self.assertEqual(result.reason, Reason.LATE_LABEL)
        self.assertEqual(self.log.status("t-1"), TrialStatus.CENSORED)
        self.assertEqual((self.counts().positive, self.counts().inconclusive), (0, 1))

    def test_inconclusive_is_never_a_success_and_is_observable(self):
        self.trial(1)
        self.trial(2, TrialLabel.INCONCLUSIVE)
        self.log.freeze("t-3", self.proposal(3))
        self.clock.now = T0 + timedelta(hours=2)
        self.assertEqual(self.log.expire_unlabeled(), ["t-3"])
        counts = self.counts()
        self.assertEqual((counts.positive, counts.inconclusive, counts.pending), (1, 2, 0))
        self.assertAlmostEqual(counts.coverage, 1 / 3)
        self.assertAlmostEqual(counts.censored_fraction, 2 / 3)
        snapshot = self.log.snapshot(self.key, T0)
        self.assertEqual((snapshot.successes, snapshot.failures), (1, 2))

    def test_publish_expires_overdue_trials_so_withholding_does_not_help(self):
        self.trial(1)
        self.log.freeze("t-2", self.proposal(2))
        self.clock.now = T0 + timedelta(hours=2)
        store = EvidenceStore()
        self.assertTrue(self.log.publish(store, self.key, T0))
        snapshot = store.get(self.key)
        self.assertEqual((snapshot.successes, snapshot.failures), (1, 1))
        self.assertTrue(self.log.publish(store, self.key, T0))
        self.assertEqual(store.get(self.key).version, 2)

    def test_pre_review_commitment_precedes_review_and_label(self):
        proposal = self.proposal(1)
        self.log.freeze("t-1", proposal)
        self.log.record_human_review("t-1", "rev", ReviewAction.APPROVE)
        self.log.adjudicate(
            "t-1", self.key, proposal.proposal_digest, TrialLabel.POSITIVE, ORACLE,
            self.clock.now + timedelta(minutes=1),
        )
        sequence = {e.event_type: e.sequence for e in self.ledger.events}
        self.assertEqual(self.log.trial("t-1").frozen_sequence, sequence["shadow.frozen"])
        self.assertLess(sequence["shadow.frozen"], sequence["shadow.human_review"])
        self.assertLess(sequence["shadow.frozen"], sequence["shadow.labeled"])

    def test_review_of_unfrozen_trial_is_quarantined(self):
        result = self.log.record_human_review("ghost", "rev", ReviewAction.APPROVE)
        self.assertEqual(result.reason, Reason.UNKNOWN_TRIAL)

    def test_trace_replay_reproduces_counts_and_survives_quarantine_noise(self):
        for n in range(4):
            self.trial(n)
        self.trial(4, TrialLabel.NEGATIVE)
        self.trial(5, TrialLabel.INCONCLUSIVE)
        self.log.freeze("t-6", self.proposal(6))
        self.log.freeze("t-0", self.proposal(99))  # quarantined duplicate
        self.log.record_human_review("t-6", "rev", ReviewAction.REJECT)
        self.clock.now = T0 + timedelta(hours=2)
        self.log.expire_unlabeled()
        replayed = ShadowEvidenceLog.replay(self.ledger, timedelta(hours=1), self.clock)
        for prov in Provenance:
            self.assertEqual(replayed.counts(self.key, prov), self.log.counts(self.key, prov))
        self.assertEqual(replayed.status("t-6"), TrialStatus.CENSORED)

    def test_replay_rejects_tampered_ledger(self):
        self.trial(1)
        self.ledger.events[0] = replace(
            self.ledger.events[0], payload={**self.ledger.events[0].payload, "provenance": "x"}
        )
        with self.assertRaises(ValueError):
            ShadowEvidenceLog.replay(self.ledger)


if __name__ == "__main__":
    unittest.main()
