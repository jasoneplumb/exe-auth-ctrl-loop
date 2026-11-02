"""Live OpenAI -> authority controller -> Claude -> local mock tool example.

Set OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_MODEL, and ANTHROPIC_MODEL.
Use pinned model identifiers when collecting evidence; aliases can move.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    Policy,
)
from exe_auth_ctrl_loop.executor import ClaudeExecutionAgent
from exe_auth_ctrl_loop.ledger import EventLedger
from exe_auth_ctrl_loop.providers import OpenAIProposalGenerator
from exe_auth_ctrl_loop.store import Store
from exe_auth_ctrl_loop.tools import HandlerTable, ToolCatalog, ToolDefinition


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Set {name} before running this example.")
    return value


openai_model = required_env("OPENAI_MODEL")
anthropic_model = required_env("ANTHROPIC_MODEL")
now = datetime.now(timezone.utc)

catalog = ToolCatalog()
catalog.register(ToolDefinition(
    name="record_refund_request",
    description="Record one simulated refund request; this demo does not move money.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer"},
            "usd": {"type": "number", "minimum": 0, "maximum": 50},
        },
        "required": ["order_id", "usd"],
        "additionalProperties": False,
    },
    effects=frozenset({"demo-refund:write"}),
    version="demo-refund-v1",
    task_category="bounded_demo_refund",
    risk_class="low",
))
# Only the gateway holds the handler table; the models' side gets the catalog.
handlers = HandlerTable(catalog)
handlers.register(
    "record_refund_request", lambda args: {"receipt_id": "demo-r-1", "recorded": dict(args)}
)

generator = OpenAIProposalGenerator(
    model=openai_model,
    prompt_version="proposal-prompt-v1",
    catalog=catalog,
)
bundle = generator.generate(
    "Record a simulated $24 refund request for order 4815.",
    execution_model=anthropic_model,
    execution_prompt_version="execution-prompt-v1",
    policy_version="policy-v1",
    environment_version="local-demo-v1",
)
print({"bundle": bundle.bundle_id, "readiness": bundle.readiness.value})

# Authority state is durable since v3: evidence lives in a database file. A throwaway
# directory keeps this demo self-contained; a deployment passes a real path.
_scratch = tempfile.TemporaryDirectory()
evidence = EvidenceStore(Store(Path(_scratch.name) / "authority.db"))
for proposal in bundle.proposals:
    # Illustrative seeded evidence only. Production evidence must come from adjudicated outcomes.
    evidence.put(EvidenceSnapshot(
        evidence_id=f"seed-{proposal.proposal_id}",
        version=1,
        key=proposal.partition,
        successes=199,
        failures=1,
        collected_from=now - timedelta(days=7),
        collected_until=now,
    ))

policy = Policy(
    version="policy-v1",
    minimum_success_by_risk={"low": .95},
    n_min=100,
    max_evidence_age=timedelta(days=30),
    audit_rate=0.0,
)
executor = ClaudeExecutionAgent(
    model=anthropic_model,
    prompt_version="execution-prompt-v1",
    authority=AuthorityController(evidence, policy),
    gateway=ExecutionGateway(store=evidence.store, handlers=handlers),
    catalog=catalog,
    # every decision is committed to the ledger before the gateway will issue for it
    commit=EventLedger(evidence.store).commit_decision,
)
run = executor.run(bundle)
print({
    "status": run.status.value,
    "final_text": run.final_text,
    "steps": [
        {
            "proposal_id": step.proposal_id,
            "route": step.route.value,
            "executed": step.executed,
            "receipt": step.receipt,
            "error": step.error,
        }
        for step in run.steps
    ],
})
