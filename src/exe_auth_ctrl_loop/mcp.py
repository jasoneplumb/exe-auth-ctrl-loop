"""Model Context Protocol binding for the execution-authority control loop.

Carries the proposal digest, evidence-snapshot hash, and committed audit flag as
vendor-namespaced metadata on ``tools/call`` requests, so an MCP server can
verify that the exact call it received was authorized before it produces a side
effect.

The metadata is emitted by the host-owned gateway, never by a model. It is
authenticated: an MCP server recomputes the call digest from the tool name and
arguments it actually received and checks the MAC, so a forwarded, replayed, or
edited call fails closed rather than executing on an unverified claim.

Key names follow the MCP ``_meta`` naming rules. The prefix is a third-party
vendor prefix in reverse-DNS notation; prefixes whose second label is ``mcp`` or
``modelcontextprotocol`` are reserved by the specification and are rejected here.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from .authority import AuthorizationToken, Decision, Proposal, Route, digest

EXTENSION_PREFIX = "com.jasoneplumb.exe-auth/"
EXTENSION_VERSION = "0.1"

_LABEL = r"[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_PREFIX_PATTERN = re.compile(rf"^(?:{_LABEL})(?:\.{_LABEL})*/$")
_NAME_PATTERN = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)?$")
_RESERVED_SECOND_LABELS = frozenset({"mcp", "modelcontextprotocol"})


class MetaKeyError(ValueError):
    """A ``_meta`` key violates the MCP naming rules or squats a reserved prefix."""


class MetaVerificationError(PermissionError):
    """Authority metadata is absent, malformed, unauthentic, or does not bind this call."""


def validate_meta_prefix(prefix: str) -> None:
    """Fail closed unless ``prefix`` is a legal, non-reserved third-party vendor prefix."""
    if not _PREFIX_PATTERN.match(prefix):
        raise MetaKeyError(f"invalid _meta prefix: {prefix!r}")
    labels = prefix.rstrip("/").split(".")
    if len(labels) >= 2 and labels[1].lower() in _RESERVED_SECOND_LABELS:
        raise MetaKeyError(f"prefix reserved for MCP use: {prefix!r}")


def meta_key(name: str, prefix: str = EXTENSION_PREFIX) -> str:
    """Build a validated ``_meta`` key. Raises before an illegal key ever reaches the wire."""
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
KEY_MAC = meta_key("mac")

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
)


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


def call_digest(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Digest of the call as the server sees it, so binding needs no shared proposal."""
    return digest({"tool": tool_name, "arguments": dict(arguments)})


def evidence_snapshot_hash(decision: Decision) -> str | None:
    """Hash the evidence identity and version that justified this decision."""
    if decision.evidence_id is None:
        return None
    return digest({"evidence_id": decision.evidence_id, "version": decision.evidence_version})


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
    """Emit signed authority metadata for one ``tools/call`` request.

    The secret is mandatory: unsigned metadata would be an unverifiable assertion,
    and a server that trusted it would grant authority on a model-reachable field.
    """
    if not secret:
        raise ValueError("a signing secret is required; unsigned authority metadata is not valid")
    if token.proposal_digest != proposal.proposal_digest:
        raise ValueError("token is not bound to this proposal")
    if decision.proposal_digest != proposal.proposal_digest:
        raise ValueError("decision is not bound to this proposal")

    body: dict[str, Any] = {
        KEY_VERSION: EXTENSION_VERSION,
        KEY_PROPOSAL_DIGEST: proposal.proposal_digest,
        KEY_EVIDENCE_SNAPSHOT: evidence_snapshot_hash(decision),
        KEY_AUDIT_COMMITTED: decision.route == Route.AUDIT,
        KEY_HUMAN_APPROVED: token.human_approved,
        KEY_CALL_DIGEST: call_digest(proposal.tool_name, proposal.parameters),
        KEY_DECISION_ID: decision.decision_id,
        KEY_TOKEN_ID: token.token_id,
        KEY_POLICY_VERSION: token.policy_version,
        KEY_EXPIRES_AT: token.expires_at.isoformat(),
    }
    body[KEY_MAC] = _mac(body, secret)
    return body


def attach_meta(params: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, Any]:
    """Merge authority metadata into ``tools/call`` params, preserving foreign ``_meta`` keys."""
    merged = dict(params)
    existing = dict(merged.get("_meta", {}))
    collisions = set(existing) & set(meta)
    if collisions:
        raise ValueError(f"refusing to overwrite _meta keys: {sorted(collisions)}")
    existing.update(meta)
    merged["_meta"] = existing
    return merged


def _parse_expiry(raw: Any) -> datetime:
    if not isinstance(raw, str):
        raise MetaVerificationError("expiry is missing or not a string")
    try:
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
) -> VerifiedAuthority:
    """Verify that this exact call was authorized. Raises rather than returning a verdict.

    Checks, in order: metadata present, all signed fields present, MAC authentic,
    the call digest recomputed from the received tool name and arguments matches,
    and the authorization has not expired. Any failure denies execution.
    """
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        raise MetaVerificationError("no _meta on the request")
    missing = [key for key in _SIGNED_KEYS if key not in meta]
    if missing:
        raise MetaVerificationError(f"missing authority metadata: {sorted(missing)}")
    if KEY_MAC not in meta:
        raise MetaVerificationError("authority metadata is unsigned")

    body = {key: meta[key] for key in _SIGNED_KEYS}
    if not hmac.compare_digest(_mac(body, secret), str(meta[KEY_MAC])):
        raise MetaVerificationError("authority metadata failed authentication")
    if meta[KEY_VERSION] != EXTENSION_VERSION:
        raise MetaVerificationError(f"unsupported extension version: {meta[KEY_VERSION]!r}")

    observed = call_digest(tool_name, arguments)
    if not hmac.compare_digest(observed, str(meta[KEY_CALL_DIGEST])):
        raise MetaVerificationError("call does not match the authorized proposal")

    expires_at = _parse_expiry(meta[KEY_EXPIRES_AT])
    if (now or datetime.now(timezone.utc)) >= expires_at:
        raise MetaVerificationError("authorization expired")

    return VerifiedAuthority(
        proposal_digest=str(meta[KEY_PROPOSAL_DIGEST]),
        evidence_snapshot=(
            None if meta[KEY_EVIDENCE_SNAPSHOT] is None else str(meta[KEY_EVIDENCE_SNAPSHOT])
        ),
        audit_committed=bool(meta[KEY_AUDIT_COMMITTED]),
        human_approved=bool(meta[KEY_HUMAN_APPROVED]),
        decision_id=str(meta[KEY_DECISION_ID]),
        token_id=str(meta[KEY_TOKEN_ID]),
        policy_version=str(meta[KEY_POLICY_VERSION]),
        expires_at=expires_at,
    )
