"""
Issue #43: the owner's Step 09 decisions as Policy fields, each tested through the gate.
O-3 pessimistic pending, O-4 online α spending by registration order, O-8 lineage spending
across epochs (docs/v3/spec.md §11; docs/v2/statistical-method.md §11).
"""

import math
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from _support import TEST_ORACLE, principals_for, release, scratch_store, shadow_log
from _support import label as support_label
from _support import trial as freeze_trial

from exe_auth_ctrl_loop import (
    AuthorityController,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    LifecycleManager,
    LifecycleState,
    PartitionKey,
    Policy,
    Proposal,
    Provenance,
    Route,
    TrialLabel,
)
from exe_auth_ctrl_loop.sequential import beta_mixture_lower_bound
from exe_auth_ctrl_loop.store import partition_text

T0 = datetime(2026, 11, 3, tzinfo=timezone.utc)
SPENDING = 6 / math.pi**2
R = 0.90


def key(prompt: str = "pp1") -> PartitionKey:
    return PartitionKey("openai", "m1", prompt, "anthropic", "m2", "ep1",
                        "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low")


V031 = {"pending": "deadline", "partition_alpha": "per_partition", "epoch_alpha": "per_epoch"}


def policy(**fields) -> Policy:
    """A policy with the v0.3.1 rules except those named, so each test turns on one rule."""
    return Policy("pol1", {"low": R}, 30, timedelta(days=30), 0.0, **{**V031, **fields})


def perfect_record_needs(alpha: float) -> int:
    """The fewest all-positive trials whose bound reaches R at this α."""
    n = 1
    while beta_mixture_lower_bound(n, 0, alpha) < R:
        n += 1
    return n


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class PolicyTests(unittest.TestCase):
    def test_the_decided_rules_are_the_defaults(self):
        # issue #43 phase 2: a Policy built without naming them gets O-3, O-4, O-8
        default = Policy("pol1", {"low": R}, 30, timedelta(days=30), 0.0)
        self.assertEqual((default.pending, default.partition_alpha, default.epoch_alpha),
                         ("pessimistic", "online", "lineage"))

    def test_the_fields_are_validated(self):
        for field_name in ("pending", "partition_alpha", "epoch_alpha"):
            with self.subTest(field=field_name), self.assertRaises(ValueError):
                policy(**{field_name: "nonsense"})

    def test_allocation_follows_each_rule_and_compounds(self):
        self.assertEqual(policy().allocated_alpha(7, 3), 0.05)  # v0.3.1: full α everywhere
        online = policy(partition_alpha="online")
        self.assertAlmostEqual(online.allocated_alpha(2, 5), 0.05 * SPENDING / 4)
        lineage = policy(epoch_alpha="lineage")
        self.assertAlmostEqual(lineage.allocated_alpha(9, 3), 0.05 * SPENDING / 9)
        both = policy(partition_alpha="online", epoch_alpha="lineage")
        self.assertAlmostEqual(both.allocated_alpha(2, 3), 0.05 * SPENDING**2 / 36)
        total = sum(both.allocated_alpha(k, e) for k in range(1, 400) for e in range(1, 400))
        self.assertLess(total, 0.05)  # the whole family, every epoch, within α
        with self.assertRaises(ValueError):
            both.allocated_alpha(0, 1)

    def test_an_allocated_alpha_can_tighten_the_bound_and_never_loosen_it(self):
        p = policy()
        self.assertEqual(p.lower_bound(60, 0, 0.9), p.lower_bound(60, 0))
        self.assertLess(p.lower_bound(60, 0, 0.001), p.lower_bound(60, 0))


class GateCase(unittest.TestCase):
    """A lifecycle on a fresh store; trials are frozen and labelled through the helpers."""

    def make(self, **fields) -> None:
        self.clock = Clock()
        self.db = scratch_store()
        self.ledger = EventLedger(self.db)
        self.shadow = shadow_log(self.ledger, timedelta(hours=1), self.clock)
        self.store = EvidenceStore(self.db)
        self.policy = policy(**fields)
        self.controller = AuthorityController(self.store, self.policy, random.Random(1),
                                              self.clock)
        self.manager = LifecycleManager(self.shadow, self.store, self.policy, self.controller,
                                        self.clock, principals=principals_for(self.db))
        self.counter = 0

    def proposal(self, partition: PartitionKey) -> Proposal:
        self.counter += 1
        return Proposal(f"p-{self.counter}", "refund order", "create_refund",
                        {"order_id": self.counter, "usd": 24}, .96,
                        frozenset({"refund:write"}), partition, "openai")

    def add(self, partition: PartitionKey, count: int, value=TrialLabel.POSITIVE,
            labelled: bool = True) -> None:
        for _ in range(count):
            p = self.proposal(partition)
            trial_id = f"t-{self.counter}"
            self.assertTrue(freeze_trial(self.shadow, trial_id, p).accepted)
            if labelled:
                result = support_label(self.shadow, trial_id, partition, p.proposal_digest,
                                       value, TEST_ORACLE, self.clock.now)
                self.assertTrue(result.accepted, result.reason)

    def route(self, partition: PartitionKey) -> Route:
        return self.manager.evaluate(self.proposal(partition)).route

    def alpha_rows(self) -> list[tuple]:
        return self.db.connection().execute(
            "SELECT partition, epoch, partition_index, alpha, reason FROM alpha_ledger"
            " ORDER BY entry_id").fetchall()


class PessimisticPendingTests(GateCase):
    """O-3: a pending trial is in n from freezing and a failure until a POSITIVE."""

    def test_pending_trials_count_as_failures_only_under_the_pessimistic_rule(self):
        for pending, expected in (("deadline", (55, 0)), ("pessimistic", (55, 5))):
            with self.subTest(pending=pending):
                self.make(pending=pending)
                k = key()
                self.add(k, 55)
                self.add(k, 5, labelled=False)
                snapshot = self.shadow.snapshot(k, T0, pending=pending)
                self.assertEqual((snapshot.successes, snapshot.failures), expected)

    def test_withheld_labels_cannot_open_the_gate(self):
        # 55 positives qualify on their own; five trials whose labels are held back are
        # outside n under the deadline rule and failures under the pessimistic one
        self.assertGreaterEqual(beta_mixture_lower_bound(55, 0, 0.05), R)
        self.assertLess(beta_mixture_lower_bound(55, 5, 0.05), R)
        for pending, autonomous in (("deadline", True), ("pessimistic", False)):
            with self.subTest(pending=pending):
                self.make(pending=pending)
                k = key()
                self.add(k, 55)
                self.add(k, 5, labelled=False)
                self.route(k)
                self.assertEqual(self.manager.state(k) == LifecycleState.AUTONOMOUS, autonomous)

    def test_a_late_positive_still_counts_once_it_arrives(self):
        self.make(pending="pessimistic")
        k = key()
        self.add(k, 60)
        p = self.proposal(k)
        self.assertTrue(freeze_trial(self.shadow, "late", p).accepted)
        self.assertEqual(self.shadow.snapshot(k, T0, pending="pessimistic").failures, 1)
        support_label(self.shadow, "late", k, p.proposal_digest, TrialLabel.POSITIVE,
                      TEST_ORACLE, self.clock.now)
        snapshot = self.shadow.snapshot(k, T0, pending="pessimistic")
        self.assertEqual((snapshot.successes, snapshot.failures), (61, 0))


class OnlineSpendingTests(GateCase):
    """O-4: the k-th partition registered gets α·6/(π²k²)."""

    def test_registration_order_sets_each_partitions_alpha(self):
        self.make(partition_alpha="online")
        first, second, third = key("a"), key("b"), key("c")
        for k in (first, second, third):
            self.manager.refresh(k)
        rows = self.alpha_rows()
        self.assertEqual([(r[2], r[4]) for r in rows],
                         [(1, "REGISTERED"), (2, "REGISTERED"), (3, "REGISTERED")])
        for index, row in enumerate(rows, start=1):
            self.assertAlmostEqual(row[3], 0.05 * SPENDING / index**2)

    def test_a_later_partition_needs_more_evidence_for_the_same_gate(self):
        self.make(partition_alpha="online")
        first, second = key("a"), key("b")
        self.manager.refresh(first)
        self.manager.refresh(second)
        n = perfect_record_needs(0.05 * SPENDING)
        self.assertLess(beta_mixture_lower_bound(n, 0, 0.05 * SPENDING / 4), R)
        for k in (first, second):
            self.add(k, n)
            self.route(k)
        self.assertEqual(self.manager.state(first), LifecycleState.AUTONOMOUS)
        self.assertEqual(self.manager.state(second), LifecycleState.QUALIFYING)
        # the published snapshot carries the allocation, so a bare controller applies it
        self.assertAlmostEqual(self.store.get(second).alpha, 0.05 * SPENDING / 4)

    def test_registration_order_does_not_restart_with_a_new_policy_version(self):
        # the family is every partition the store has registered; a version change opens
        # no fresh budget
        self.make(partition_alpha="online")
        self.manager.refresh(key("a"))
        self.manager.refresh(replace(key("b"), policy_version="pol2"))
        self.assertEqual([r[2] for r in self.alpha_rows()], [1, 2])


class LineageSpendingTests(GateCase):
    """O-8: epoch e gets 6/(π²e²) of its partition's allocation."""

    def test_a_release_opens_an_epoch_with_a_smaller_alpha(self):
        self.make(epoch_alpha="lineage")
        k = key()
        self.manager.refresh(k)
        self.manager.record_severe(k, Provenance.SHADOW, "SEVERE_HARM")
        release(self.manager, k, "alice", "root cause fixed")
        rows = [(r[1], r[4]) for r in self.alpha_rows()]
        self.assertEqual(rows, [(0, "REGISTERED"), (1, "HUMAN_RELEASE")])
        self.assertAlmostEqual(self.alpha_rows()[-1][3], 0.05 * SPENDING / 4)
        # the same evidence that qualified epoch 1 does not qualify epoch 2
        n = perfect_record_needs(0.05 * SPENDING)
        self.assertLess(beta_mixture_lower_bound(n, 0, 0.05 * SPENDING / 4), R)
        self.add(k, n)
        self.route(k)
        self.assertEqual(self.manager.state(k), LifecycleState.QUALIFYING)
        self.add(k, perfect_record_needs(0.05 * SPENDING / 4) - n)
        self.route(k)
        self.assertEqual(self.manager.state(k), LifecycleState.AUTONOMOUS)


class ReviewFollowUpTests(GateCase):
    """PR #45 review: defaults, the oracle-change path, and databases from before #43."""

    def test_snapshot_and_publish_default_to_the_policys_default_rule(self):
        self.make(pending="pessimistic")
        k = key()
        self.add(k, 3)
        self.add(k, 2, labelled=False)
        snapshot = self.shadow.snapshot(k, T0)  # no pending argument: Policy's default
        self.assertEqual((snapshot.successes, snapshot.failures), (3, 2))
        self.assertTrue(self.shadow.publish(self.store, k, T0))
        self.assertEqual(self.store.get(k).failures, 2)

    def test_an_oracle_change_opens_an_epoch_with_a_smaller_alpha(self):
        from exe_auth_ctrl_loop import Oracle, OracleKind

        self.make(epoch_alpha="lineage")
        k = key()
        self.manager.refresh(k)
        other = Oracle("reviewer", "1", OracleKind.INDEPENDENT_REVIEW, "post-hoc")
        self.manager.change_oracle(k, other, by="alice")
        rows = self.alpha_rows()
        self.assertEqual([(r[1], r[4]) for r in rows], [(0, "REGISTERED"), (1, "ORACLE_CHANGED")])
        self.assertAlmostEqual(rows[-1][3], 0.05 * SPENDING / 4)

    def test_a_partition_from_before_the_upgrade_is_registered_at_its_next_refresh(self):
        self.make(partition_alpha="online")
        k = key()
        self.manager.refresh(k)
        # a schema-v7 database: the partition exists, but alpha_ledger has no row for it
        with self.db.transaction() as conn:
            triggers = [row[0] for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger'"
                " AND tbl_name = 'alpha_ledger'")]
            for name in ("alpha_ledger_no_update", "alpha_ledger_no_delete"):
                conn.execute(f"DROP TRIGGER {name}")
            conn.execute("DELETE FROM alpha_ledger")
            for sql in triggers:
                conn.execute(sql)
        self.manager.refresh(key("b"))  # a partition first seen after the upgrade: k = 1
        self.manager.refresh(k)  # the old one registers now, as k = 2, instead of failing
        self.assertEqual([(r[2], r[4]) for r in self.alpha_rows()],
                         [(1, "REGISTERED"), (2, "REGISTERED")])
        self.add(k, perfect_record_needs(0.05 * SPENDING / 4))
        self.assertEqual(self.route(k), Route.AUTONOMOUS)


class SnapshotAlphaTests(GateCase):
    def test_a_bare_controller_applies_the_snapshots_alpha_only_to_tighten(self):
        self.make()
        k = key()
        enough = perfect_record_needs(0.05)
        # a mature record that a looser α would wrongly qualify
        loose = max(perfect_record_needs(0.9), self.policy.n_min)
        self.assertLess(loose, enough)
        cases = (
            (enough, None, Route.AUTONOMOUS),       # the policy's α
            (enough, 0.001, Route.HUMAN_APPROVAL),  # a smaller allocation tightens
            (loose, None, Route.HUMAN_APPROVAL),
            (loose, 0.9, Route.HUMAN_APPROVAL),     # a larger one does not loosen
        )
        for n, alpha, route in cases:
            with self.subTest(n=n, alpha=alpha):
                current = self.store.get(k)
                self.store.put(EvidenceSnapshot(
                    "ev", current.version + 1 if current else 1, k, n, 0, T0, T0, alpha=alpha))
                self.assertEqual(self.controller.evaluate(self.proposal(k)).route, route)

    def test_the_snapshot_alpha_survives_the_store(self):
        self.make()
        k = key()
        self.store.put(EvidenceSnapshot("ev", 1, k, 3, 1, T0, T0, alpha=0.0123))
        self.assertEqual(self.store.get(k).alpha, 0.0123)
        row = self.db.connection().execute(
            "SELECT alpha FROM evidence_snapshots WHERE partition = ?", (partition_text(k),)
        ).fetchone()
        self.assertEqual(row[0], 0.0123)


if __name__ == "__main__":
    unittest.main()
