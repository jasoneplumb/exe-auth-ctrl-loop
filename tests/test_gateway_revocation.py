"""
Step 05: tokens die with their justification, and a token is spent at most once.

Every test is offline and deterministic. The concurrency tests use barriers and events,
not sleeps, so they either demonstrate the property or fail; they never flap.
"""

import random
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from exe_auth_ctrl_loop import (
    AuthorityController,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    LifecycleManager,
    Oracle,
    OracleKind,
    Outcome,
    OutcomeStatus,
    PartitionKey,
    Policy,
    Proposal,
    Provenance,
    Route,
    ShadowEvidenceLog,
    TrialLabel,
)

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Handler:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, proposal):
        with self.lock:
            self.calls += 1
        return "done"


class GatewayBase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.key = PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
        )
        self.policy = Policy(
            "pol1", {"low": .90}, 30, timedelta(days=30), 0.0,
            prohibited_effects=frozenset({"account:delete"}),
        )
        self.store = EvidenceStore()
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(1), self.clock
        )
        self.gateway = ExecutionGateway(
            self.clock, evidence=self.store, current_policy=lambda: self.policy
        )
        self.handler = Handler()

    def proposal(self, **kw):
        base = Proposal(
            "p-1", "refund order", "create_refund", {"order_id": 7, "usd": 24},
            .96, frozenset({"refund:write"}), self.key, "openai",
        )
        return replace(base, **kw) if kw else base

    def seed(self, successes=99, failures=1):
        self.store.put(EvidenceSnapshot(
            "e1", 1, self.key, successes, failures, T0 - timedelta(days=2), T0,
        ))

    def autonomous_token(self):
        self.seed()
        decision = self.controller.evaluate(self.proposal())
        self.assertEqual(decision.route, Route.AUTONOMOUS)
        return self.gateway.issue(decision, self.proposal(), self.policy)

    def human_token(self):
        decision = self.controller.evaluate(self.proposal())
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        return self.gateway.issue(decision, self.proposal(), self.policy, human_approved=True)

    def assert_denied(self, token, fragment, proposal=None):
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, proposal or self.proposal(), self.handler)
        self.assertIn(fragment, str(ctx.exception))
        self.assertEqual(self.handler.calls, 0)
        self.assertFalse(token.used)


class LiveStateInvalidationTests(GatewayBase):
    def test_issue_then_suspend_denies_within_ttl(self):
        token = self.autonomous_token()
        severe = Outcome("p-0", OutcomeStatus.UNACCEPTABLE, True, "reviewer", T0, "d")
        self.store.adjudicate(self.key, severe, autonomous=True)
        self.clock.now = T0 + timedelta(seconds=1)  # well inside the 2-minute TTL
        self.assert_denied(token, "PARTITION_SUSPENDED")

    def test_issue_then_policy_change_denies(self):
        token = self.autonomous_token()
        self.policy = replace(self.policy, version="pol2")
        self.assert_denied(token, "POLICY_CHANGED")

    def test_issue_then_declared_invalidation_denies(self):
        token = self.autonomous_token()
        self.store.invalidate(self.key, "oracle_recalled")
        self.assert_denied(token, "EVIDENCE_INVALID")

    def test_issue_then_evidence_record_replaced_denies(self):
        token = self.autonomous_token()
        self.store.put(EvidenceSnapshot("e2", 2, self.key, 500, 0, T0, T0))
        self.assert_denied(token, "EVIDENCE_WITHDRAWN")

    def test_ordinary_evidence_increment_does_not_invalidate(self):
        token = self.autonomous_token()
        ok = Outcome("p-0", OutcomeStatus.ACCEPTABLE, False, "oracle", T0, "d")
        self.store.adjudicate(self.key, ok, autonomous=True)
        self.assertEqual(self.store.get(self.key).version, 2)
        receipt = self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertEqual(receipt, "done")
        self.assertEqual(self.handler.calls, 1)

    def test_newly_prohibited_effect_denies_even_with_same_policy_version(self):
        # A policy object swap that keeps the version but widens prohibitions is a
        # configuration error; the gateway still refuses the effect.
        token = self.autonomous_token()
        self.policy = replace(self.policy, prohibited_effects=frozenset({"refund:write"}))
        self.assert_denied(token, "PROHIBITED_EFFECT")

    def test_denial_names_every_reason(self):
        token = self.autonomous_token()
        self.store.invalidate(self.key, "x")
        self.policy = replace(self.policy, version="pol2")
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertIn("POLICY_CHANGED", str(ctx.exception))
        self.assertIn("EVIDENCE_INVALID", str(ctx.exception))

    def test_suspended_and_invalid_names_both_reasons(self):
        token = self.autonomous_token()
        severe = Outcome("p-0", OutcomeStatus.UNACCEPTABLE, True, "reviewer", T0, "d")
        self.store.adjudicate(self.key, severe, autonomous=True)
        self.store.invalidate(self.key, "oracle_recalled")
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertIn("PARTITION_SUSPENDED", str(ctx.exception))
        self.assertIn("EVIDENCE_INVALID", str(ctx.exception))

    def test_gateway_without_live_state_keeps_v1_semantics(self):
        self.gateway = ExecutionGateway(self.clock)
        token = self.autonomous_token()
        self.store.invalidate(self.key, "x")
        self.policy = replace(self.policy, version="pol2")
        receipt = self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertEqual(receipt, "done")


class HumanApprovalTests(GatewayBase):
    def test_human_override_cannot_issue_for_prohibited_effect(self):
        proposal = self.proposal(requested_effects=frozenset({"account:delete"}))
        decision = self.controller.evaluate(proposal)
        self.assertEqual(decision.route, Route.DENY)
        with self.assertRaises(PermissionError):
            self.gateway.issue(decision, proposal, self.policy, human_approved=True)
        # a forged non-DENY decision for the same proposal is refused on the effect alone
        forged = replace(decision, route=Route.HUMAN_APPROVAL)
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.issue(forged, proposal, self.policy, human_approved=True)
        self.assertIn("prohibited", str(ctx.exception))

    def test_human_token_dies_with_policy_change(self):
        token = self.human_token()
        self.policy = replace(self.policy, version="pol2")
        self.assert_denied(token, "POLICY_CHANGED")

    def test_revoked_human_grant_is_refused(self):
        token = self.human_token()
        self.gateway.revoke(token.token_id, "approver rescinded")
        self.assertEqual(token.revocation_reason, "approver rescinded")
        with self.assertRaises(PermissionError):
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertEqual(self.handler.calls, 0)

    def test_human_approval_before_suspension_is_refused(self):
        self.seed(10, 0)  # immature: routes to a human
        token = self.human_token()
        severe = Outcome("p-0", OutcomeStatus.UNACCEPTABLE, True, "reviewer", T0, "d")
        self.store.adjudicate(self.key, severe, autonomous=False)
        self.assert_denied(token, "PARTITION_SUSPENDED")

    def test_human_approval_given_after_suspension_is_honored(self):
        self.seed(10, 0)
        severe = Outcome("p-0", OutcomeStatus.UNACCEPTABLE, True, "reviewer", T0, "d")
        self.store.adjudicate(self.key, severe, autonomous=False)
        decision = self.controller.evaluate(self.proposal())
        self.assertIn("PARTITION_SUSPENDED", decision.reason_codes)
        token = self.gateway.issue(decision, self.proposal(), self.policy, human_approved=True)
        receipt = self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertEqual(receipt, "done")

    def test_issue_refuses_a_stale_policy_object(self):
        self.seed()
        decision = self.controller.evaluate(self.proposal())
        with self.assertRaises(PermissionError):
            self.gateway.issue(decision, self.proposal(), replace(self.policy, version="old"))


class ScopeAndExpiryTests(GatewayBase):
    def test_digest_tool_and_effect_changes_fail_closed(self):
        token = self.autonomous_token()
        self.assert_denied(token, "proposal changed", self.proposal(parameters={"usd": 9000}))
        self.assert_denied(token, "proposal changed", self.proposal(tool_name="delete_order"))
        wider = self.proposal(requested_effects=frozenset({"refund:write", "ledger:write"}))
        self.assert_denied(token, "proposal changed", wider)

    def test_effect_check_is_independent_of_digest(self):
        # Same digest cannot carry different effects, so exercise the scope check directly
        # by issuing for a narrower proposal and forging a token with wider effects.
        token = self.autonomous_token()
        token.allowed_effects = frozenset()
        self.assert_denied(token, "effect exceeds")

    def test_expiry_boundary(self):
        token = self.autonomous_token()
        self.clock.now = token.expires_at - timedelta(microseconds=1)
        self.assertEqual(self.gateway.redemption_blockers(token, self.proposal()), ())
        self.clock.now = token.expires_at
        self.assert_denied(token, "expired")


class ConcurrencyTests(GatewayBase):
    def test_simultaneous_redemptions_invoke_handler_once(self):
        token = self.autonomous_token()
        threads = 8
        barrier = threading.Barrier(threads)
        outcomes: list[str] = []
        lock = threading.Lock()

        def redeem():
            barrier.wait()
            try:
                self.gateway.execute(token.token_id, self.proposal(), self.handler)
                result = "ok"
            except PermissionError:
                result = "denied"
            with lock:
                outcomes.append(result)

        workers = [threading.Thread(target=redeem) for _ in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.assertEqual(self.handler.calls, 1)
        self.assertEqual(sorted(outcomes), ["denied"] * (threads - 1) + ["ok"])

    def test_token_is_consumed_before_the_handler_runs(self):
        token = self.autonomous_token()
        entered = threading.Event()
        release = threading.Event()

        def slow_handler(proposal):
            entered.set()
            release.wait(timeout=5)
            return "slow"

        first = threading.Thread(
            target=lambda: self.gateway.execute(token.token_id, self.proposal(), slow_handler)
        )
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        # The first redemption is inside its handler; a second one must already be refused
        with self.assertRaises(PermissionError):
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        release.set()
        first.join()
        self.assertEqual(self.handler.calls, 0)

    def test_revoke_and_redeem_cannot_both_win(self):
        token = self.autonomous_token()
        barrier = threading.Barrier(2)
        outcome: list[str] = []

        def redeem():
            barrier.wait()
            try:
                self.gateway.execute(token.token_id, self.proposal(), self.handler)
                outcome.append("ok")
            except PermissionError:
                outcome.append("denied")

        def revoke():
            barrier.wait()
            self.gateway.revoke(token.token_id)

        threads = [threading.Thread(target=redeem), threading.Thread(target=revoke)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # exactly one of: handler ran (then revoke landed after), or denied (revoke first)
        self.assertEqual(self.handler.calls, 1 if outcome == ["ok"] else 0)
        self.assertTrue(token.revoked)

    def test_handler_failure_does_not_refund_the_token(self):
        token = self.autonomous_token()

        def failing(proposal):
            raise RuntimeError("downstream failed")

        with self.assertRaises(RuntimeError):
            self.gateway.execute(token.token_id, self.proposal(), failing)
        self.assertTrue(token.used)
        with self.assertRaises(PermissionError):
            self.gateway.execute(token.token_id, self.proposal(), self.handler)


class LifecycleGuardTests(unittest.TestCase):
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
        self.gateway = ExecutionGateway(
            self.clock, evidence=self.store, current_policy=lambda: self.manager.policy,
            guards=(self.manager,),
        )
        self.key = PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
        )
        self.handler = Handler()
        self.counter = 0

    def proposal(self, key=None):
        return Proposal(
            "p-0", "refund order", "create_refund", {"order_id": 7, "usd": 24},
            .96, frozenset({"refund:write"}), key or self.key, "openai",
        )

    def add(self, count, label=TrialLabel.POSITIVE, key=None):
        key = key or self.key
        for _ in range(count):
            self.counter += 1
            p = replace(self.proposal(key), proposal_id=f"p-{self.counter}")
            tid = f"t-{self.counter}"
            self.shadow.freeze(tid, p, Provenance.SHADOW)
            self.shadow.adjudicate(tid, key, p.proposal_digest, label, ORACLE, self.clock.now)

    def autonomous_token(self):
        self.add(60)
        decision = self.manager.evaluate(self.proposal())
        self.assertEqual(decision.route, Route.AUTONOMOUS)
        return self.gateway.issue(decision, self.proposal(), self.policy)

    def test_lifecycle_suspension_denies_outstanding_token(self):
        token = self.autonomous_token()
        self.manager.record_severe(self.key, Provenance.AUDIT, "bad")
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertIn("PARTITION_SUSPENDED", str(ctx.exception))
        self.assertIn("LIFECYCLE_SUSPENDED", str(ctx.exception))
        self.assertEqual(self.handler.calls, 0)

    def test_gate_lost_before_redemption_denies(self):
        token = self.autonomous_token()
        self.add(10, TrialLabel.NEGATIVE)
        self.manager.evaluate(self.proposal())  # next decision notices the bound fell
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertIn("LIFECYCLE_QUALIFYING", str(ctx.exception))

    def test_ordinary_increment_without_reevaluation_keeps_token_good(self):
        token = self.autonomous_token()
        self.add(1)
        receipt = self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertEqual(receipt, "done")

    def test_config_churn_denies_old_partition_token(self):
        token = self.autonomous_token()
        new = replace(self.key, tool_version="refund-api-v3")
        self.manager.on_churn(self.key, new)
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertIn("PARTITION_RETIRED", str(ctx.exception))

    def test_policy_change_via_manager_denies(self):
        token = self.autonomous_token()
        self.manager.policy = replace(self.policy, version="pol2")
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, self.proposal(), self.handler)
        self.assertIn("POLICY_CHANGED", str(ctx.exception))

    def test_human_token_survives_qualifying_but_not_suspension(self):
        decision = self.manager.evaluate(self.proposal())
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        token = self.gateway.issue(decision, self.proposal(), self.policy, human_approved=True)
        self.assertEqual(self.gateway.redemption_blockers(token, self.proposal()), ())
        self.manager.record_severe(self.key, Provenance.HUMAN_APPROVED, "bad")
        with self.assertRaises(PermissionError):
            self.gateway.execute(token.token_id, self.proposal(), self.handler)


if __name__ == "__main__":
    unittest.main()
