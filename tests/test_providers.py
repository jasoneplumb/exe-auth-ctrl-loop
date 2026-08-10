import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    Policy,
    Route,
)
from exe_auth_ctrl_loop.executor import ClaudeExecutionAgent, ExecutionStatus
from exe_auth_ctrl_loop.providers import OpenAIProposalGenerator, ProposalDraftModel
from exe_auth_ctrl_loop.tools import ToolDefinition, ToolRegistry

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


class FakeOpenAIResponses:
    def __init__(self, draft):
        self.draft = draft
        self.last_request = None

    def parse(self, **kwargs):
        self.last_request = kwargs
        return SimpleNamespace(output_parsed=self.draft)


class FakeOpenAI:
    def __init__(self, draft):
        self.responses = FakeOpenAIResponses(draft)


class FakeClaudeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return next(self.responses)


class FakeClaude:
    def __init__(self, responses):
        self.messages = FakeClaudeMessages(responses)


def tool_use(name, proposal_id, **parameters):
    return SimpleNamespace(
        content=[SimpleNamespace(
            type="tool_use", id="toolu-1", name=name,
            input={"proposal_id": proposal_id, **parameters},
        )]
    )


def final_text(text="complete"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class ProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.receipts = []
        self.registry = ToolRegistry()
        self.registry.register(ToolDefinition(
            name="create_refund",
            description="Create one bounded refund.",
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "usd": {"type": "number", "minimum": 0, "maximum": 50},
                },
                "required": ["order_id", "usd"],
                "additionalProperties": False,
            },
            effects=frozenset({"refund:write"}),
            version="refund-api-v2",
            task_category="bounded_refund",
            risk_class="low",
            handler=lambda args: self._record(args),
        ))

    def _record(self, args):
        receipt = {"receipt_id": "r-1", **args}
        self.receipts.append(receipt)
        return receipt

    def generate_bundle(self, readiness="executable"):
        draft = ProposalDraftModel.model_validate({
            "readiness": readiness,
            "summary": "Refund a verified duplicate charge.",
            "missing_information": [] if readiness == "executable" else ["confirmation"],
            "actions": [{
                "action_id": "refund",
                "intent": "Refund the duplicate charge",
                "tool_name": "create_refund",
                "arguments_json": "{\"order_id\": 7, \"usd\": 24}",
                "declared_effects": ["refund:write"],
                "declared_confidence": .97,
                "preconditions": ["duplicate charge verified"],
                "success_criteria": ["one receipt returned"],
                "assumptions": [],
                "unresolved_questions": [],
            }],
        })
        client = FakeOpenAI(draft)
        generator = OpenAIProposalGenerator(
            "openai-model-pinned", "proposal-v1", self.registry, client,
        )
        bundle = generator.generate(
            "Refund duplicate charge",
            execution_model="claude-model-pinned",
            execution_prompt_version="execution-v1",
            policy_version="policy-v1",
            environment_version="prod-v3",
        )
        return bundle, client

    def executor(self, bundle, claude_responses, evidence=True):
        store = EvidenceStore()
        if evidence:
            for proposal in bundle.proposals:
                store.put(EvidenceSnapshot(
                    "ev-1", 1, proposal.partition, 199, 1,
                    NOW - timedelta(days=10), NOW,
                ))
        policy = Policy(
            "policy-v1", {"low": .95}, 100, timedelta(days=30), 0.0,
        )
        authority = AuthorityController(store, policy, clock=lambda: NOW)
        gateway = ExecutionGateway(clock=lambda: NOW)
        return ClaudeExecutionAgent(
            "claude-model-pinned", "execution-v1", authority, gateway,
            self.registry, FakeClaude(claude_responses),
        )

    def test_openai_structured_output_becomes_versioned_proposal(self):
        bundle, client = self.generate_bundle()
        proposal = bundle.proposals[0]
        self.assertEqual(proposal.partition.proposal_provider, "openai")
        self.assertEqual(proposal.partition.execution_provider, "anthropic")
        self.assertEqual(proposal.partition.proposal_model_version, "openai-model-pinned")
        self.assertEqual(proposal.partition.execution_model_version, "claude-model-pinned")
        self.assertIs(client.responses.last_request["text_format"], ProposalDraftModel)

    def test_claude_request_executes_only_exact_authorized_action(self):
        bundle, _ = self.generate_bundle()
        proposal = bundle.proposals[0]
        agent = self.executor(bundle, [
            tool_use("create_refund", proposal.proposal_id, order_id=7, usd=24),
            final_text(),
        ])
        run = agent.run(bundle)
        self.assertEqual(run.status, ExecutionStatus.COMPLETED)
        self.assertEqual(len(self.receipts), 1)
        self.assertTrue(run.steps[0].executed)

    def test_claude_changed_argument_is_denied_before_handler(self):
        bundle, _ = self.generate_bundle()
        proposal = bundle.proposals[0]
        agent = self.executor(bundle, [
            tool_use("create_refund", proposal.proposal_id, order_id=7, usd=25),
        ])
        run = agent.run(bundle)
        self.assertEqual(run.status, ExecutionStatus.DENIED)
        self.assertEqual(self.receipts, [])
        self.assertIn("arguments changed", run.steps[0].error)

    def test_insufficient_evidence_waits_for_human(self):
        bundle, _ = self.generate_bundle()
        proposal = bundle.proposals[0]
        agent = self.executor(bundle, [
            tool_use("create_refund", proposal.proposal_id, order_id=7, usd=24),
        ], evidence=False)
        run = agent.run(bundle)
        self.assertEqual(run.status, ExecutionStatus.AWAITING_APPROVAL)
        self.assertEqual(self.receipts, [])

    def test_human_approval_can_release_approval_route(self):
        bundle, _ = self.generate_bundle()
        proposal = bundle.proposals[0]
        agent = self.executor(bundle, [
            tool_use("create_refund", proposal.proposal_id, order_id=7, usd=24),
            final_text(),
        ], evidence=False)
        run = agent.run(bundle, approved_proposal_ids={proposal.proposal_id})
        self.assertEqual(run.status, ExecutionStatus.COMPLETED)
        self.assertEqual(len(self.receipts), 1)
        self.assertTrue(run.steps[0].decision.route == Route.HUMAN_APPROVAL)

    def test_draft_never_reaches_claude(self):
        bundle, _ = self.generate_bundle("draft")
        fake = FakeClaude([])
        agent = self.executor(bundle, [])
        agent.client = fake
        run = agent.run(bundle)
        self.assertEqual(run.status, ExecutionStatus.NEEDS_REVISION)
        self.assertEqual(fake.messages.requests, [])


if __name__ == "__main__":
    unittest.main()
