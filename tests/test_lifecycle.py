import json
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from exe_auth_ctrl_loop import (
    AuthorityController,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    LifecycleManager,
    LifecycleState,
    Oracle,
    OracleKind,
    PartitionKey,
    Policy,
    Proposal,
    Provenance,
    Route,
    ShadowEvidenceLog,
    TrialLabel,
)
from exe_auth_ctrl_loop.lifecycle import TRANSITIONS, LifecycleEvent

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")
FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "docs" / "v2" / "lifecycle_fixture.json").read_text()
)
S = LifecycleState


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ledger = EventLedger()
        self.shadow = ShadowEvidenceLog(self.ledger, timedelta(hours=1), self.clock)
        self.store = EvidenceStore()
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(1), self.clock
        )
        self.manager = LifecycleManager(
            self.shadow, self.store, self.policy, self.controller, self.clock
        )
        self.key = self.make_key()
        self.counter = 0

    @staticmethod
    def make_key(tool="refund-api-v2", policy="pol1"):
        return PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            tool, policy, "prod1", "refund", "0.95-1.00", "low",
        )

    def proposal(self, key=None):
        return Proposal(
            "p-0", "refund order", "create_refund", {"order_id": 7, "usd": 24},
            .96, frozenset({"refund:write"}), key or self.key, "openai",
        )

    def add(self, count, label=TrialLabel.POSITIVE, key=None, provenance=Provenance.SHADOW):
        key = key or self.key
        for _ in range(count):
            self.counter += 1
            p = replace(self.proposal(key), proposal_id=f"p-{self.counter}")
            tid = f"t-{self.counter}"
            self.assertTrue(self.shadow.freeze(tid, p, provenance).accepted)
            self.assertTrue(self.shadow.adjudicate(
                tid, key, p.proposal_digest, label, ORACLE, self.clock.now,
            ).accepted)

    def decide(self, key=None):
        return self.manager.evaluate(self.proposal(key))

    def autonomous(self, key=None):
        self.add(60, key=key)
        decision = self.decide(key)
        self.assertEqual(decision.route, Route.AUTONOMOUS)
        return decision

    def transitions(self):
        return [
            (e.payload["from"], e.payload["to"], e.payload["event"], tuple(e.payload["reasons"]))
            for e in self.ledger.events
            if e.event_type == "lifecycle.transition" and e.payload["event"]
        ]

    def test_empty_store_to_autonomy_from_shadow_evidence_only(self):
        self.assertIsNone(self.store.get(self.key))
        first = self.decide()
        self.assertEqual((self.manager.state(self.key), first.route),
                         (S.UNESTABLISHED, Route.HUMAN_APPROVAL))
        self.assertIn("NO_EXACT_EVIDENCE", first.reason_codes)

        self.add(29)
        thin = self.decide()
        self.assertEqual((self.manager.state(self.key), thin.route),
                         (S.QUALIFYING, Route.HUMAN_APPROVAL))
        self.assertIn("EVIDENCE_IMMATURE", thin.reason_codes)

        self.add(31)
        decision = self.decide()
        self.assertEqual((self.manager.state(self.key), decision.route),
                         (S.AUTONOMOUS, Route.AUTONOMOUS))
        self.assertEqual(
            [(f, t, e) for f, t, e, _ in self.transitions()],
            [(None, "UNESTABLISHED", "FIRST_PROPOSAL"),
             ("UNESTABLISHED", "QUALIFYING", "TRIAL_ADMITTED"),
             ("QUALIFYING", "AUTONOMOUS", "GATE_MET")],
        )
        gateway = ExecutionGateway(self.clock)
        token = gateway.issue(decision, self.proposal(), self.policy)
        self.assertEqual(gateway.execute(token.token_id, self.proposal(), lambda p: "ok"), "ok")

    def test_transition_records_policy_and_evidence_identity(self):
        self.autonomous()
        record = next(
            e.payload for e in self.ledger.events
            if e.event_type == "lifecycle.transition" and e.payload["event"] == "GATE_MET"
        )
        self.assertEqual(record["policy_version"], "pol1")
        self.assertEqual(record["evidence_version"], self.store.get(self.key).version)
        self.assertTrue(record["evidence_id"])
        self.assertTrue(self.ledger.verify())

    def test_all_human_approved_history_never_earns_autonomy(self):
        self.add(100, provenance=Provenance.HUMAN_APPROVED)
        decision = self.decide()
        self.assertEqual(self.manager.state(self.key), S.UNESTABLISHED)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIsNone(self.store.get(self.key))

    def test_insufficient_or_poor_evidence_stays_below_autonomy(self):
        self.add(29)
        self.assertNotEqual(self.decide().route, Route.AUTONOMOUS)
        self.add(25, TrialLabel.NEGATIVE)
        decision = self.decide()
        self.assertEqual(self.manager.state(self.key), S.QUALIFYING)
        self.assertNotEqual(decision.route, Route.AUTONOMOUS)

    def test_failures_withdraw_autonomy_with_reason(self):
        self.autonomous()
        self.add(10, TrialLabel.NEGATIVE)
        decision = self.decide()
        self.assertEqual(self.manager.state(self.key), S.QUALIFYING)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("BOUND_BELOW_POLICY", decision.reason_codes)
        last = self.transitions()[-1]
        self.assertEqual(last[:3], ("AUTONOMOUS", "QUALIFYING", "GATE_LOST_OUTCOME"))
        self.assertIn("BOUND_BELOW_POLICY", last[3])

    def test_stale_evidence_withdraws_autonomy(self):
        self.autonomous()
        self.clock.now = T0 + timedelta(days=31)
        decision = self.decide()
        self.assertEqual(self.manager.state(self.key), S.QUALIFYING)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("EVIDENCE_STALE", decision.reason_codes)
        self.assertEqual(self.transitions()[-1][2], "EVIDENCE_EXPIRED")

    def test_republishing_does_not_freshen_old_evidence(self):
        self.add(60)
        self.clock.now = T0 + timedelta(days=31)
        self.assertNotEqual(self.decide().route, Route.AUTONOMOUS)
        self.assertEqual(self.store.get(self.key).collected_until, T0)

    def test_severe_failure_suspends_immediately_and_persists(self):
        self.autonomous()
        self.manager.record_severe(self.key, Provenance.HUMAN_APPROVED, "wrong_recipient")
        self.assertEqual(self.manager.state(self.key), S.SUSPENDED)
        self.add(60)
        decision = self.decide()
        self.assertEqual(self.manager.state(self.key), S.SUSPENDED)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("PARTITION_SUSPENDED", decision.reason_codes)
        self.assertTrue(self.store.get(self.key).suspended)
        # the v1 controller on the same store fails closed too
        self.assertEqual(self.controller.evaluate(self.proposal()).route, Route.HUMAN_APPROVAL)
        last = self.transitions()[-1]
        self.assertEqual(last[:3], ("AUTONOMOUS", "SUSPENDED", "SEVERE_FAILURE"))
        self.assertIn("wrong_recipient", last[3])

    def test_severe_failure_at_zero_evidence_suspends(self):
        self.manager.record_severe(self.key, Provenance.AUTONOMOUS, "bad")
        self.assertEqual(self.manager.state(self.key), S.SUSPENDED)
        self.assertTrue(self.store.get(self.key).suspended)
        self.add(60)
        self.assertEqual(self.decide().route, Route.HUMAN_APPROVAL)

    def test_full_suspension_to_requalification_cycle(self):
        self.autonomous()
        self.manager.record_severe(self.key, Provenance.AUDIT, "bad")
        self.add(50)  # accrues during suspension; must not count afterwards
        with self.assertRaises(ValueError):
            self.manager.release(self.key, "", "reviewed")
        with self.assertRaises(ValueError):
            self.manager.release(self.key, "alice", "")
        self.manager.release(self.key, "alice", "root cause fixed")
        decision = self.decide()
        self.assertEqual(self.manager.state(self.key), S.QUALIFYING)
        self.assertEqual(self.manager.record(self.key).epoch, 1)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("EVIDENCE_IMMATURE", decision.reason_codes)
        self.assertEqual(self.store.get(self.key).n, 0)
        self.assertFalse(self.store.get(self.key).suspended)
        self.add(60)
        self.assertEqual(self.decide().route, Route.AUTONOMOUS)
        self.assertEqual(
            [t[:3] for t in self.transitions()][-3:],
            [("AUTONOMOUS", "SUSPENDED", "SEVERE_FAILURE"),
             ("SUSPENDED", "QUALIFYING", "HUMAN_RELEASE"),
             ("QUALIFYING", "AUTONOMOUS", "GATE_MET")],
        )

    def test_declared_invalidation_survives_refresh_and_blocks_the_gate(self):
        self.autonomous()
        self.store.invalidate(self.key, "oracle_recalled")
        decision = self.decide()
        self.assertFalse(self.store.get(self.key).valid)
        self.assertEqual(self.store.get(self.key).invalidation_reason, "oracle_recalled")
        self.assertIn("EVIDENCE_INVALID", decision.reason_codes)
        self.assertNotEqual(decision.route, Route.AUTONOMOUS)
        self.assertEqual(self.manager.state(self.key), S.QUALIFYING)
        self.add(20)  # fresh labels republish counts but must not un-invalidate
        decision = self.decide()
        self.assertFalse(self.store.get(self.key).valid)
        self.assertIn("EVIDENCE_INVALID", decision.reason_codes)
        self.assertNotEqual(decision.route, Route.AUTONOMOUS)

    def test_severe_on_retired_partition_is_logged_not_stuck(self):
        self.autonomous()
        new = self.make_key(tool="refund-api-v3")
        self.manager.on_churn(self.key, new)
        self.manager.record_severe(self.key, Provenance.AUTONOMOUS, "late report")
        record = self.manager.record(self.key)
        self.assertTrue(record.retired)
        self.assertNotEqual(record.state, S.SUSPENDED)
        logged = [
            e.payload for e in self.ledger.events
            if e.event_type == "lifecycle.transition"
            and {"PARTITION_RETIRED", "late report"} <= set(e.payload["reasons"])
        ]
        self.assertEqual(len(logged), 1)
        # the successor is unaffected: no lineage suspension from a post-retirement report
        self.assertFalse(self.manager.record(new).lineage_suspended)

    def test_seeded_record_is_replaced_by_shadow_evidence_even_with_equal_counts(self):
        self.store.put(EvidenceSnapshot("seed", 1, self.key, 1, 0, T0, T0))
        self.add(1)  # one shadow positive labelled at T0: identical counts and time
        self.decide()
        snapshot = self.store.get(self.key)
        self.assertTrue(snapshot.evidence_id.startswith("shadow-"), snapshot.evidence_id)
        self.assertEqual((snapshot.successes, snapshot.failures), (1, 0))

    def test_severe_failure_while_suspended_is_recorded_not_raised(self):
        self.autonomous()
        self.manager.record_severe(self.key, Provenance.AUTONOMOUS, "first")
        self.manager.record_severe(self.key, Provenance.HUMAN_APPROVED, "second")
        self.assertEqual(self.manager.state(self.key), S.SUSPENDED)
        self.assertEqual(self.manager.record(self.key).suspension_reason, "first")
        logged = [
            e.payload for e in self.ledger.events
            if e.event_type == "lifecycle.transition"
            and "ALREADY_SUSPENDED" in e.payload["reasons"]
        ]
        self.assertEqual(len(logged), 1)
        self.assertIn("second", logged[0]["reasons"])
        self.assertEqual(self.transitions()[-1][:3], ("AUTONOMOUS", "SUSPENDED", "SEVERE_FAILURE"))

    def test_release_requires_a_suspended_partition(self):
        self.autonomous()
        with self.assertRaises(ValueError):
            self.manager.release(self.key, "alice", "why")

    def test_suspended_cannot_reach_autonomous_without_release(self):
        self.autonomous()
        self.manager.record_severe(self.key, Provenance.AUTONOMOUS, "bad")
        for _ in range(3):
            self.add(60)
            self.assertEqual(self.decide().route, Route.HUMAN_APPROVAL)
        self.assertEqual(self.manager.state(self.key), S.SUSPENDED)

    def test_policy_change_retires_matching_authority(self):
        self.autonomous()
        new_policy = replace(self.policy, version="pol2")
        self.manager.policy = new_policy
        self.controller.policy = new_policy
        decision = self.decide()
        self.assertNotEqual(decision.route, Route.AUTONOMOUS)
        self.assertIn("POLICY_VERSION_MISMATCH", decision.reason_codes)
        self.assertIn("PARTITION_RETIRED", decision.reason_codes)
        fresh = self.make_key(policy="pol2")
        self.assertNotEqual(self.decide(fresh).route, Route.AUTONOMOUS)
        self.assertEqual(self.manager.state(fresh), S.UNESTABLISHED)
        self.assertIsNone(self.store.get(fresh))

    def test_config_churn_does_not_pool_evidence(self):
        self.autonomous()
        new = self.make_key(tool="refund-api-v3")
        self.manager.on_churn(self.key, new)
        old_decision = self.decide()
        self.assertNotEqual(old_decision.route, Route.AUTONOMOUS)
        self.assertIn("PARTITION_RETIRED", old_decision.reason_codes)
        new_decision = self.decide(new)
        self.assertEqual(self.manager.state(new), S.UNESTABLISHED)
        self.assertNotEqual(new_decision.route, Route.AUTONOMOUS)
        self.assertIsNone(self.store.get(new))

    def test_churn_cannot_launder_a_suspension(self):
        self.autonomous()
        self.manager.record_severe(self.key, Provenance.AUTONOMOUS, "bad")
        new = self.make_key(tool="refund-api-v3")
        self.manager.on_churn(self.key, new)
        self.add(60, key=new)
        decision = self.decide(new)
        self.assertEqual(self.manager.state(new), S.QUALIFYING)
        self.assertNotEqual(decision.route, Route.AUTONOMOUS)
        self.assertIn("LINEAGE_SUSPENDED", decision.reason_codes)
        self.manager.release(new, "alice", "lineage reviewed")
        self.assertNotEqual(self.decide(new).route, Route.AUTONOMOUS)  # pre-release trials excluded
        self.add(60, key=new)
        self.assertEqual(self.decide(new).route, Route.AUTONOMOUS)

    def test_hand_seeded_snapshot_cannot_bypass_the_lifecycle(self):
        self.store.put(EvidenceSnapshot("seed", 1, self.key, 99, 1, T0 - timedelta(days=2), T0))
        self.assertEqual(self.controller.evaluate(self.proposal()).route, Route.AUTONOMOUS)
        decision = self.decide()
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("LIFECYCLE_UNESTABLISHED", decision.reason_codes)
        self.assertNotIn("AUTHORITY_SUFFICIENT", decision.reason_codes)


class TransitionTableTests(unittest.TestCase):
    def test_only_qualifying_gate_met_enters_autonomous(self):
        entries = [k for k, to in TRANSITIONS.items() if to == S.AUTONOMOUS]
        self.assertEqual(entries, [(S.QUALIFYING, LifecycleEvent.GATE_MET)])

    def test_suspended_exits_only_by_human_release_to_qualifying(self):
        exits = {(k, to) for k, to in TRANSITIONS.items() if k[0] == S.SUSPENDED}
        self.assertEqual(exits, {((S.SUSPENDED, LifecycleEvent.HUMAN_RELEASE), S.QUALIFYING)})

    def test_table_agrees_with_the_step_01_fixture(self):
        rows = {
            (t["from"], t["event"]): t["to"]
            for t in FIXTURE["transitions"]
            if not t["blocked"] and t["to"] != t["from"]
        }
        coded = {(k[0].value if k[0] else None, k[1].value): to.value
                 for k, to in TRANSITIONS.items()}
        events = {e for _, e in coded}
        for key, to in coded.items():
            self.assertEqual(rows.get(key), to, key)
        for key, to in rows.items():
            if key[1] in events:
                self.assertEqual(coded.get(key), to, key)


if __name__ == "__main__":
    unittest.main()
