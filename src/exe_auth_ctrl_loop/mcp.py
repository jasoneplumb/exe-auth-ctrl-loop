"""
Intent: Bind an authorization to the exact MCP tools/call it authorizes, so a receiving
        server can verify that call rather than trust an assertion about it
Context: Emitted by the host-owned gateway once authority.py has issued a token; consumed
        by any MCP server holding the shared secret. Never emitted by a model.
Pattern: Fail-closed verification — every check raises; there is no "unverified" return value
Future: Since extension 0.2 (v3 Step 07) a block names its server (`audience`, in the MAC)
        and is spent at that server: a consequential call by redemption against the host's
        token table, an effect-free one by a single-use nonce. A replay, to the same server
        or another, is refused. The shared secret remains a single key for every server;
        per-server keys are a production requirement.

Key names follow the MCP _meta naming rules. The prefix is a third-party vendor prefix in
reverse-DNS notation; prefixes whose second label is `mcp` or `modelcontextprotocol` are
reserved by the specification and are rejected here.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Mapping

from .authority import (
    AuthorizationToken,
    Decision,
    ExecutionGateway,
    PartitionKey,
    Proposal,
    Route,
    digest,
)

EXTENSION_PREFIX = "com.jasoneplumb.exe-auth/"
EXTENSION_VERSION = "0.2"

# constraint: MCP labels start with a letter and end with a letter or digit; hyphens are
# interior-only. Names are alphanumeric at both ends with . _ - permitted between.
# constraint: the empty name is deliberately accepted -- the spec reads "Unless empty, MUST
# begin and end with an alphanumeric character", so a bare prefix is a legal key. Tightening
# this would reject something MCP permits, and this module is the spec's rules in code.
_LABEL = r"[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_PREFIX_PATTERN = re.compile(rf"^(?:{_LABEL})(?:\.{_LABEL})*/$")
_NAME_PATTERN = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)?$")
_RESERVED_SECOND_LABELS = frozenset({"mcp", "modelcontextprotocol"})


class MetaKeyError(ValueError):
    """A _meta key violates the MCP naming rules or squats a reserved prefix."""


class MetaVerificationError(PermissionError):
    """Authority metadata is absent, malformed, unauthentic, or does not bind this call."""


def validate_meta_prefix(prefix: str) -> None:
    """
    intent: Refuse to emit a key that is malformed or reserved for MCP's own use
    method: Match the label grammar, then reject any prefix whose second label is `mcp`
            or `modelcontextprotocol`
    effect: `com.mcp.tools/` is rejected; `com.example.mcp/` is not, since only the second
            label carries the reservation
    """
    if not _PREFIX_PATTERN.match(prefix):
        raise MetaKeyError(f"invalid _meta prefix: {prefix!r}")
    labels = prefix.rstrip("/").split(".")
    if len(labels) >= 2 and labels[1].lower() in _RESERVED_SECOND_LABELS:
        raise MetaKeyError(f"prefix reserved for MCP use: {prefix!r}")


def meta_key(name: str, prefix: str = EXTENSION_PREFIX) -> str:
    """
    intent: Build a validated _meta key
    effect: An illegal key raises at import time rather than reaching the wire, since the
            module's key constants are built through this function
    """
    validate_meta_prefix(prefix)
    if not _NAME_PATTERN.match(name):
        raise MetaKeyError(f"invalid _meta key name: {name!r}")
    return f"{prefix}{name}"


KEY_VERSION = meta_key("version")
KEY_PROPOSAL_DIGEST = meta_key("proposalDigest")
KEY_EVIDENCE_SNAPSHOT = meta_key("evidenceSnapshot")
KEY_AUDIT_COMMITTED = meta_key("auditCommitted")
KEY_HUMAN_APPROVED = meta_key("humanApproved")
KEY_CALL_DIGEST = meta_key("callDigest")
KEY_DECISION_ID = meta_key("decisionId")
KEY_TOKEN_ID = meta_key("tokenId")
KEY_POLICY_VERSION = meta_key("policyVersion")
KEY_EXPIRES_AT = meta_key("expiresAt")
KEY_AUDIENCE = meta_key("audience")
KEY_REDEMPTION = meta_key("redemption")
KEY_MAC = meta_key("mac")

# constraint: the MAC covers exactly these keys, in this order-independent set. Adding a
# key here changes the wire format and requires an EXTENSION_VERSION bump.
_SIGNED_KEYS = (
    KEY_VERSION,
    KEY_PROPOSAL_DIGEST,
    KEY_EVIDENCE_SNAPSHOT,
    KEY_AUDIT_COMMITTED,
    KEY_HUMAN_APPROVED,
    KEY_CALL_DIGEST,
    KEY_DECISION_ID,
    KEY_TOKEN_ID,
    KEY_POLICY_VERSION,
    KEY_EXPIRES_AT,
    KEY_AUDIENCE,
    KEY_REDEMPTION,
)

# constraint: `server` for a consequential call (non-empty effect set), which the receiving
# server must redeem against the token table; `nonce` for an effect-free one (spec §6)
REDEMPTION_MODES = ("server", "nonce")


@dataclass(frozen=True)
class VerifiedAuthority:
    """What an MCP server may rely on after verification, and nothing more."""

    proposal_digest: str
    evidence_snapshot: str | None
    audit_committed: bool
    human_approved: bool
    decision_id: str
    token_id: str
    policy_version: str
    expires_at: datetime
    audience: str
    redemption: str


def call_digest(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """
    intent: Give the server something it can recompute from what it received
    method: Canonical JSON digest of the tool name and arguments
    context: Both halves of the binding — the gateway commits to it, the server checks it
    """
    return digest({"tool": tool_name, "arguments": dict(arguments)})


def evidence_snapshot_hash(decision: Decision, partition: PartitionKey) -> str | None:
    """
    intent: Identify the evidence that justified this decision without exposing its contents
    effect: None when no evidence backed the decision, which is the human-approval path
    constraint: the partition is part of the input because nothing enforces evidence_id
                uniqueness across partitions -- EvidenceStore keys on PartitionKey, so the
                same id may name different evidence in two partitions. Hashing the id alone
                would let those collide and make downstream correlation ambiguous.
    """
    if decision.evidence_id is None:
        return None
    return digest({
        "evidence_id": decision.evidence_id,
        "version": decision.evidence_version,
        # constraint: asdict rather than __dict__ -- identical output today, but __dict__
        # disappears if PartitionKey ever gains __slots__, and it would fail silently
        "partition": asdict(partition),
    })


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _mac(payload: Mapping[str, Any], secret: bytes) -> str:
    return hmac.new(secret, _canonical(payload), sha256).hexdigest()


def build_call_meta(
    decision: Decision,
    proposal: Proposal,
    token: AuthorizationToken,
    secret: bytes,
) -> dict[str, Any]:
    """
    intent: Emit signed authority metadata for one tools/call request
    method: Assemble the signed field set, then MAC it under the shared secret
    context: Called by the host gateway after issue(); the resulting block travels in _meta
    tradeoff: The secret is mandatory rather than optional, because unsigned metadata is an
              unverifiable claim on a model-reachable field — the exact hole the loop closes
    """
    if not secret:
        raise ValueError("a signing secret is required; unsigned authority metadata is not valid")
    if token.proposal_digest != proposal.proposal_digest:
        raise ValueError("token is not bound to this proposal")
    if decision.proposal_digest != proposal.proposal_digest:
        raise ValueError("decision is not bound to this proposal")
    # constraint: only a token issued for MCP (issue(..., audience=)) can travel in a block;
    # a local token would be spendable both here and by the host
    if token.redemption_channel != "mcp" or not token.mcp_audience:
        raise ValueError("token is not bound to an MCP server; issue it with audience=")

    body: dict[str, Any] = {
        KEY_VERSION: EXTENSION_VERSION,
        KEY_PROPOSAL_DIGEST: proposal.proposal_digest,
        KEY_EVIDENCE_SNAPSHOT: evidence_snapshot_hash(decision, proposal.partition),
        KEY_AUDIT_COMMITTED: decision.route == Route.AUDIT,
        # intent: keep human-approved runs distinguishable downstream, or an outcome
        # reporter will feed them back as autonomous evidence and censor the estimator
        KEY_HUMAN_APPROVED: token.human_approved,
        KEY_CALL_DIGEST: call_digest(proposal.tool_name, proposal.parameters),
        KEY_DECISION_ID: decision.decision_id,
        KEY_TOKEN_ID: token.token_id,
        KEY_POLICY_VERSION: token.policy_version,
        KEY_EXPIRES_AT: token.expires_at.isoformat(),
        KEY_AUDIENCE: token.mcp_audience,
        KEY_REDEMPTION: "server" if token.allowed_effects else "nonce",
    }
    body[KEY_MAC] = _mac(body, secret)
    return body


def attach_meta(params: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, Any]:
    """
    intent: Merge authority metadata into tools/call params
    constraint: MCP requires its own _meta keys on every request, so this preserves foreign
                keys and refuses a collision rather than silently overwriting one
    """
    merged = dict(params)
    existing = dict(merged.get("_meta", {}))
    collisions = set(existing) & set(meta)
    if collisions:
        raise ValueError(f"refusing to overwrite _meta keys: {sorted(collisions)}")
    existing.update(meta)
    merged["_meta"] = existing
    return merged


def _require_bool(value: Any, key: str) -> bool:
    """
    intent: Read a flag as the boolean it is meant to be, not as whatever is truthy
    tradeoff: unreachable for an authentic block, since the MAC covers these fields -- but
              bool("false") is True, so a hand-built params dict would misread intent
              silently rather than fail
    """
    if not isinstance(value, bool):
        raise MetaVerificationError(f"{key} must be a boolean, got {type(value).__name__}")
    return value


def _parse_expiry(raw: Any) -> datetime:
    if not isinstance(raw, str):
        raise MetaVerificationError("expiry is missing or not a string")
    try:
        # constraint: Python 3.10's fromisoformat does not accept a trailing Z
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetaVerificationError(f"unparseable expiry: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise MetaVerificationError("expiry must be timezone-aware")
    return parsed


def verify_call_meta(
    params: Mapping[str, Any],
    tool_name: str,
    arguments: Mapping[str, Any],
    secret: bytes,
    now: datetime | None = None,
    *,
    audience: str,
) -> VerifiedAuthority:
    """
    intent: Establish that this exact call was authorized, before any side effect runs
    method: Version (exactly the one this code speaks), presence, MAC, the call digest
            recomputed from the received tool name and arguments, expiry, and that the
            block names this server (`audience`, the verifying server's principal id)
    effect: Edited arguments, a substituted tool, a forged or stripped MAC, and an expired
            authorization each deny; the function raises rather than returning a verdict
    constraint: Verification is stateless. A verified block still has to be spent at the
                host -- McpServer.call() does that -- or a replay inside the TTL would
                verify again.
    """
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        raise MetaVerificationError("no _meta on the request")
    # constraint: an allowlist of one. Past versions (0.1 had no audience and no
    # redemption) and unknown future ones are refused alike, before anything else is read.
    if meta.get(KEY_VERSION) != EXTENSION_VERSION:
        raise MetaVerificationError(f"unsupported extension version: {meta.get(KEY_VERSION)!r}")
    missing = [key for key in _SIGNED_KEYS if key not in meta]
    if missing:
        raise MetaVerificationError(f"missing authority metadata: {sorted(missing)}")
    # constraint: KEY_MAC is absent from _SIGNED_KEYS because it covers the signed set, not
    # itself, so the loop above cannot catch a stripped MAC. This guard is load-bearing:
    # without it a stripped MAC reaches str(meta[KEY_MAC]) below and raises KeyError instead
    # of MetaVerificationError, which a caller catching the latter would not treat as denial.
    if KEY_MAC not in meta:
        raise MetaVerificationError("authority metadata is unsigned")

    body = {key: meta[key] for key in _SIGNED_KEYS}
    if not isinstance(meta[KEY_MAC], str):
        raise MetaVerificationError("signature must be a string")
    if not hmac.compare_digest(_mac(body, secret), meta[KEY_MAC]):
        raise MetaVerificationError("authority metadata failed authentication")

    if not isinstance(meta[KEY_CALL_DIGEST], str):
        raise MetaVerificationError("call digest must be a string")
    observed = call_digest(tool_name, arguments)
    if not hmac.compare_digest(observed, meta[KEY_CALL_DIGEST]):
        raise MetaVerificationError("call does not match the authorized proposal")

    expires_at = _parse_expiry(meta[KEY_EXPIRES_AT])
    # constraint: a naive clock would raise TypeError from the comparison below, and a
    # server gating on `except MetaVerificationError` would not catch it -- an unhandled
    # crash where a denial belongs. The annotation is not enforced at runtime, so check.
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise MetaVerificationError("the verifying clock must be timezone-aware")
    if current >= expires_at:
        raise MetaVerificationError("authorization expired")
    if meta[KEY_AUDIENCE] != audience:
        raise MetaVerificationError("block is for another server")
    if meta[KEY_REDEMPTION] not in REDEMPTION_MODES:
        raise MetaVerificationError(f"unknown redemption mode: {meta[KEY_REDEMPTION]!r}")

    return VerifiedAuthority(
        proposal_digest=str(meta[KEY_PROPOSAL_DIGEST]),
        evidence_snapshot=(
            None if meta[KEY_EVIDENCE_SNAPSHOT] is None else str(meta[KEY_EVIDENCE_SNAPSHOT])
        ),
        audit_committed=_require_bool(meta[KEY_AUDIT_COMMITTED], KEY_AUDIT_COMMITTED),
        human_approved=_require_bool(meta[KEY_HUMAN_APPROVED], KEY_HUMAN_APPROVED),
        decision_id=str(meta[KEY_DECISION_ID]),
        token_id=str(meta[KEY_TOKEN_ID]),
        policy_version=str(meta[KEY_POLICY_VERSION]),
        expires_at=expires_at,
        audience=str(meta[KEY_AUDIENCE]),
        redemption=str(meta[KEY_REDEMPTION]),
    )


@dataclass(frozen=True)
class CallReceipt:
    """What McpServer.call() returns: the handler's result and the authority it ran on."""

    result: Any
    authority: VerifiedAuthority


class McpServer:
    """
    intent: What a receiving MCP server does with a tools/call before any side effect
    method: verify_call_meta() as this server, then spend the block at the host: a
            `server` block by ExecutionGateway.redeem_remote(), a `nonce` block by
            accept_nonce(); only then the server's own handler runs
    constraint: The server shares the host's token table (one database on one host,
                v3's posture, O-1). Across hosts the same calls become a network service.
    """

    def __init__(self, gateway: ExecutionGateway, principal_id: str, secret: bytes) -> None:
        self.gateway = gateway
        self.principal_id = principal_id
        self.secret = secret

    def call(
        self,
        params: Mapping[str, Any],
        handler: Callable[[Mapping[str, Any]], Any],
        now: datetime | None = None,
    ) -> CallReceipt:
        """
        effect: Returns the handler's result with the VerifiedAuthority it ran on, so a
                caller never re-verifies a block after spending it (a second, stateless
                verification could fail after the effect, e.g. on expiry)
        """
        tool_name, arguments = params["name"], dict(params.get("arguments", {}))
        verified = verify_call_meta(
            params, tool_name, arguments, self.secret, now, audience=self.principal_id,
        )
        digest = call_digest(tool_name, arguments)
        if verified.redemption == "server":
            result = self.gateway.redeem_remote(
                verified.token_id, audience=self.principal_id, call_digest=digest,
                handler=lambda: handler(arguments),
            )
            return CallReceipt(result, verified)
        self.gateway.accept_nonce(verified.token_id, audience=self.principal_id,
                                  call_digest=digest)
        # by design: the nonce path has no handler-state lifecycle. The token stays
        # `issued` (spec §6 rule 3) and the nonce row is the single use, so there is no
        # handler_started/handler_failed to record; an effect-free call has no effect whose
        # outcome could be unknown. Do not add _advance calls here.
        return CallReceipt(handler(arguments), verified)
