"""
Step 07: the event-order contract (draw, commit, then act), fail-closed commit, and what
the hash chain does and does not detect. Offline; the Claude client is a fake.
"""

import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from exe_auth_ctrl_loop import (
    AuthorityController,
    ClaudeExecutionAgent,
    CrossModelAuthorityLoop,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    ExecutionStatus,
    PartitionKey,
    Policy,
    Proposal,
    ProposalBundle,
    ProposalReadiness,
    Route,
    ToolDefinition,
    ToolRegistry,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


class FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)

    def create(self, **kwargs):
        return next(self.responses)


class FakeClaude:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def tool_use(name, proposal_id, **parameters):
    return SimpleNamespace(content=[SimpleNamespace(
        type="tool_use", id="toolu-1", name=name,
        input={"proposal_id": proposal_id, **parameters},
    )])


def final_text():
    return SimpleNamespace(content=[SimpleNamespace(type="text", text="complete")])


class RefusingLedger(EventLedger):
    """Simulates a ledger whose decision commit fails (disk full, connection lost)."""

    def commit_decision(self, decision, proposal):
        raise OSError("ledger unavailable")


class Harness:
    def __init__(self, audit_rate=0.0, seed=7, ledger=None, approved=(), commit="loop",
                 handler=None):
        self.ledger = ledger if ledger is not None else EventLedger()
        self.handler_calls = []
        self.custom_handler = handler
        self.registry = ToolRegistry()
        self.registry.register(ToolDefinition(
            name="create_refund",
            description="Create one bounded refund.",
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "integer"}, "usd": {"type": "number"}},
                "required": ["order_id", "usd"],
                "additionalProperties": False,
            },
            effects=frozenset({"refund:write"}),
            version="refund-api-v2",
            task_category="bounded_refund",
            risk_class="low",
            handler=self._handle,
        ))
        self.key = PartitionKey(
            "openai", "m1", "pp1", "anthropic", "claude-pinned", "execution-v1",
            "refund-api-v2", "pol1", "prod1", "bounded_refund", "0.95-1.00", "low",
        )
        self.store = EvidenceStore()
        self.store.put(EvidenceSnapshot("e1", 1, self.key, 99, 1, NOW - timedelta(days=2), NOW))
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), audit_rate)
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(seed), lambda: NOW
        )
        self.gateway = ExecutionGateway(lambda: NOW, evidence=self.store)
        self.proposal = Proposal(
            "p-1", "refund order", "create_refund", {"order_id": 1, "usd": 24}, .96,
            frozenset({"refund:write"}), self.key, "openai",
        )
        self.bundle = ProposalBundle(
            "b-1", "refund order", ProposalReadiness.EXECUTABLE, (self.proposal,), "one refund",
        )
        self.agent = ClaudeExecutionAgent(
            "claude-pinned", "execution-v1", self.controller, self.gateway, self.registry,
            FakeClaude([tool_use("create_refund", "p-1", order_id=1, usd=24), final_text()]),
        )
        self.approved = approved
        if commit == "loop":
            self.loop = CrossModelAuthorityLoop(
                proposer=None, executor=self.agent, ledger=self.ledger,
                policy_version="pol1", environment_version="prod1",
            )

    def _handle(self, args):
        # What the ledger looked like at the instant the side effect happened
        self.handler_calls.append([e.event_type for e in self.ledger.events])
        if self.custom_handler is not None:
            return self.custom_handler(args)
        return {"receipt": "refund-1"}

    def run(self):
        return self.loop.execute(self.bundle, approved_proposal_ids=self.approved)

    def committed(self):
        return [e for e in self.ledger.events if e.event_type == "authority.decision.committed"]


class EventOrderTests(unittest.TestCase):
    def test_decision_and_draw_are_committed_before_the_handler_runs(self):
        # audit_rate 0 keeps the route autonomous while the draw is still realized
        h = Harness(audit_rate=0.0)
        run = h.run()
        self.assertEqual(run.status, ExecutionStatus.COMPLETED)
        self.assertEqual(len(h.handler_calls), 1)
        seen_at_effect = h.handler_calls[0]
        self.assertIn("authority.decision.committed", seen_at_effect)
        self.assertNotIn("execution.step.recorded", seen_at_effect)
        committed = h.committed()[0]
        step = run.steps[0]
        self.assertEqual(committed.payload["decision_id"], step.decision.decision_id)
        self.assertEqual(committed.payload["audit_draw"], step.decision.audit_draw)
        self.assertEqual(committed.payload["audit_probability"], 0.0)
        self.assertEqual(committed.payload["route"], "autonomous")
        self.assertIsNotNone(step.decision.audit_draw)
        recorded = next(e for e in h.ledger.events if e.event_type == "execution.step.recorded")
        self.assertLess(committed.sequence, recorded.sequence)
        self.assertTrue(h.ledger.verify())

    def test_loop_wires_the_commit_hook(self):
        h = Harness()
        self.assertEqual(h.agent.commit, h.loop.commit)

    def test_existing_commit_hook_is_kept(self):
        calls = []
        h = Harness(commit="none")
        h.agent.commit = lambda d, p: calls.append(d.decision_id)
        CrossModelAuthorityLoop(
            proposer=None, executor=h.agent, ledger=h.ledger,
            policy_version="pol1", environment_version="prod1",
        ).execute(h.bundle)
        self.assertEqual(len(calls), 1)
        self.assertEqual(h.committed(), [])


class FailClosedTests(unittest.TestCase):
    def test_commit_failure_refuses_the_step_and_issues_no_token(self):
        h = Harness(ledger=RefusingLedger())
        run = h.run()
        self.assertEqual(run.status, ExecutionStatus.DENIED)
        step = run.steps[0]
        self.assertFalse(step.executed)
        self.assertEqual(step.route, Route.DENY)
        self.assertTrue(step.error.startswith("decision not committed to ledger"))
        self.assertEqual(step.decision.route, Route.AUTONOMOUS)  # the decision itself was fine
        self.assertEqual(h.handler_calls, [])
        self.assertEqual(h.gateway.tokens, {})
        # the refusal is still recorded by the loop after the fact
        self.assertEqual(
            [e.event_type for e in h.ledger.events],
            ["execution.step.recorded", "execution.run.completed"],
        )

    def test_handler_exception_becomes_a_failed_step_not_an_exception(self):
        def failing(args):
            raise RuntimeError("downstream unavailable")

        h = Harness(handler=failing)
        run = h.run()
        self.assertEqual(run.status, ExecutionStatus.FAILED)
        step = run.steps[0]
        self.assertFalse(step.executed)
        self.assertTrue(step.failed)
        self.assertEqual(step.error, "handler failed: downstream unavailable")
        self.assertEqual(step.decision.route, Route.AUTONOMOUS)
        # the token was consumed before the handler ran; nothing is refunded
        self.assertTrue(all(t.used for t in h.gateway.tokens.values()))
        # the failure is on the record after the committed decision
        kinds = [e.event_type for e in h.ledger.events]
        self.assertLess(
            kinds.index("authority.decision.committed"), kinds.index("execution.step.recorded")
        )

    def test_agent_without_a_commit_hook_records_nothing(self):
        # Allowed for offline demos and unit tests; the loop never runs this way.
        h = Harness(commit="none")
        run = h.agent.run(h.bundle)
        self.assertEqual(run.status, ExecutionStatus.COMPLETED)
        self.assertEqual(h.ledger.events, [])


class AuditDrawTests(unittest.TestCase):
    def test_seeded_rng_replays_the_same_committed_draw(self):
        first, second = Harness(audit_rate=0.3, seed=11), Harness(audit_rate=0.3, seed=11)
        first.run()
        second.run()
        a, b = first.committed()[0].payload, second.committed()[0].payload
        self.assertEqual(a["audit_draw"], b["audit_draw"])
        self.assertTrue(0.0 <= a["audit_draw"] < 1.0)
        self.assertEqual(a["route"], "audit" if a["audit_draw"] < 0.3 else "autonomous")

    def test_different_seeds_change_the_draw_not_the_contract(self):
        draws = {
            Harness(audit_rate=0.0, seed=s).run().steps[0].decision.audit_draw for s in range(5)
        }
        self.assertEqual(len(draws), 5)

    def test_audit_executes_unattended_after_the_commit(self):
        h = Harness(audit_rate=1.0)
        run = h.run()
        self.assertEqual(run.status, ExecutionStatus.COMPLETED)
        self.assertEqual(h.committed()[0].payload["route"], "audit")
        self.assertIn("authority.decision.committed", h.handler_calls[0])
        tokens = list(h.gateway.tokens.values())
        self.assertTrue(tokens and not any(t.human_approved for t in tokens))

    def test_audit_is_frozen_before_execution_and_labelled_afterwards(self):
        from exe_auth_ctrl_loop import Oracle, OracleKind, Provenance, ShadowEvidenceLog, TrialLabel

        h = Harness(audit_rate=1.0, commit="none")
        shadow = ShadowEvidenceLog(h.ledger, timedelta(hours=1), lambda: NOW)
        seen = []
        loop = CrossModelAuthorityLoop(
            proposer=None, executor=h.agent, ledger=h.ledger,
            policy_version="pol1", environment_version="prod1", shadow=shadow,
        )
        original = h.agent.commit

        def observing_commit(decision, proposal):
            original(decision, proposal)
            seen.append(shadow.trial(decision.decision_id))

        h.agent.commit = observing_commit
        run = loop.execute(h.bundle)
        decision = run.steps[0].decision
        self.assertEqual(decision.route, Route.AUDIT)
        self.assertTrue(run.steps[0].executed)
        # frozen at commit time, i.e. before the token and the handler
        self.assertEqual(seen[0].provenance, Provenance.AUDIT)
        self.assertEqual(seen[0].proposal_digest, h.proposal.proposal_digest)
        frozen = next(e for e in h.ledger.events if e.event_type == "shadow.frozen")
        self.assertLess(frozen.sequence, next(
            e.sequence for e in h.ledger.events if e.event_type == "execution.step.recorded"
        ))
        # the label lands on the frozen form, under the decision id, as AUDIT provenance
        oracle = Oracle("reviewer", "1", OracleKind.INDEPENDENT_REVIEW, "post-hoc")
        result = shadow.adjudicate(
            decision.decision_id, h.key, h.proposal.proposal_digest, TrialLabel.POSITIVE,
            oracle, NOW + timedelta(minutes=5),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(shadow.counts(h.key, Provenance.AUDIT).positive, 1)
        self.assertEqual(shadow.counts(h.key, Provenance.AUTONOMOUS).frozen, 0)

    def test_autonomous_route_is_not_frozen_by_the_loop(self):
        from exe_auth_ctrl_loop import ShadowEvidenceLog

        h = Harness(audit_rate=0.0, commit="none")
        shadow = ShadowEvidenceLog(h.ledger, timedelta(hours=1), lambda: NOW)
        CrossModelAuthorityLoop(
            proposer=None, executor=h.agent, ledger=h.ledger,
            policy_version="pol1", environment_version="prod1", shadow=shadow,
        ).execute(h.bundle)
        self.assertEqual([e for e in h.ledger.events if e.event_type == "shadow.frozen"], [])

    def test_tampering_with_the_committed_draw_breaks_the_chain(self):
        h = Harness(audit_rate=0.5)
        h.run()
        event = h.committed()[0]
        h.ledger.events[event.sequence] = replace(
            event, payload={**event.payload, "audit_draw": 0.999}
        )
        self.assertFalse(h.ledger.verify())


class ChainLimitTests(unittest.TestCase):
    def ledger(self, n=4):
        ledger = EventLedger()
        for i in range(n):
            ledger.append("e", f"a-{i}", "x", {"i": i})
        return ledger

    def test_interior_edit_is_detected(self):
        ledger = self.ledger()
        ledger.events[1] = replace(ledger.events[1], payload={"i": 99})
        self.assertFalse(ledger.verify())

    def test_truncation_is_not_detected_by_verify_alone(self):
        ledger = self.ledger()
        anchor = ledger.anchor()
        del ledger.events[-1]
        self.assertTrue(ledger.verify())  # the documented limit
        self.assertFalse(ledger.verify_anchor(*anchor))  # what an external anchor adds

    def test_wholesale_rewrite_is_not_detected_by_verify_alone(self):
        ledger = self.ledger()
        anchor = ledger.anchor()
        rebuilt = EventLedger()
        for i in range(4):
            rebuilt.append("e", f"a-{i}", "x", {"i": -i})  # different history, fresh chain
        ledger.events = rebuilt.events
        self.assertTrue(ledger.verify())
        self.assertFalse(ledger.verify_anchor(*anchor))

    def test_anchor_holds_for_an_intact_and_extended_chain(self):
        ledger = self.ledger()
        anchor = ledger.anchor()
        ledger.append("e", "a-4", "x", {"i": 4})
        self.assertTrue(ledger.verify_anchor(*anchor))
        self.assertEqual(anchor, (3, ledger.events[3].event_hash))

    def test_anchor_on_empty_ledger(self):
        ledger = EventLedger()
        self.assertEqual(ledger.anchor(), (-1, "GENESIS"))
        self.assertTrue(ledger.verify_anchor(-1, "GENESIS"))
        self.assertFalse(ledger.verify_anchor(0, "anything"))


if __name__ == "__main__":
    unittest.main()
