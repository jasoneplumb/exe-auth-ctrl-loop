"""
Intent: Show the MCP binding end to end — one authorized tools/call, and the five calls a
        server refuses
Context: Runnable offline alongside examples/example.py; CI executes it on every push
Pattern: The server function trusts nothing it is told, recomputing the call digest from
        the tool name and arguments it actually received
Future: Swap the shared secret for an asymmetric scheme so servers verify without holding
        the gateway's signing key
"""

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

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
from exe_auth_ctrl_loop.mcp import (
    MetaVerificationError,
    attach_meta,
    build_call_meta,
    verify_call_meta,
)

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
# constraint: a literal secret is demo-only. In production the gateway and server hold it
# as a managed secret, and neither this file's key nor its fixed clock is representative.
SHARED_SECRET = b"demo-only-shared-secret-between-gateway-and-server"


def build_request(params: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, Any]:
    """
    intent: Assemble a JSON-RPC tools/call carrying both MCP's own _meta keys and ours
    context: Demonstrates coexistence — the base protocol's required per-request fields
             survive the merge untouched
    """
    base = dict(params)
    base["_meta"] = {
        # Required on every request by the MCP base protocol; our keys sit alongside.
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": attach_meta(base, meta),
    }


def mcp_server(request: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """
    intent: Stand in for an MCP server that grants nothing on an unverified claim
    method: Verify before acting; a raised MetaVerificationError becomes a denial, never a
            warning that execution proceeds past
    effect: Every refusal path in the demo below returns without producing a side effect
    """
    params = request["params"]
    try:
        verified = verify_call_meta(
            params, params["name"], params["arguments"], SHARED_SECRET, now,
        )
    except MetaVerificationError as denial:
        return {"executed": False, "denied": str(denial)}
    return {
        "executed": True,
        "receipt": "refund-9001",
        "audit_committed": verified.audit_committed,
        "human_approved": verified.human_approved,
        "evidence_snapshot": verified.evidence_snapshot,
    }


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
evidence.put(EvidenceSnapshot("ev-88", 4, key, 198, 2, NOW - timedelta(days=14), NOW))
policy = Policy(
    "policy-v5", {"financial-low": .95}, n_min=100,
    max_evidence_age=timedelta(days=30), audit_rate=.05,
)

controller = AuthorityController(evidence, policy, random.Random(7), lambda: NOW)
decision = controller.evaluate(proposal)
print({"route": decision.route.value, "reasons": decision.reason_codes})
assert decision.route == Route.AUTONOMOUS, "seeded evidence should authorize this operation"

gateway = ExecutionGateway(lambda: NOW)
token = gateway.issue(decision, proposal, policy)
meta = build_call_meta(decision, proposal, token, SHARED_SECRET)
request = build_request(
    {"name": proposal.tool_name, "arguments": dict(proposal.parameters)}, meta,
)

print("\nauthority metadata on the request:")
for name, value in sorted(meta.items()):
    print(f"  {name} = {value}")

print("\nMCP's own _meta keys survive alongside ours:")
print(f"  {sorted(k for k in request['params']['_meta'] if k.startswith('io.'))}")

print("\nthe authorized call:")
print(f"  {mcp_server(request, NOW)}")

print("\ncalls the server refuses:")

tampered = build_request(
    {"name": "create_refund", "arguments": {"order_id": 4815, "usd": 9999.00}}, meta,
)
print(f"  edited amount     -> {mcp_server(tampered, NOW)['denied']}")

substituted = build_request(
    {"name": "delete_order", "arguments": dict(proposal.parameters)}, meta,
)
print(f"  substituted tool  -> {mcp_server(substituted, NOW)['denied']}")

forged = build_request(
    {"name": proposal.tool_name, "arguments": dict(proposal.parameters)}, dict(meta),
)
forged["params"]["_meta"][next(k for k in meta if k.endswith("/auditCommitted"))] = True
print(f"  flipped audit bit -> {mcp_server(forged, NOW)['denied']}")

expired_at = NOW + policy.token_ttl + timedelta(seconds=1)
print(f"  expired token     -> {mcp_server(request, expired_at)['denied']}")

bare = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
    "name": proposal.tool_name, "arguments": dict(proposal.parameters),
}}
print(f"  no metadata       -> {mcp_server(bare, NOW)['denied']}")
