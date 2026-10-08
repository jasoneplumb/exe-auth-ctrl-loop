"""
Step 06: host-owned risk envelopes and sequence budgets, and how they interact with the
evidence policy. Offline and deterministic throughout.
"""

import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from exe_auth_ctrl_loop import (
    AuthorityController,
    Bound,
    ClaudeExecutionAgent,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    LifecycleManager,
    OneOf,
    Oracle,
    OracleKind,
    PartitionKey,
    Policy,
    Proposal,
    ProposalBundle,
    ProposalReadiness,
    Provenance,
    RiskEnvelope,
    RiskPolicy,
    Route,
    SequenceBudget,
    ShadowEvidenceLog,
    ToolDefinition,
    ToolRegistry,
    ToolValidationError,
    TrialLabel,
)

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")
PAYEES = frozenset({"acct-100", "acct-200"})


def envelopes(low_max=100.0, high_max=10_000.0):
    return (
        RiskEnvelope(
            "create_refund", "low", 0, frozenset({"refund:write"}),
            (Bound("usd", maximum=low_max, minimum=0), OneOf("payee", PAYEES)),
        ),
        RiskEnvelope(
            "create_refund", "high", 1, frozenset({"refund:write", "ledger:write"}),
            (Bound("usd", maximum=high_max, minimum=0), OneOf("payee", PAYEES)),
        ),
        RiskEnvelope("cancel_order", "low", 0, frozenset({"order:write"})),
    )


def budgets():
    return (
        SequenceBudget("daily-refunds", frozenset({"create_refund"}), "partition", "usd", 250.0),
        SequenceBudget("per-payee", frozenset({"create_refund"}), "parameter:payee", "usd", 150.0),
        SequenceBudget("ops-per-partition", frozenset(), "partition", None, None, 5),
    )


class RiskBase(unittest.TestCase):
    def setUp(self):
        self.clock = lambda: T0
        self.risk = RiskPolicy(
            "risk-v1", envelopes(), budgets(),
            conflicts=frozenset({("create_refund", "cancel_order")}),
        )
        self.policy = Policy("pol1", {"low": .90, "high": .99}, 30, timedelta(days=30), 0.0)
        self.store = EvidenceStore()
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(1), self.clock, guards=(self.risk,)
        )
        self.gateway = ExecutionGateway(
            self.clock, evidence=self.store, current_policy=lambda: self.policy,
            guards=(self.risk,),
        )
        self.calls = 0
        self.counter = 0

    def key(self, risk_class="low", **kw):
        base = PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", risk_class,
        )
        return replace(base, **kw) if kw else base

    def proposal(self, usd=24, payee="acct-100", risk_class="low", tool="create_refund",
                 effects=frozenset({"refund:write"}), confidence=.96, **kw):
        self.counter += 1
        params = {"order_id": self.counter, "usd": usd, "payee": payee}
        if tool == "cancel_order":
            params = {"order_id": self.counter}
            effects = frozenset({"order:write"})
        return Proposal(
            f"p-{self.counter}", "refund order", tool, params, confidence, effects,
            self.key(risk_class, **kw), "openai",
        )

    def seed(self, risk_class="low", successes=99, failures=1):
        key = self.key(risk_class)
        self.store.put(EvidenceSnapshot(
            f"e-{risk_class}", 1, key, successes, failures, T0 - timedelta(days=2), T0,
        ))

    def handler(self, proposal):
        self.calls += 1
        return "done"

    def run_op(self, proposal, human_approved=False):
        decision = self.controller.evaluate(proposal)
        token = self.gateway.issue(decision, proposal, self.policy, human_approved=human_approved)
        return self.gateway.execute(token.token_id, proposal, self.handler)


class HostDerivedRiskTests(RiskBase):
    def test_class_comes_from_arguments_not_labels_or_confidence(self):
        classify = lambda usd, payee="acct-100": self.risk.risk_class(  # noqa: E731
            "create_refund", {"usd": usd, "payee": payee}
        )
        self.assertEqual(classify(100), "low")
        self.assertEqual(classify(100.01), "high")
        self.assertIsNone(classify(10_001))
        self.assertIsNone(classify(50, "acct-999"))
        self.assertIsNone(classify(True))
        self.assertIsNone(self.risk.risk_class("unknown_tool", {}))
        # confidence is not an input to classification at all
        self.seed("low")
        decision = self.controller.evaluate(self.proposal(usd=5000, confidence=.999))
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("RISK_CLASS_MISMATCH", decision.reason_codes)

    def test_mislabeled_large_refund_cannot_borrow_low_risk_evidence(self):
        self.seed("low")
        self.assertEqual(self.controller.evaluate(self.proposal(usd=100)).route, Route.AUTONOMOUS)
        decision = self.controller.evaluate(self.proposal(usd=101, risk_class="low"))
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("RISK_CLASS_MISMATCH", decision.reason_codes)

    def test_correctly_labeled_large_refund_needs_its_own_partition_evidence(self):
        self.seed("low")
        decision = self.controller.evaluate(self.proposal(usd=101, risk_class="high"))
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("NO_EXACT_EVIDENCE", decision.reason_codes)
        self.assertNotIn("RISK_CLASS_MISMATCH", decision.reason_codes)
        self.seed("high", 990, 10)  # 99% is not enough for the 0.99 threshold
        decision = self.controller.evaluate(self.proposal(usd=101, risk_class="high"))
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("BOUND_BELOW_POLICY", decision.reason_codes)

    def test_altered_payee_is_denied(self):
        self.seed("low")
        decision = self.controller.evaluate(self.proposal(payee="acct-attacker"))
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("NO_RISK_ENVELOPE", decision.reason_codes)

    def test_effect_outside_envelope_is_denied(self):
        self.seed("low")
        wider = self.proposal(effects=frozenset({"refund:write", "ledger:write"}))
        decision = self.controller.evaluate(wider)
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("EFFECT_OUTSIDE_ENVELOPE", decision.reason_codes)

    def test_human_approval_cannot_override_a_scope_denial(self):
        self.seed("low")
        decision = self.controller.evaluate(self.proposal(usd=101))
        self.assertEqual(decision.route, Route.DENY)
        with self.assertRaises(PermissionError):
            self.gateway.issue(decision, self.proposal(usd=101), self.policy, human_approved=True)

    def test_boundary_amounts(self):
        self.seed("low")
        self.assertEqual(self.run_op(self.proposal(usd=100)), "done")
        self.assertEqual(self.controller.evaluate(self.proposal(usd=100.01)).route, Route.DENY)
        self.assertEqual(self.controller.evaluate(self.proposal(usd=-1)).route, Route.DENY)


class SequenceBudgetTests(RiskBase):
    def test_individually_legal_operations_cannot_exceed_the_aggregate(self):
        self.seed("low")
        # each op is inside the low envelope (<= 100) and its payee budget (<= 150);
        # together they leave 10 in the partition budget and 50 for acct-200
        for usd, payee in ((100, "acct-100"), (100, "acct-200"), (40, "acct-100")):
            self.assertEqual(self.run_op(self.proposal(usd=usd, payee=payee)), "done")
        self.assertEqual(self.risk.charged("daily-refunds", self.key()), (240.0, 3))
        decision = self.controller.evaluate(self.proposal(usd=20, payee="acct-200"))
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("BUDGET_EXCEEDED:daily-refunds", decision.reason_codes)
        self.assertNotIn("BUDGET_EXCEEDED:per-payee", decision.reason_codes)
        self.assertEqual(self.run_op(self.proposal(usd=10, payee="acct-200")), "done")
        self.assertEqual(self.risk.charged("daily-refunds", self.key()), (250.0, 4))

    def test_per_payee_budget_is_scoped_by_argument(self):
        self.seed("low")
        self.assertEqual(self.run_op(self.proposal(usd=100, payee="acct-100")), "done")
        decision = self.controller.evaluate(self.proposal(usd=60, payee="acct-100"))
        self.assertIn("BUDGET_EXCEEDED:per-payee", decision.reason_codes)
        self.assertEqual(self.run_op(self.proposal(usd=60, payee="acct-200")), "done")

    def test_budget_is_charged_at_redemption_not_decision(self):
        self.seed("low")
        first, second = self.proposal(usd=100), self.proposal(usd=100, payee="acct-200")
        d1, d2 = self.controller.evaluate(first), self.controller.evaluate(second)
        self.assertEqual((d1.route, d2.route), (Route.AUTONOMOUS, Route.AUTONOMOUS))
        t1 = self.gateway.issue(d1, first, self.policy)
        t2 = self.gateway.issue(d2, second, self.policy)
        third = self.proposal(usd=100, payee="acct-100")
        d3 = self.controller.evaluate(third)
        self.assertEqual(d3.route, Route.AUTONOMOUS)  # nothing charged yet
        t3 = self.gateway.issue(d3, third, self.policy)
        self.assertEqual(self.gateway.execute(t1.token_id, first, self.handler), "done")
        self.assertEqual(self.gateway.execute(t2.token_id, second, self.handler), "done")
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(t3.token_id, third, self.handler)
        self.assertIn("BUDGET_EXCEEDED:daily-refunds", str(ctx.exception))
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.risk.charged("daily-refunds", self.key()), (200.0, 2))

    def test_denied_redemption_charges_nothing(self):
        self.seed("low")
        proposal = self.proposal(usd=100)
        decision = self.controller.evaluate(proposal)
        token = self.gateway.issue(decision, proposal, self.policy)
        self.policy = replace(self.policy, version="pol2")  # unrelated withdrawal
        with self.assertRaises(PermissionError):
            self.gateway.execute(token.token_id, proposal, self.handler)
        self.assertEqual(self.risk.charged("daily-refunds", self.key()), (0.0, 0))

    def test_operation_count_limit(self):
        self.seed("low")
        for _ in range(5):
            self.assertEqual(self.run_op(self.proposal(usd=1, payee="acct-100")), "done")
        decision = self.controller.evaluate(self.proposal(usd=1, payee="acct-200"))
        self.assertIn("OPERATION_LIMIT:ops-per-partition", decision.reason_codes)

    def test_conflicting_action_within_partition(self):
        self.seed("low")  # cancel_order shares the partition: same category and class
        self.assertEqual(self.run_op(self.proposal(usd=10)), "done")
        decision = self.controller.evaluate(self.proposal(tool="cancel_order"))
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("CONFLICTING_ACTION:create_refund", decision.reason_codes)

    def test_budgets_do_not_leak_across_partitions(self):
        # partition-scoped budget only; the per-payee budget is global by design
        self.risk.update("risk-v1", envelopes(), (budgets()[0],))
        self.seed("low")
        other = self.key(environment_version="staging")
        self.store.put(EvidenceSnapshot("e-staging", 1, other, 99, 1, T0 - timedelta(days=2), T0))
        for _ in range(2):
            self.run_op(self.proposal(usd=100, payee="acct-100"))
        self.run_op(self.proposal(usd=50, payee="acct-200"))
        self.assertEqual(self.risk.charged("daily-refunds", self.key()), (250.0, 3))
        self.assertEqual(self.controller.evaluate(self.proposal(usd=1)).route, Route.DENY)
        staged = self.proposal(usd=100, payee="acct-200", environment_version="staging")
        self.assertEqual(self.controller.evaluate(staged).route, Route.AUTONOMOUS)
        self.assertEqual(self.risk.charged("daily-refunds", other), (0.0, 0))

    def test_non_numeric_metric_is_denied_before_issue_not_raised_at_commit(self):
        # count-only budget that still names a metric: the metric must be checked up front
        counted = (SequenceBudget("ops", frozenset({"create_refund"}), "partition", "usd",
                                  None, 10),)
        self.risk.update("risk-v1", envelopes(), counted)
        self.seed("low")
        bad = self.proposal(usd="24")  # string, not a number
        decision = self.controller.evaluate(bad)
        self.assertEqual(decision.route, Route.DENY)
        self.assertIn("BUDGET_METRIC_ERROR:ops", decision.reason_codes)
        with self.assertRaises(PermissionError):
            self.gateway.issue(decision, bad, self.policy)
        self.assertEqual(self.risk.charged("ops", self.key()), (0.0, 0))
        # a numeric metric on the same budget is fine and commit() charges the count
        self.assertEqual(self.run_op(self.proposal(usd=24)), "done")
        self.assertEqual(self.risk.charged("ops", self.key()), (24.0, 1))

    def test_budget_validation(self):
        with self.assertRaises(ValueError):
            SequenceBudget("x", frozenset())
        with self.assertRaises(ValueError):
            SequenceBudget("x", frozenset(), "partition", None, 10.0)
        with self.assertRaises(ValueError):
            SequenceBudget("x", frozenset(), "session", "usd", 10.0)


class PolicyChangePropagationTests(RiskBase):
    def test_rollback_to_stricter_envelope_denies_outstanding_token(self):
        self.seed("low")
        self.risk.update("risk-v2", envelopes(low_max=1000.0))
        proposal = self.proposal(usd=500)
        decision = self.controller.evaluate(proposal)
        self.assertEqual(decision.route, Route.AUTONOMOUS)
        token = self.gateway.issue(decision, proposal, self.policy)
        self.risk.update("risk-v1", envelopes())  # rollback
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, proposal, self.handler)
        self.assertIn("RISK_CLASS_MISMATCH", str(ctx.exception))
        self.assertEqual(self.calls, 0)

    def test_tightened_budget_denies_outstanding_token(self):
        self.seed("low")
        proposal = self.proposal(usd=100)
        token = self.gateway.issue(self.controller.evaluate(proposal), proposal, self.policy)
        tighter = (
            SequenceBudget("daily-refunds", frozenset({"create_refund"}), "partition", "usd", 50.0),
        )
        self.risk.update("risk-v3", envelopes(), tighter)
        with self.assertRaises(PermissionError) as ctx:
            self.gateway.execute(token.token_id, proposal, self.handler)
        self.assertIn("BUDGET_EXCEEDED:daily-refunds", str(ctx.exception))

    def test_charges_survive_a_policy_update(self):
        self.seed("low")
        self.run_op(self.proposal(usd=100))
        self.risk.update("risk-v2", envelopes(), budgets())
        self.assertEqual(self.risk.charged("daily-refunds", self.key()), (100.0, 1))


class LifecycleInteractionTests(unittest.TestCase):
    """Risk scope and earned autonomy are separate gates; both must open."""

    def setUp(self):
        self.clock = lambda: T0
        self.ledger = EventLedger()
        self.shadow = ShadowEvidenceLog(self.ledger, timedelta(hours=1), self.clock)
        self.store = EvidenceStore()
        self.policy = Policy("pol1", {"low": .90, "high": .90}, 30, timedelta(days=30), 0.0)
        self.risk = RiskPolicy("risk-v1", envelopes())  # scope only; budgets tested above
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(1), self.clock, guards=(self.risk,)
        )
        self.manager = LifecycleManager(
            self.shadow, self.store, self.policy, self.controller, self.clock
        )
        self.counter = 0

    def key(self, risk_class):
        return PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", risk_class,
        )

    def proposal(self, usd, risk_class):
        self.counter += 1
        return Proposal(
            f"p-{self.counter}", "refund order", "create_refund",
            {"order_id": self.counter, "usd": usd, "payee": "acct-100"},
            .96, frozenset({"refund:write"}), self.key(risk_class), "openai",
        )

    def qualify(self, risk_class, usd):
        for _ in range(60):
            p = self.proposal(usd, risk_class)
            self.shadow.freeze(p.proposal_id, p, Provenance.SHADOW)
            self.shadow.adjudicate(
                p.proposal_id, p.partition, p.proposal_digest, TrialLabel.POSITIVE, ORACLE, T0,
            )

    def test_low_risk_autonomy_does_not_extend_to_the_high_envelope(self):
        self.qualify("low", usd=20)
        self.assertEqual(self.manager.evaluate(self.proposal(20, "low")).route, Route.AUTONOMOUS)
        high = self.manager.evaluate(self.proposal(5000, "high"))
        self.assertEqual(high.route, Route.HUMAN_APPROVAL)
        self.assertIn("LIFECYCLE_UNESTABLISHED", high.reason_codes)
        mislabeled = self.manager.evaluate(self.proposal(5000, "low"))
        self.assertEqual(mislabeled.route, Route.DENY)
        self.assertIn("RISK_CLASS_MISMATCH", mislabeled.reason_codes)

    def test_shadow_trials_outside_the_envelope_do_not_make_the_partition_autonomous_for_them(self):
        # Evidence was earned on small refunds; a large one in the same labelled class is
        # a scope question, answered DENY before evidence is even consulted.
        self.qualify("low", usd=20)
        decision = self.manager.evaluate(self.proposal(100, "low"))
        self.assertEqual(decision.route, Route.AUTONOMOUS)
        decision = self.manager.evaluate(self.proposal(101, "low"))
        self.assertEqual(decision.route, Route.DENY)


class _FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)

    def create(self, **kwargs):
        return next(self.responses)


class _FakeClaude:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _tool_use(name, proposal_id, **parameters):
    return SimpleNamespace(content=[SimpleNamespace(
        type="tool_use", id="toolu-1", name=name,
        input={"proposal_id": proposal_id, **parameters},
    )])


class RegistryUnificationTests(unittest.TestCase):
    """One classifier per registry: the RiskPolicy when attached, the tool's own otherwise."""

    SCHEMA = {
        "type": "object",
        "properties": {
            "order_id": {"type": "integer"}, "usd": {"type": "number"}, "payee": {"type": "string"},
        },
        "required": ["order_id", "usd", "payee"],
        "additionalProperties": False,
    }

    def setUp(self):
        self.risk = RiskPolicy("risk-v1", envelopes())
        self.calls = []

    def tool(self, risk_class):
        return ToolDefinition(
            name="create_refund", description="refund", input_schema=self.SCHEMA,
            effects=frozenset({"refund:write"}), version="refund-api-v2",
            task_category="refund", risk_class=risk_class,
            handler=lambda args: self.calls.append(args) or {"ok": True},
        )

    def test_policy_registry_delegates_classification(self):
        registry = ToolRegistry(risk=self.risk)
        registry.register(self.tool(None))
        low = {"order_id": 1, "usd": 100, "payee": "acct-100"}
        self.assertEqual(registry.classify_risk("create_refund", low), "low")
        self.assertEqual(registry.classify_risk("create_refund", {**low, "usd": 101}), "high")
        with self.assertRaises(ToolValidationError):
            registry.classify_risk("create_refund", {**low, "payee": "acct-999"})

    def test_two_classifiers_are_refused_at_registration(self):
        with self.assertRaises(ValueError):
            ToolRegistry(risk=self.risk).register(self.tool("low"))
        with self.assertRaises(ValueError):
            ToolRegistry().register(self.tool(None))
        with self.assertRaises(ToolValidationError):
            self.tool(None).classify_risk({})

    def test_plain_registry_keeps_the_tool_classifier(self):
        registry = ToolRegistry()
        registry.register(self.tool(lambda p: "high" if p["usd"] > 10 else "low"))
        self.assertEqual(registry.classify_risk("create_refund", {"usd": 5}), "low")
        self.assertEqual(registry.classify_risk("create_refund", {"usd": 50}), "high")

    def agent(self, registry, proposal, store):
        policy = Policy("pol1", {"low": .90, "high": .99}, 30, timedelta(days=30), 0.0)
        controller = AuthorityController(
            store, policy, random.Random(1), lambda: T0, guards=(self.risk,)
        )
        gateway = ExecutionGateway(lambda: T0, evidence=store, guards=(self.risk,))
        client = _FakeClaude([
            _tool_use("create_refund", proposal.proposal_id, **proposal.parameters),
            SimpleNamespace(content=[SimpleNamespace(type="text", text="done")]),
        ])
        return ClaudeExecutionAgent("m2", "ep1", controller, gateway, registry, client)

    def test_executor_refuses_a_proposal_whose_class_the_registry_rejects(self):
        registry = ToolRegistry(risk=self.risk)
        registry.register(self.tool(None))
        key = PartitionKey(
            "openai", "m1", "pp1", "anthropic", "m2", "ep1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
        )
        store = EvidenceStore()
        store.put(EvidenceSnapshot("e", 1, key, 99, 1, T0 - timedelta(days=2), T0))
        mislabeled = Proposal(
            "p-1", "refund", "create_refund", {"order_id": 1, "usd": 500, "payee": "acct-100"},
            .96, frozenset({"refund:write"}), key, "openai",
        )
        bundle = ProposalBundle("b", "refund", ProposalReadiness.EXECUTABLE, (mislabeled,), "s")
        run = self.agent(registry, mislabeled, store).run(bundle)
        self.assertEqual(run.steps[0].error, "risk class does not match registry")
        self.assertEqual(self.calls, [])

        unknown_payee = replace(
            mislabeled, parameters={"order_id": 1, "usd": 5, "payee": "acct-999"},
        )
        bundle = ProposalBundle("b", "refund", ProposalReadiness.EXECUTABLE, (unknown_payee,), "s")
        run = self.agent(registry, unknown_payee, store).run(bundle)
        self.assertIn("no risk envelope admits", run.steps[0].error)
        self.assertEqual(self.calls, [])

        honest = replace(mislabeled, parameters={"order_id": 1, "usd": 50, "payee": "acct-100"})
        bundle = ProposalBundle("b", "refund", ProposalReadiness.EXECUTABLE, (honest,), "s")
        run = self.agent(registry, honest, store).run(bundle)
        self.assertTrue(run.steps[0].executed)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
