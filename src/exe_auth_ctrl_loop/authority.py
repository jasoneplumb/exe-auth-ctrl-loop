from __future__ import annotations

import json
import math
import random
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Callable, Mapping


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


class Route(str, Enum):
    AUTONOMOUS = "autonomous"
    AUDIT = "audit"
    HUMAN_APPROVAL = "human_approval"
    CLARIFICATION = "clarification"
    REVISION = "revision"
    DENY = "deny"


class ProposalReadiness(str, Enum):
    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"
    EXECUTABLE = "executable"


class OutcomeStatus(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    DISPUTED = "disputed"
    ACCEPTABLE = "acceptable"
    UNACCEPTABLE = "unacceptable"


@dataclass(frozen=True)
class PartitionKey:
    proposal_provider: str
    proposal_model_version: str
    proposal_prompt_version: str
    execution_provider: str
    execution_model_version: str
    execution_prompt_version: str
    tool_version: str
    policy_version: str
    environment_version: str
    task_category: str
    confidence_bin: str
    risk_class: str


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    intent: str
    tool_name: str
    parameters: Mapping[str, Any]
    confidence: float
    requested_effects: frozenset[str]
    partition: PartitionKey
    proposer: str
    readiness: ProposalReadiness = ProposalReadiness.EXECUTABLE
    preconditions: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if not self.proposal_id or not self.tool_name:
            raise ValueError("proposal_id and tool_name are required")

    @property
    def proposal_digest(self) -> str:
        return digest({
            "id": self.proposal_id,
            "intent": self.intent,
            "tool_name": self.tool_name,
            "parameters": self.parameters,
            "confidence": self.confidence,
            "effects": sorted(self.requested_effects),
            "partition": self.partition.__dict__,
            "proposer": self.proposer,
            "readiness": self.readiness.value,
            "preconditions": self.preconditions,
            "success_criteria": self.success_criteria,
            "assumptions": self.assumptions,
            "unresolved_questions": self.unresolved_questions,
        })


@dataclass(frozen=True)
class ProposalBundle:
    bundle_id: str
    intent: str
    readiness: ProposalReadiness
    proposals: tuple[Proposal, ...]
    summary: str
    missing_information: tuple[str, ...] = ()

    @property
    def bundle_digest(self) -> str:
        return digest({
            "bundle_id": self.bundle_id,
            "intent": self.intent,
            "readiness": self.readiness.value,
            "proposal_digests": [p.proposal_digest for p in self.proposals],
            "summary": self.summary,
            "missing_information": self.missing_information,
        })


@dataclass(frozen=True)
class EvidenceSnapshot:
    evidence_id: str
    version: int
    key: PartitionKey
    successes: int
    failures: int
    collected_from: datetime
    collected_until: datetime
    valid: bool = True
    suspended: bool = False
    invalidation_reason: str | None = None

    @property
    def n(self) -> int:
        return self.successes + self.failures


@dataclass(frozen=True)
class Policy:
    version: str
    minimum_success_by_risk: Mapping[str, float]
    n_min: int
    max_evidence_age: timedelta
    audit_rate: float
    prohibited_effects: frozenset[str] = frozenset()
    token_ttl: timedelta = timedelta(minutes=2)
    bound_z: float = 1.96

    def __post_init__(self) -> None:
        if self.n_min < 1:
            raise ValueError("n_min must be positive")
        if not 0.0 <= self.audit_rate <= 1.0:
            raise ValueError("audit_rate must be in [0, 1]")


@dataclass(frozen=True)
class Decision:
    decision_id: str
    proposal_id: str
    proposal_digest: str
    evidence_id: str | None
    evidence_version: int | None
    lower_bound: float | None
    required_bound: float
    route: Route
    reason_codes: tuple[str, ...]
    audit_probability: float
    audit_draw: float | None
    decided_at: datetime


@dataclass
class AuthorizationToken:
    token_id: str
    decision_id: str
    proposal_digest: str
    allowed_tool: str
    allowed_effects: frozenset[str]
    policy_version: str
    evidence_id: str | None
    evidence_version: int | None
    expires_at: datetime
    human_approved: bool
    used: bool = False
    revoked: bool = False


@dataclass(frozen=True)
class Outcome:
    proposal_id: str
    status: OutcomeStatus
    severe: bool
    assessor: str
    observed_at: datetime
    details_digest: str


class EvidenceStore:
    def __init__(self) -> None:
        self._items: dict[PartitionKey, EvidenceSnapshot] = {}

    def put(self, snapshot: EvidenceSnapshot) -> None:
        current = self._items.get(snapshot.key)
        if current and snapshot.version <= current.version:
            raise ValueError("evidence version must increase")
        self._items[snapshot.key] = snapshot

    def get(self, key: PartitionKey) -> EvidenceSnapshot | None:
        return self._items.get(key)

    def adjudicate(self, key: PartitionKey, outcome: Outcome, autonomous: bool) -> None:
        current = self._items.get(key)
        if current is None:
            raise KeyError("partition not found")
        if outcome.severe:
            self.put(replace(
                current,
                version=current.version + 1,
                suspended=True,
                invalidation_reason="severe_failure",
            ))
            return
        if not autonomous or outcome.status not in {
            OutcomeStatus.ACCEPTABLE,
            OutcomeStatus.UNACCEPTABLE,
        }:
            return
        self.put(replace(
            current,
            version=current.version + 1,
            successes=current.successes + int(outcome.status == OutcomeStatus.ACCEPTABLE),
            failures=current.failures + int(outcome.status == OutcomeStatus.UNACCEPTABLE),
            collected_until=outcome.observed_at,
        ))


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    center = p + z2 / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total)
    return max(0.0, (center - margin) / (1 + z2 / total))


class AuthorityController:
    def __init__(
        self,
        evidence: EvidenceStore,
        policy: Policy,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.evidence = evidence
        self.policy = policy
        self.rng = rng or random.SystemRandom()
        self.clock = clock

    def evaluate(self, proposal: Proposal) -> Decision:
        now = self.clock()
        reasons: list[str] = []
        snapshot = self.evidence.get(proposal.partition)
        required = self.policy.minimum_success_by_risk.get(
            proposal.partition.risk_class, 1.0
        )
        lower: float | None = None
        route = Route.HUMAN_APPROVAL

        if proposal.readiness == ProposalReadiness.DRAFT:
            route = Route.REVISION
            reasons.append("PROPOSAL_DRAFT")
        elif proposal.readiness == ProposalReadiness.NEEDS_CLARIFICATION:
            route = Route.CLARIFICATION
            reasons.append("PROPOSAL_NEEDS_CLARIFICATION")

        if proposal.unresolved_questions:
            route = Route.CLARIFICATION
            reasons.append("UNRESOLVED_QUESTIONS")
        if proposal.partition.policy_version != self.policy.version:
            reasons.append("POLICY_VERSION_MISMATCH")
        if proposal.requested_effects & self.policy.prohibited_effects:
            route = Route.DENY
            reasons.append("PROHIBITED_EFFECT")

        if snapshot is None:
            reasons.append("NO_EXACT_EVIDENCE")
        else:
            if not snapshot.valid:
                reasons.append("EVIDENCE_INVALID")
            if snapshot.suspended:
                reasons.append("PARTITION_SUSPENDED")
            if now - snapshot.collected_until > self.policy.max_evidence_age:
                reasons.append("EVIDENCE_STALE")
            if snapshot.n < self.policy.n_min:
                reasons.append("EVIDENCE_IMMATURE")
            if snapshot.key != proposal.partition:
                reasons.append("PARTITION_MISMATCH")
            lower = wilson_lower_bound(snapshot.successes, snapshot.n, self.policy.bound_z)
            if lower < required:
                reasons.append("BOUND_BELOW_POLICY")

        blocking = bool(reasons)
        audit_draw: float | None = None
        audit_probability = 0.0
        if route not in {Route.DENY, Route.REVISION, Route.CLARIFICATION} and not blocking:
            audit_probability = self.policy.audit_rate
            audit_draw = self.rng.random()
            route = Route.AUDIT if audit_draw < audit_probability else Route.AUTONOMOUS
            reasons.append(
                "RANDOM_AUDIT" if route == Route.AUDIT else "AUTHORITY_SUFFICIENT"
            )

        return Decision(
            decision_id=secrets.token_hex(12),
            proposal_id=proposal.proposal_id,
            proposal_digest=proposal.proposal_digest,
            evidence_id=snapshot.evidence_id if snapshot else None,
            evidence_version=snapshot.version if snapshot else None,
            lower_bound=lower,
            required_bound=required,
            route=route,
            reason_codes=tuple(reasons),
            audit_probability=audit_probability,
            audit_draw=audit_draw,
            decided_at=now,
        )


class ExecutionGateway:
    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self.clock = clock
        self.tokens: dict[str, AuthorizationToken] = {}

    def issue(
        self,
        decision: Decision,
        proposal: Proposal,
        policy: Policy,
        human_approved: bool = False,
    ) -> AuthorizationToken:
        human_overridable = decision.route in {Route.HUMAN_APPROVAL, Route.AUDIT}
        allowed = decision.route == Route.AUTONOMOUS or (
            human_approved and human_overridable
        )
        if not allowed:
            raise PermissionError("decision does not authorize execution")
        if decision.proposal_digest != proposal.proposal_digest:
            raise PermissionError("decision is not bound to this proposal")
        token = AuthorizationToken(
            token_id=secrets.token_hex(24),
            decision_id=decision.decision_id,
            proposal_digest=proposal.proposal_digest,
            allowed_tool=proposal.tool_name,
            allowed_effects=proposal.requested_effects,
            policy_version=policy.version,
            evidence_id=decision.evidence_id,
            evidence_version=decision.evidence_version,
            expires_at=self.clock() + policy.token_ttl,
            human_approved=human_approved,
        )
        self.tokens[token.token_id] = token
        return token

    def execute(
        self,
        token_id: str,
        proposal: Proposal,
        adapter: Callable[[Proposal], Any],
    ) -> Any:
        token = self.tokens.get(token_id)
        if token is None or token.used or token.revoked:
            raise PermissionError("missing, consumed, or revoked token")
        if self.clock() >= token.expires_at:
            raise PermissionError("expired token")
        if token.proposal_digest != proposal.proposal_digest:
            raise PermissionError("proposal changed after authorization")
        if token.allowed_tool != proposal.tool_name:
            raise PermissionError("tool exceeds authorization")
        if not proposal.requested_effects <= token.allowed_effects:
            raise PermissionError("effect exceeds authorization")
        token.used = True  # Production: atomically consume before invocation.
        return adapter(proposal)

    def revoke(self, token_id: str) -> None:
        self.tokens[token_id].revoked = True
