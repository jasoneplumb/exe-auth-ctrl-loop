"""Offline example of the deterministic authority and gateway path."""

import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from exe_auth_ctrl_loop.ledger import EventLedger
from exe_auth_ctrl_loop.principals import (
    GrantScope,
    Keyring,
    PrincipalKind,
    Principals,
    root_grant_payload,
)
from exe_auth_ctrl_loop.store import Store
from exe_auth_ctrl_loop.tools import HandlerTable, ToolCatalog, ToolDefinition

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
# Authority state is durable since v3: evidence lives in a database file. A throwaway
# directory keeps this demo self-contained; a deployment passes a real path.
# The ledger key keys the hash chain (v3 Step 08); this one is example-only, and a
# deployment supplies its own secret from outside the database file.
LEDGER_KEY = b"example-only-ledger-key"
_scratch = tempfile.TemporaryDirectory()
evidence = EvidenceStore(Store(Path(_scratch.name) / "authority.db", ledger_key=LEDGER_KEY))
evidence.put(EvidenceSnapshot(
    "ev-88", 4, key, 297, 3, now - timedelta(days=14), now,
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

# The host's tool catalog and handler table. The catalog describes the tool; the
# handler table, given to the gateway alone, is the only route to its effect.
catalog = ToolCatalog()
catalog.register(ToolDefinition(
    name="create_refund",
    description="Refund one order.",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "integer"}, "usd": {"type": "number"}},
        "required": ["order_id", "usd"],
        "additionalProperties": False,
    },
    effects=frozenset({"refund:create"}),
    version="refund-api-v2",
    task_category="bounded_refund",
    risk_class="financial-low",
))
handlers = HandlerTable(catalog)
handlers.register(
    "create_refund", lambda args: {"receipt": "refund-9001", "parameters": dict(args)}
)

# Who acts, and on whose authority (v3 Step 05): a human operator delegates this tool to
# the agent. The key is an example-only secret; a deployment supplies its own.
keyring = Keyring({"k-operator": b"example-only-secret"})
principals = Principals(evidence.store, keyring)
principals.register("operator", PrincipalKind.HUMAN, by="controller", key_id="k-operator")
principals.register("agent", PrincipalKind.MODEL, by="controller")
scope = GrantScope(tools=frozenset({"create_refund"}))
grant_expiry = datetime(2100, 1, 1, tzinfo=timezone.utc)
principals.grant_root(
    "operator", "agent", scope, grant_expiry,
    keyring.sign("k-operator", root_grant_payload("g-1", "operator", "agent", scope, grant_expiry)),
    grant_id="g-1",
)

if decision.route == Route.AUTONOMOUS:
    # The decision goes on the ledger first; the gateway issues only for a committed one.
    EventLedger(evidence.store).commit_decision(decision, proposal)
    gateway = ExecutionGateway(store=evidence.store, handlers=handlers, principals=principals)
    token = gateway.issue(decision, proposal, policy, principal_id="agent", grant_id="g-1")
    # No handler is passed: the gateway runs the one the token names.
    receipt = gateway.execute(token.token_id, proposal)
    print(receipt)
