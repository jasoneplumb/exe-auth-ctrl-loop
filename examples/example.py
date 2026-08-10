"""Offline example of the deterministic authority and gateway path."""

import random
from datetime import datetime, timedelta, timezone

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    PartitionKey,
    Policy,
    Proposal,
    Route,
)

now = datetime.now(timezone.utc)
key = PartitionKey(
    "openai", "openai-model-pinned", "proposal-v1",
    "anthropic", "claude-model-pinned", "execution-v1",
    "refund-api-v2", "policy-v5", "prod-us-v4",
    "bounded_refund", "0.95-1.00", "financial-low",
)
proposal = Proposal(
    "op-1042", "refund a verified duplicate charge", "create_refund",
    {"order_id": 4815, "usd": 24.00}, .97,
    frozenset({"refund:create"}), key, "openai",
)
evidence = EvidenceStore()
evidence.put(EvidenceSnapshot(
    "ev-88", 4, key, 198, 2, now - timedelta(days=14), now,
))
policy = Policy(
    "policy-v5", {"financial-low": .95}, n_min=100,
    max_evidence_age=timedelta(days=30), audit_rate=.05,
)
controller = AuthorityController(evidence, policy, random.Random(7))
decision = controller.evaluate(proposal)
print({
    "route": decision.route.value,
    "lower_bound": round(decision.lower_bound or 0, 4),
    "required": decision.required_bound,
    "reasons": decision.reason_codes,
})

if decision.route == Route.AUTONOMOUS:
    gateway = ExecutionGateway()
    token = gateway.issue(decision, proposal, policy)
    receipt = gateway.execute(
        token.token_id,
        proposal,
        lambda p: {"receipt": "refund-9001", "parameters": dict(p.parameters)},
    )
    print(receipt)
