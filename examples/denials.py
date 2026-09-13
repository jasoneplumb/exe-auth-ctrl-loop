"""Offline demonstration of the paths that do NOT reach a handler.

`example.py` shows the authorized path end to end. This file shows the other
half of the boundary, because a gate that only ever opens demonstrates nothing:
five requests that a reviewer can watch fail, each for a different reason, with
no network access and no API keys.

Run: python examples/denials.py

Each case prints the route and reason codes the controller produced, or the
PermissionError the gateway raised. The handler below records every invocation
it receives; the final line asserts that it was never called.
"""

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
    ProposalReadiness,
    Route,
)

now = datetime.now(timezone.utc)

KEY = PartitionKey(
    "openai", "openai-model-pinned", "proposal-v1",
    "anthropic", "claude-model-pinned", "execution-v1",
    "refund-api-v2", "policy-v5", "prod-us-v4",
    "bounded_refund", "0.95-1.00", "financial-low",
)
POLICY = Policy(
    "policy-v5", {"financial-low": .95}, n_min=100,
    max_evidence_age=timedelta(days=30), audit_rate=0.0,
)

# The only path to a side effect. If any case below reached it, this list would
# not be empty at the end of the run.
effects_performed: list[str] = []


def handler(proposal: Proposal) -> dict[str, object]:
    effects_performed.append(proposal.proposal_id)
    return {"receipt": "refund-9001", "parameters": dict(proposal.parameters)}


def proposal(proposal_id: str, **overrides: object) -> Proposal:
    fields: dict[str, object] = {
        "proposal_id": proposal_id,
        "intent": "refund a verified duplicate charge",
        "tool_name": "create_refund",
        "parameters": {"order_id": 4815, "usd": 24.00},
        "confidence": .97,
        "requested_effects": frozenset({"refund:create"}),
        "partition": KEY,
        "proposer": "openai",
    }
    fields.update(overrides)
    return Proposal(**fields)  # type: ignore[arg-type]


def evidence_with(snapshot: EvidenceSnapshot) -> EvidenceStore:
    store = EvidenceStore()
    store.put(snapshot)
    return store


def controller_for(store: EvidenceStore) -> AuthorityController:
    return AuthorityController(store, POLICY, random.Random(7))


def show(case: str, decision) -> None:
    print(f"{case:<28} route={decision.route.value:<14} reasons={decision.reason_codes}")


# --- 1. Sparse evidence -------------------------------------------------------
# 40 observations against a policy that requires 100. The confidence bound is
# not the objection; the sample size is. Autonomy is withheld before the
# arithmetic is consulted.
sparse = controller_for(evidence_with(
    EvidenceSnapshot("ev-01", 1, KEY, 39, 1, now - timedelta(days=3), now)
)).evaluate(proposal("op-sparse"))
show("sparse evidence", sparse)

# --- 2. Stale evidence --------------------------------------------------------
# A mature, high-success partition — collected 90 days ago against a 30-day
# ceiling. Past performance expires.
stale = controller_for(evidence_with(
    EvidenceSnapshot("ev-02", 1, KEY, 198, 2, now - timedelta(days=120), now - timedelta(days=90))
)).evaluate(proposal("op-stale"))
show("stale evidence", stale)

# --- 3. No evidence for this exact partition ---------------------------------
# Nothing has been recorded for this combination of models, versions, tool,
# policy, and risk class. An empty store is not a clean record: absence routes
# to a human rather than to a default.
missing = controller_for(EvidenceStore()).evaluate(proposal("op-unknown"))
show("no exact evidence", missing)

# --- 4. An unresolved question in the proposal itself ------------------------
# The proposing model said it was not sure. That answer is carried, not
# averaged away by an otherwise strong record.
questioning = controller_for(evidence_with(
    EvidenceSnapshot("ev-04", 1, KEY, 198, 2, now - timedelta(days=14), now)
)).evaluate(proposal(
    "op-questions",
    readiness=ProposalReadiness.EXECUTABLE,
    unresolved_questions=("is order 4815 the duplicate, or 4816?",),
))
show("unresolved question", questioning)

# --- 5. Arguments edited after authorization ---------------------------------
# This one is authorized. The token is real. Then the amount changes between
# the decision and the call — the case a plan-level approval would wave through.
authorized_store = evidence_with(
    EvidenceSnapshot("ev-05", 1, KEY, 198, 2, now - timedelta(days=14), now)
)
original = proposal("op-mutated")
granted = controller_for(authorized_store).evaluate(original)
show("authorized (before edit)", granted)
assert granted.route == Route.AUTONOMOUS, granted.reason_codes

gateway = ExecutionGateway()
token = gateway.issue(granted, original, POLICY)
edited = proposal("op-mutated", parameters={"order_id": 4815, "usd": 2400.00})
try:
    gateway.execute(token.token_id, edited, handler)
except PermissionError as exc:
    print(f"{'arguments edited':<28} gateway denied: {exc}")

# --- 6. The same capability spent twice --------------------------------------
# The unedited call succeeds once. The token is single-use, so the replay of a
# valid, unexpired, correctly scoped request is refused on identity alone.
gateway.execute(token.token_id, original, handler)
print(f"{'first use':<28} handler ran: {effects_performed}")
try:
    gateway.execute(token.token_id, original, handler)
except PermissionError as exc:
    print(f"{'replayed capability':<28} gateway denied: {exc}")

assert effects_performed == ["op-mutated"], effects_performed
print("\nOne effect, from the one request that was authorized and unmodified.")
