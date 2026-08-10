import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    Outcome,
    OutcomeStatus,
    PartitionKey,
    Policy,
    Proposal,
    ProposalReadiness,
    Route,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.key = PartitionKey(
            "openai", "openai-model-pinned", "proposal-v1",
            "anthropic", "claude-model-pinned", "execution-v1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
        )
        self.proposal = Proposal(
            "p-1", "refund order", "create_refund", {"order_id": 7, "usd": 24},
            .96, frozenset({"refund:write"}), self.key, "openai",
        )
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        self.store = EvidenceStore()

    def controller(self, seed=1):
        return AuthorityController(self.store, self.policy, random.Random(seed), lambda: NOW)

    def mature_evidence(self):
        self.store.put(EvidenceSnapshot(
            "e1", 1, self.key, 99, 1, NOW - timedelta(days=2), NOW,
        ))

    def test_no_evidence_requires_approval(self):
        self.assertEqual(self.controller().evaluate(self.proposal).route, Route.HUMAN_APPROVAL)

    def test_strong_current_evidence_authorizes(self):
        self.mature_evidence()
        decision = self.controller().evaluate(self.proposal)
        self.assertEqual(decision.route, Route.AUTONOMOUS)
        gateway = ExecutionGateway(lambda: NOW)
        token = gateway.issue(decision, self.proposal, self.policy)
        self.assertEqual(gateway.execute(token.token_id, self.proposal, lambda p: "done"), "done")
        self.assertEqual(token.allowed_tool, "create_refund")
        self.assertEqual(token.evidence_version, 1)

    def test_token_is_single_use(self):
        self.mature_evidence()
        decision = self.controller().evaluate(self.proposal)
        gateway = ExecutionGateway(lambda: NOW)
        token = gateway.issue(decision, self.proposal, self.policy)
        gateway.execute(token.token_id, self.proposal, lambda p: None)
        with self.assertRaises(PermissionError):
            gateway.execute(token.token_id, self.proposal, lambda p: None)

    def test_changed_tool_is_rejected(self):
        self.mature_evidence()
        decision = self.controller().evaluate(self.proposal)
        gateway = ExecutionGateway(lambda: NOW)
        token = gateway.issue(decision, self.proposal, self.policy)
        changed = replace(self.proposal, tool_name="delete_order")
        with self.assertRaises(PermissionError):
            gateway.execute(token.token_id, changed, lambda p: None)

    def test_stale_evidence_requires_approval(self):
        self.store.put(EvidenceSnapshot(
            "e1", 1, self.key, 999, 1,
            NOW - timedelta(days=90), NOW - timedelta(days=31),
        ))
        self.assertEqual(self.controller().evaluate(self.proposal).route, Route.HUMAN_APPROVAL)

    def test_random_audit_is_not_autonomous(self):
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 1.0)
        self.mature_evidence()
        decision = self.controller().evaluate(self.proposal)
        self.assertEqual(decision.route, Route.AUDIT)
        with self.assertRaises(PermissionError):
            ExecutionGateway(lambda: NOW).issue(decision, self.proposal, self.policy)

    def test_draft_requests_revision(self):
        draft = replace(self.proposal, readiness=ProposalReadiness.DRAFT)
        self.assertEqual(self.controller().evaluate(draft).route, Route.REVISION)

    def test_unresolved_question_requests_clarification(self):
        unclear = replace(self.proposal, unresolved_questions=("Which order?",))
        self.assertEqual(self.controller().evaluate(unclear).route, Route.CLARIFICATION)

    def test_severe_failure_suspends_before_update(self):
        self.mature_evidence()
        outcome = Outcome("p-1", OutcomeStatus.UNACCEPTABLE, True, "reviewer", NOW, "d")
        self.store.adjudicate(self.key, outcome, autonomous=True)
        self.assertTrue(self.store.get(self.key).suspended)
        self.assertEqual(self.controller().evaluate(self.proposal).route, Route.HUMAN_APPROVAL)


if __name__ == "__main__":
    unittest.main()
