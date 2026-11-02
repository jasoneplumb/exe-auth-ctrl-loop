"""
Intent: Decide whether one operation may run without a human, and issue a capability
        narrow enough that the answer cannot be stretched into a different question
Context: The host owns this module. Both model runtimes are upstream of it and neither can
        reach past it -- providers.py proposes, executor.py requests, this authorizes
Pattern: Fail-closed accumulation -- reasons are collected, and any reason at all withholds
        autonomy. Nothing here grants on the absence of a check.
Future: One process, durable state: evidence since v3 Step 02 and transactional token
        consumption since Step 03 (store.py). A separate gateway service identity remains
        a production requirement (docs/v3/spec.md O-1).
"""

from __future__ import annotations

import json
import math
import random
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence

from .sequential import beta_mixture_lower_bound
from .store import Store, partition_text, time_text

if TYPE_CHECKING:  # these modules import this one
    from .principals import Approval, Principals
    from .tools import HandlerTable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: Any) -> str:
    """
    intent: Give any record a stable identity that changes if any field changes
    method: Canonical JSON -- sorted keys, no whitespace -- then SHA-256
    context: Underpins proposal binding, the evidence snapshot, and the ledger hash chain,
             so equality of digests is what "unmodified" means everywhere in this package
    """
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


class Route(str, Enum):
    AUTONOMOUS = "autonomous"
    AUDIT = "audit"
    HUMAN_APPROVAL = "human_approval"
    CLARIFICATION = "clarification"
    REVISION = "revision"
    DENY = "deny"


def executable_route(route: Route, human_approved: bool) -> bool:
    """
    intent: The one statement of which routes may become a token
    effect: AUTONOMOUS and AUDIT issue unattended; HUMAN_APPROVAL only with an approval;
            DENY, REVISION, and CLARIFICATION never
    """
    return route in {Route.AUTONOMOUS, Route.AUDIT} or (
        human_approved and route == Route.HUMAN_APPROVAL
    )


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
    """
    intent: Name the exact configuration a track record belongs to
    constraint: Twelve fields, and evidence never pools across them. Both models' versions
                AND both prompt versions are in the key, so changing a prompt invalidates a
                record earned under the old one -- evidence from one configuration says
                nothing about another.
    """

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
        """
        intent: Bind an authorization to this proposal and no other
        effect: Every field is in the digest, so an argument edited after the decision
                produces a different digest and the gateway refuses the token
        future: Uses partition.__dict__; asdict() is safer against __slots__, but changing
                it would alter every digest, so it is deferred rather than done quietly
        """
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
    alpha: float = 0.05
    estimator: str = "beta_mixture"

    def __post_init__(self) -> None:
        if self.n_min < 1:
            raise ValueError("n_min must be positive")
        if not 0.0 <= self.audit_rate <= 1.0:
            raise ValueError("audit_rate must be in [0, 1]")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")
        if self.estimator not in ESTIMATORS:
            raise ValueError(f"estimator must be one of {sorted(ESTIMATORS)}")

    def lower_bound(self, successes: int, failures: int) -> float:
        """
        intent: The one place that turns counts into the bound the gate compares
        constraint: `estimator` and `alpha` are part of the policy, so changing either is
                    a policy change and must come with a new policy version; evidence
                    judged under one method is not silently re-read under another.
        effect: `beta_mixture` is valid at every look (docs/v2/statistical-method.md).
                `wilson_legacy` is the v1 fixed-sample bound, kept for comparison only;
                read repeatedly it exceeds its nominal error rate.
        """
        if self.estimator == "wilson_legacy":
            return wilson_lower_bound(successes, successes + failures, self.bound_z)
        return beta_mixture_lower_bound(successes, failures, self.alpha)


ESTIMATORS = frozenset({"beta_mixture", "wilson_legacy"})


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
    partition: PartitionKey
    policy_version: str
    evidence_id: str | None
    evidence_version: int | None
    expires_at: datetime
    human_approved: bool
    # effect: as returned by issue() these describe the token at issue. The current
    # values are in the database: ExecutionGateway.token() or .tokens.
    used: bool = False
    revoked: bool = False
    revocation_reason: str | None = None
    state: str = "issued"
    # v3 Step 05: who asked, under which grant, and on which approval (if any)
    grant_id: str | None = None
    requesting_principal: str | None = None
    approval_id: str | None = None


class ProposalGuard(Protocol):
    """
    intent: Let host-owned policy that is not about evidence veto a proposal at decision
            time -- risk envelopes, budgets, sequence rules (risk.py)
    effect: Any reason a guard returns routes the proposal to DENY, which no signature
            can override
    """

    def proposal_blockers(self, proposal: Proposal) -> tuple[str, ...]: ...


class RedemptionGuard(Protocol):
    """
    intent: Let shared authority state veto a token at the moment it is spent, and learn
            when it was spent
    context: The lifecycle manager and the risk policy implement this; the gateway asks
             every guard inside its consume transaction, so a veto and a consume cannot
             interleave, and calls commit() in the same transaction once the token is
             consumed
    constraint: Must not call back into the gateway. redemption_blockers() is read-only;
                commit() is the one place a guard may record that an effect happened.
                Anything that could refuse the operation belongs in redemption_blockers();
                if commit() raises, the consume is rolled back with it.
    """

    def redemption_blockers(
        self, token: AuthorizationToken, proposal: Proposal
    ) -> tuple[str, ...]: ...

    def commit(self, token: AuthorizationToken, proposal: Proposal) -> None: ...


@dataclass(frozen=True)
class Outcome:
    proposal_id: str
    status: OutcomeStatus
    severe: bool
    assessor: str
    observed_at: datetime
    details_digest: str


class EvidenceStore:
    """
    Intent: Hold one track record per exact configuration, and decide what is allowed to
            enter it
    Pattern: Monotonic versioning -- snapshots are replaced, never mutated, so a decision
            can name the exact evidence version it relied on
    Future: Durable since v3 Step 02. Every version is a row in the append-only
            `evidence_snapshots` table and the current snapshot is the highest version, so
            the version a decision names stays readable after it is superseded.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def put(self, snapshot: EvidenceSnapshot) -> None:
        """
        intent: Replace a partition's snapshot while keeping version history meaningful
        constraint: Versions must strictly increase -- a decision records the version it
                    relied on, and a reused version would make that record ambiguous. The
                    check and the insert are one transaction, so two writers cannot both
                    pass the check with the same version.
        """
        with self.store.transaction():
            current = self.get(snapshot.key)
            if current and snapshot.version <= current.version:
                raise ValueError("evidence version must increase")
            self.store.connection().execute(
                "INSERT INTO evidence_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    partition_text(snapshot.key), snapshot.version, snapshot.evidence_id,
                    snapshot.successes, snapshot.failures,
                    time_text(snapshot.collected_from), time_text(snapshot.collected_until),
                    int(snapshot.valid), int(snapshot.suspended), snapshot.invalidation_reason,
                ),
            )

    def get(self, key: PartitionKey) -> EvidenceSnapshot | None:
        row = self.store.connection().execute(
            "SELECT version, evidence_id, successes, failures, collected_from,"
            " collected_until, valid, suspended, invalidation_reason"
            " FROM evidence_snapshots WHERE partition = ? ORDER BY version DESC LIMIT 1",
            (partition_text(key),),
        ).fetchone()
        if row is None:
            return None
        version, evidence_id, successes, failures, start, until, valid, suspended, reason = row
        return EvidenceSnapshot(
            evidence_id, version, key, successes, failures,
            datetime.fromisoformat(start), datetime.fromisoformat(until),
            bool(valid), bool(suspended), reason,
        )

    def invalidate(self, key: PartitionKey, reason: str) -> None:
        """
        intent: Declare a partition's evidence unusable without deleting the record
        effect: A new version with valid=False. The controller withholds autonomy, and a
                gateway holding this store refuses tokens issued against earlier versions.
        """
        with self.store.transaction():
            current = self.get(key)
            if current is None:
                raise KeyError("partition not found")
            self.put(replace(
                current, version=current.version + 1, valid=False, invalidation_reason=reason,
            ))

    def adjudicate(self, key: PartitionKey, outcome: Outcome, autonomous: bool) -> None:
        """
        intent: Fold one adjudicated outcome into the track record -- or deliberately not
        method: Severe failures suspend before any counting; otherwise only autonomous,
                decisively-adjudicated outcomes update the counts
        effect: This is the selection-bias firewall. Human-approved executions are dropped
                on the floor rather than counted, because a reviewer removes exactly the
                failures the autonomous path would have committed. Counting them would let
                the system infer "I no longer need watching" from data that looks good only
                because someone was watching -- and reviewing harder would earn autonomy
                faster. Discarding real signal is the price of keeping the estimator
                on-policy, and it is intended.
        constraint: PENDING, PARTIAL, and DISPUTED are also dropped -- an ambiguous outcome
                    is not evidence of success or of failure.
        """
        with self.store.transaction():
            current = self.get(key)
            if current is None:
                raise KeyError("partition not found")
            if outcome.severe:
                self.put(replace(
                    current,
                    version=current.version + 1,
                    suspended=True,
                    invalidation_reason="severe_failure",
                ))
                # constraint: nothing in this package ever clears `suspended`. Autonomy is
                # earned over many outcomes and withdrawn by one; restoring it is a human act.
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
    """
    intent: Ask what success rate the evidence actually supports, not what it observed
    method: One-sided Wilson score interval, which stays well behaved at small n and at
            rates near 1.0 where the normal approximation falls apart
    effect: Ten-for-ten returns roughly 0.72, not 1.0. Thin evidence cannot clear a high
            threshold no matter how clean it looks, which is what makes n_min a floor
            rather than the only defence.
    tradeoff: Returns 0.0 for an empty record rather than raising, so an unknown partition
              flows into the same "below threshold" path as a bad one
    constraint: Fixed-sample. Legacy since v2 (`Policy.estimator = "wilson_legacy"`): read
                after every trial it exceeds its nominal error rate, so it no longer gates
                new autonomy by default. See docs/v2/statistical-method.md.
    """
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    center = p + z2 / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total)
    return max(0.0, (center - margin) / (1 + z2 / total))


class AuthorityController:
    """
    Intent: Answer one question -- may this exact operation run right now without a human?
    Context: Called by executor.py immediately before each tool invocation, never once per
            plan. An approval earned earlier in a run buys nothing here.
    Pattern: Deterministic given its inputs, apart from the audit draw, so a decision can be
            replayed from the ledger and re-derived.
    """

    def __init__(
        self,
        evidence: EvidenceStore,
        policy: Policy,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = utcnow,
        guards: Sequence[ProposalGuard] = (),
    ) -> None:
        self.evidence = evidence
        self.policy = policy
        self.rng = rng or random.SystemRandom()
        self.clock = clock
        self.guards = tuple(guards)

    def evaluate(self, proposal: Proposal) -> Decision:
        """
        intent: Route one proposal, and record why in a form that survives the decision
        method: Accumulate reason codes, then grant autonomy only if none were raised
        effect: Reasons are additive and never cancel, so a new check can only narrow what
                is authorized. Adding one cannot accidentally widen authority.
        context: The returned Decision is what the gateway binds a token to and what the
                 ledger records; nothing downstream re-derives the verdict.
        """
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
        # constraint: scope and budget are host policy, like prohibited effects, so a
        # guard veto is a DENY -- not a request for a human, who cannot widen an envelope
        for guard in self.guards:
            vetoes = guard.proposal_blockers(proposal)
            if vetoes:
                route = Route.DENY
                reasons.extend(v for v in vetoes if v not in reasons)

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
            lower = self.policy.lower_bound(snapshot.successes, snapshot.failures)
            if lower < required:
                reasons.append("BOUND_BELOW_POLICY")

        # constraint: any reason at all withholds autonomy. There is no weighing and no
        # threshold of severity -- the check is `bool(reasons)`, so a check that fires for
        # an unforeseen reason still fails closed.
        blocking = bool(reasons)
        audit_draw: float | None = None
        audit_probability = 0.0
        if route not in {Route.DENY, Route.REVISION, Route.CLARIFICATION} and not blocking:
            audit_probability = self.policy.audit_rate
            # intent: draw now, before the operation runs and before any outcome exists,
            # and carry the draw into the Decision the ledger chains. Selecting audits
            # afterwards -- or being able to revise the selection -- would let an operator
            # or a strategic agent steer scrutiny away from the failures. Committing first
            # keeps audit selection independent of results -- given a trusted RNG and a
            # controller that commits the draw before acting (docs/v2/audit-commitment.md).
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
    """
    Intent: Be the only path from an authorization to a real side effect
    Context: Holds the handler call. Neither model runtime can reach a handler except by
            presenting a token this gateway issued.
    Pattern: Capability, not permission check -- the token names one tool, one effect set,
            one proposal digest, one use, and a short expiry.
    Future: Since v3 Step 03 tokens are rows of the `tokens` table and are consumed by one
            database transaction, so single use holds across threads, processes on one
            host, and restarts (docs/v3/spec.md §3). Across hosts they must still be signed
            or held server-side (Step 07 for MCP).
    """

    def __init__(
        self,
        clock: Callable[[], datetime] = utcnow,
        evidence: EvidenceStore | None = None,
        current_policy: Callable[[], Policy] | None = None,
        guards: Sequence[RedemptionGuard] = (),
        *,
        store: Store,
        handlers: HandlerTable,
        principals: Principals,
    ) -> None:
        """
        intent: Optionally bind the gateway to the live authority state it should re-check
        effect: With `evidence`, a token dies with its partition's suspension or evidence
                invalidation. With `current_policy`, it dies with a policy version change
                or a newly prohibited effect. Each guard (e.g. LifecycleManager) can veto
                on its own grounds. Without any of them the gateway behaves as v0.2.0 did:
                expiry and explicit revocation only.
        constraint: `store` is required and holds the tokens. Guards that write (a budget
                    charge) should use the same Store, so their write commits with the
                    consume; on another Store it commits separately.
        constraint: `handlers` is required and is the only route to a side effect
                    (docs/v3/spec.md §4). It is given to the gateway and to nothing on the
                    agent side; execute() picks the handler from the consumed token.
        constraint: `principals` is required (spec §8). It must use the same Store, so a
                    token, its grant, and its approval are checked and recorded together.
        """
        if principals.store is not store:
            raise ValueError("the gateway and its principals must share one Store")
        self.clock = clock
        self.evidence = evidence
        self.current_policy = current_policy
        self.guards = tuple(guards)
        self.store = store
        self.handlers = handlers
        self.principals = principals

    @property
    def tokens(self) -> dict[str, AuthorizationToken]:
        """Every token, with its current state, read from the database (a fresh dict)."""
        rows = self.store.connection().execute(f"SELECT {_TOKEN_COLUMNS} FROM tokens").fetchall()
        return {row[0]: _token(row) for row in rows}

    def token(self, token_id: str) -> AuthorizationToken | None:
        row = self.store.connection().execute(
            f"SELECT {_TOKEN_COLUMNS} FROM tokens WHERE token_id = ?", (token_id,)
        ).fetchone()
        return None if row is None else _token(row)

    def effect_unknown(self) -> list[str]:
        """
        intent: The tokens an operator must triage by hand (spec §3 rule 3)
        effect: A token in `handler_started` was consumed and its handler was entered but
                never reported back -- the process stopped mid-effect. Its effect may or may
                not have happened, and it is never re-run.
        context: With consumed_unrun() this covers every residue a crash can leave after a
                 consume commits. Both also list operations still in flight in a live
                 process, so read them when no redemption is running, or twice.
        """
        return self._in_state("handler_started")

    def consumed_unrun(self) -> list[str]:
        """
        intent: The authorized operations that were spent but never started
        effect: A token left in `consumed` had its consume committed, but the process
                stopped before `handler_started` was written, which happens before the
                handler is called. So the handler was not entered and the effect did not
                happen: unlike effect_unknown(), this is known. The token is spent and is
                never re-run; the operation needs a fresh decision.
        """
        return self._in_state("consumed")

    def _in_state(self, state: str) -> list[str]:
        return [
            row[0] for row in self.store.connection().execute(
                "SELECT token_id FROM tokens WHERE state = ? ORDER BY rowid", (state,)
            )
        ]

    def issue(
        self,
        decision: Decision,
        proposal: Proposal,
        policy: Policy,
        *,
        principal_id: str,
        grant_id: str,
        approval: Approval | None = None,
    ) -> AuthorizationToken:
        """
        intent: Mint a capability scoped to exactly the decision that justified it
        constraint: AUTONOMOUS and AUDIT issue unattended -- an audit is an autonomous
                    execution that was also selected, before the outcome, for independent
                    labelling afterwards (docs/v2/audit-commitment.md). HUMAN_APPROVAL
                    needs a real approval; DENY, REVISION, and CLARIFICATION cannot be
                    overridden at all -- they require a corrected proposal or a policy
                    change, not a signature.
        constraint: A human signature overrides a route, never a prohibition: prohibited
                    effects are refused here even with human_approved=True, and if a live
                    policy provider is attached the policy passed in must be the current one
        constraint: One decision, one token, ever (spec §3 rule 4): a second request for a
                    decision that already has a token row is refused whatever that row's
                    state, here and by the unique constraint on `decision_id`
        constraint: Only a committed decision issues (spec §3 rule 1): its id must have a
                    row in `decisions`, written by EventLedger.commit_decision with its
                    ledger event, carrying the same proposal digest and route. A Decision
                    built by hand, or one altered after it was committed, is refused.
        effect: The token names its partition and the evidence id and version it was
                judged on. execute() re-reads the live state against them, so a grant
                is only as good as its justification still is at the moment of use.
        effect: Returns the token as issued. Its state afterwards is in the database:
                `gateway.token(token_id)` or `gateway.tokens`.
        constraint: The requesting principal must hold `grant_id`, every grant in its chain
                    must be live, and every scope in the chain must admit this operation
                    (spec §8). The token records the grant and the principal, so
                    Principals.lineage(token_id) answers "on whose authority".
        constraint: An `approval` replaces v0.3.1's `human_approved=True`. It must be an
                    active human's valid signature over this proposal's digest and
                    partition, unexpired and unrescinded, and it is used once: it is
                    recorded in this transaction and the token's approval_id is unique.
        """
        human_approved = approval is not None
        if not executable_route(decision.route, human_approved):
            raise PermissionError("decision does not authorize execution")
        if decision.proposal_digest != proposal.proposal_digest:
            raise PermissionError("decision is not bound to this proposal")
        if proposal.requested_effects & policy.prohibited_effects:
            raise PermissionError("human approval cannot override a prohibited effect")
        if self.current_policy is not None and self.current_policy().version != policy.version:
            raise PermissionError("policy is not the current policy")
        token = AuthorizationToken(
            token_id=secrets.token_hex(24),
            decision_id=decision.decision_id,
            proposal_digest=proposal.proposal_digest,
            allowed_tool=proposal.tool_name,
            allowed_effects=proposal.requested_effects,
            partition=proposal.partition,
            policy_version=policy.version,
            evidence_id=decision.evidence_id,
            evidence_version=decision.evidence_version,
            expires_at=self.clock() + policy.token_ttl,
            human_approved=human_approved,
        )
        with self.store.transaction() as conn:
            committed = conn.execute(
                "SELECT proposal_digest, route FROM decisions WHERE decision_id = ?",
                (decision.decision_id,),
            ).fetchone()
            if committed is None:
                raise PermissionError("decision was not committed to the ledger")
            if committed != (decision.proposal_digest, decision.route.value):
                raise PermissionError("decision differs from its committed record")
            if conn.execute(
                "SELECT 1 FROM tokens WHERE decision_id = ?", (decision.decision_id,)
            ).fetchone():
                raise PermissionError("decision already has a token")
            grant_reasons = self.principals.grant_blockers(grant_id, principal_id, proposal)
            if grant_reasons:
                raise PermissionError(f"grant does not cover this: {', '.join(grant_reasons)}")
            if approval is not None:
                approval_reasons = self.principals.approval_blockers(approval, proposal)
                if approval_reasons:
                    raise PermissionError(f"approval refused: {', '.join(approval_reasons)}")
                if conn.execute(
                    "SELECT 1 FROM tokens WHERE approval_id = ?", (approval.approval_id,)
                ).fetchone():
                    raise PermissionError("approval already used")
                self.principals.record_approval(approval)
            token = replace(
                token, grant_id=grant_id, requesting_principal=principal_id,
                approval_id=approval.approval_id if approval is not None else None,
            )
            conn.execute(
                f"INSERT INTO tokens ({_TOKEN_COLUMNS}) VALUES ({_TOKEN_PLACEHOLDERS})",
                (
                    token.token_id, token.decision_id, token.proposal_digest,
                    token.allowed_tool, json.dumps(sorted(token.allowed_effects)),
                    partition_text(token.partition), token.policy_version, token.evidence_id,
                    token.evidence_version, time_text(token.expires_at),
                    int(token.human_approved), "issued", None, token.grant_id,
                    token.requesting_principal, token.approval_id,
                ),
            )
        return token

    def execute(self, token_id: str, proposal: Proposal) -> Any:
        """
        intent: Spend a capability once, on the operation it was issued for
        method: One transaction. Consume first, with `UPDATE ... WHERE state = 'issued'`
                affecting exactly one row; then re-check identity, liveness, and scope at
                the moment of use; then resolve the handler for the token's tool and
                validate the arguments; then let guards settle (budget charges). Any
                refusal raises inside the transaction, which rolls the consume back.
        constraint: No caller supplies the handler. It is the HandlerTable's entry for
                    `token.allowed_tool`, the tool the authorization names (spec §4).
        effect: Every mismatch raises PermissionError, so a caller cannot distinguish
                "expired" from "wrong tool" by control flow and act on the difference
        constraint: The linearization point is the commit of that transaction. The
                    conditional update is the single-use guard: a second redeemer, in this
                    process or another, either waits on the write lock and then finds no
                    row in state `issued`, or is refused by the transition trigger. No
                    Python-side check of the state stands in for it.
        constraint: The handler runs after the commit, between `handler_started` and
                    `handler_completed` or `handler_failed`, each written in its own
                    transaction. A failing handler does not refund the token; a process
                    that stops inside the handler leaves `handler_started`, which
                    effect_unknown() reports and nothing re-runs.
        """
        with self.store.transaction() as conn:
            token = self.token(token_id)
            consumed = conn.execute(
                "UPDATE tokens SET state = 'consumed' WHERE token_id = ? AND state = 'issued'",
                (token_id,),
            ).rowcount
            if token is None or consumed != 1:
                raise PermissionError("missing, consumed, or revoked token")
            if self.clock() >= token.expires_at:
                raise PermissionError("expired token")
            if token.proposal_digest != proposal.proposal_digest:
                raise PermissionError("proposal changed after authorization")
            if token.allowed_tool != proposal.tool_name:
                raise PermissionError("tool exceeds authorization")
            if not proposal.requested_effects <= token.allowed_effects:
                raise PermissionError("effect exceeds authorization")
            blockers = self.redemption_blockers(token, proposal)
            if blockers:
                raise PermissionError(f"authorization withdrawn: {', '.join(blockers)}")
            # imported here: tools.py imports risk.py, which imports this module
            from .tools import ToolValidationError

            try:
                call = self.handlers.resolve(token.allowed_tool, proposal.parameters)
            except ToolValidationError as exc:  # bad arguments, or no handler for the tool
                raise PermissionError(f"tool not executable: {exc}") from exc
            # effect: budgets are charged in the consume transaction, so a charge and a
            # consume commit together or not at all. A guard's commit() that raises now
            # rolls the consume back too, instead of leaving a spent token unsettled.
            for guard in self.guards:
                guard.commit(token, proposal)
        self._advance(token_id, "consumed", "handler_started")
        try:
            result = call()
        except BaseException:
            self._advance(token_id, "handler_started", "handler_failed")
            raise
        self._advance(token_id, "handler_started", "handler_completed")
        return result

    def redemption_blockers(
        self, token: AuthorizationToken, proposal: Proposal
    ) -> tuple[str, ...]:
        """
        intent: Everything about the world, as opposed to the token, that can refuse it
        method: Policy identity, then evidence identity and health, then each guard.
                Collected rather than short-circuited so a denial names every reason.
        constraint: Ordinary evidence increments do not appear here. A new label on the
                    same evidence record, with the partition still valid, unsuspended, and
                    (if a lifecycle guard is attached) still AUTONOMOUS, leaves the token
                    good. What does withdraw it: suspension, declared invalidation, the
                    evidence record being replaced or removed, a policy version change,
                    a newly prohibited effect, and any guard veto.
        effect: A human approval given in full view of a suspension or invalidation (the
                decision saw the same snapshot version) is honored; one given before it
                is not. Human approval is informed consent to the current state, not a
                bearer right that survives a change of state.
        """
        reasons: list[str] = []
        if self.current_policy is not None:
            policy = self.current_policy()
            if policy.version != token.policy_version:
                reasons.append("POLICY_CHANGED")
            if proposal.requested_effects & policy.prohibited_effects:
                reasons.append("PROHIBITED_EFFECT")
        if self.evidence is not None:
            snapshot = self.evidence.get(token.partition)
            if snapshot is not None and (snapshot.suspended or not snapshot.valid):
                informed = token.human_approved and token.evidence_version == snapshot.version
                if not informed:
                    # both named when both hold: an incident review should see that the
                    # evidence was independently invalid, not only that it was suspended
                    if snapshot.suspended:
                        reasons.append("PARTITION_SUSPENDED")
                    if not snapshot.valid:
                        reasons.append("EVIDENCE_INVALID")
            # constraint: this catches a record removed or replaced from outside (a new
            # evidence_id). Shadow-managed partitions keep one evidence_id for life, so
            # for them the live signals are the suspended/valid flags above, which the
            # lifecycle sets on suspension and on retirement.
            if token.evidence_id is not None and not token.human_approved:
                if snapshot is None or snapshot.evidence_id != token.evidence_id:
                    reasons.append("EVIDENCE_WITHDRAWN")
        # constraint: authority from the principal side is re-read too: a revoked, expired,
        # or narrowed grant chain, a deactivated party, or a rescinded approval refuses the
        # token here, even before v3 Step 06 revokes tokens at the moment of the change
        if token.grant_id is not None and token.requesting_principal is not None:
            reasons.extend(
                self.principals.grant_blockers(token.grant_id, token.requesting_principal,
                                               proposal)
            )
        if token.approval_id is not None and self.principals.rescinded(token.approval_id):
            reasons.append("APPROVAL_RESCINDED")
        for guard in self.guards:
            reasons.extend(guard.redemption_blockers(token, proposal))
        return tuple(dict.fromkeys(reasons))

    def revoke(self, token_id: str, reason: str = "revoked") -> None:
        """
        intent: Withdraw one grant explicitly, e.g. when a human approval is rescinded
        constraint: One transaction, serialized with every consume by the write lock, so a
                    revoke and a redeem are ordered and never interleave. Revoking a token
                    that is no longer `issued` changes nothing: there is no grant left.
        """
        with self.store.transaction() as conn:
            if self.token(token_id) is None:
                raise KeyError(token_id)
            conn.execute(
                "UPDATE tokens SET state = 'revoked', revocation_reason = ?"
                " WHERE token_id = ? AND state = 'issued'",
                (reason, token_id),
            )

    def _advance(self, token_id: str, current: str, target: str) -> None:
        """
        constraint: Loud on a mismatch. A transition that finds the token in another state
                    means the state machine and this call disagree, and a silent no-op
                    would leave effect_unknown() or consumed_unrun() reporting the wrong
                    thing.
        """
        with self.store.transaction() as conn:
            moved = conn.execute(
                "UPDATE tokens SET state = ? WHERE token_id = ? AND state = ?",
                (target, token_id, current),
            ).rowcount
            if moved != 1:
                raise RuntimeError(
                    f"token {token_id} is not in state {current!r}; cannot move to {target!r}"
                )


_TOKEN_COLUMNS = (
    "token_id, decision_id, proposal_digest, allowed_tool, allowed_effects, partition,"
    " policy_version, evidence_id, evidence_version, expires_at, human_approved, state,"
    " revocation_reason, grant_id, requesting_principal, approval_id"
)
_TOKEN_PLACEHOLDERS = ", ".join("?" for _ in _TOKEN_COLUMNS.split(","))


def _token(row: tuple[Any, ...]) -> AuthorizationToken:
    (token_id, decision_id, proposal_digest, allowed_tool, effects, partition, policy_version,
     evidence_id, evidence_version, expires_at, human_approved, state, reason, grant_id,
     requesting_principal, approval_id) = row
    return AuthorizationToken(
        token_id=token_id,
        decision_id=decision_id,
        proposal_digest=proposal_digest,
        allowed_tool=allowed_tool,
        allowed_effects=frozenset(json.loads(effects)),
        partition=PartitionKey(**json.loads(partition)),
        policy_version=policy_version,
        evidence_id=evidence_id,
        evidence_version=evidence_version,
        expires_at=datetime.fromisoformat(expires_at),
        human_approved=bool(human_approved),
        used=state in {"consumed", "handler_started", "handler_completed", "handler_failed"},
        revoked=state == "revoked",
        revocation_reason=reason,
        state=state,
        grant_id=grant_id,
        requesting_principal=requesting_principal,
        approval_id=approval_id,
    )
