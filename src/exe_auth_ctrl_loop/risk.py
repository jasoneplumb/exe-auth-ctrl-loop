"""
Intent: Make what an operation *does* -- its amount, its recipient, its effects, and its
        place in a sequence -- a host-owned policy question, separate from the evidence
        question of whether this configuration has earned autonomy
Context: The 12-field partition key carries a risk class, and evidence never pools across
        classes. This module is where the host decides which class an operation is in,
        from its arguments and the registry, so a model cannot label a large refund "low"
        and borrow the low-risk track record (docs/v2/risk-envelopes.md).
Pattern: Envelopes, not key explosion. An envelope is a versioned predicate over the
        arguments of one tool; the lowest-ranked envelope that admits the arguments names
        the risk class. Budgets charge at redemption, under the gateway's lock.
Future: In-process. Charges live in this object's memory; the guarantee is per policy
        instance per process, and production needs the same accounting in a durable store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .authority import AuthorizationToken, PartitionKey, Proposal


@dataclass(frozen=True)
class Bound:
    """A numeric argument must lie within [minimum, maximum]. Booleans are not numbers."""

    field: str
    maximum: float | None = None
    minimum: float | None = None

    def admits(self, parameters: Mapping[str, Any]) -> bool:
        value = parameters.get(self.field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        return True


@dataclass(frozen=True)
class OneOf:
    """An argument must be one of a fixed set of values, e.g. an allow-listed recipient."""

    field: str
    values: frozenset[str]

    def admits(self, parameters: Mapping[str, Any]) -> bool:
        value = parameters.get(self.field)
        return isinstance(value, str) and value in self.values


Rule = Bound | OneOf


@dataclass(frozen=True)
class RiskEnvelope:
    """
    intent: One tool, one risk class, and the exact argument region that class covers
    constraint: `rank` orders envelopes for the same tool; classification picks the lowest
                rank whose rules all admit the arguments. Arguments no envelope admits have
                no class, and an operation with no class is denied, never defaulted.
    """

    tool_name: str
    risk_class: str
    rank: int
    allowed_effects: frozenset[str]
    rules: tuple[Rule, ...] = ()

    def admits(self, parameters: Mapping[str, Any]) -> bool:
        return all(rule.admits(parameters) for rule in self.rules)


@dataclass(frozen=True)
class SequenceBudget:
    """
    intent: Cap what a run of individually acceptable operations can add up to
    constraint: `scope` is "global", "partition", or "parameter:<name>" (e.g. per
                recipient). `metric` names a numeric argument to sum; None counts
                operations only. A budget with neither limit is a configuration error.
    """

    budget_id: str
    tool_names: frozenset[str]
    scope: str = "partition"
    metric: str | None = None
    limit: float | None = None
    max_operations: int | None = None

    def __post_init__(self) -> None:
        if self.limit is None and self.max_operations is None:
            raise ValueError("a budget needs a limit or max_operations")
        if self.limit is not None and self.metric is None:
            raise ValueError("a metric limit needs a metric")
        if self.scope not in {"global", "partition"} and not self.scope.startswith("parameter:"):
            raise ValueError("scope must be global, partition, or parameter:<name>")

    def covers(self, tool_name: str) -> bool:
        return not self.tool_names or tool_name in self.tool_names

    def scope_key(self, proposal: Proposal) -> Any:
        """None when a parameter scope names an argument the proposal lacks; callers deny."""
        if self.scope == "global":
            return "*"
        if self.scope == "partition":
            return proposal.partition
        return proposal.parameters.get(self.scope.removeprefix("parameter:"))

    def amount(self, proposal: Proposal) -> float:
        if self.metric is None:
            return 0.0
        value = proposal.parameters.get(self.metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"budget {self.budget_id}: {self.metric} is not numeric")
        return float(value)


@dataclass
class _Charges:
    total: float = 0.0
    count: int = 0


@dataclass
class RiskPolicy:
    """
    intent: The host's answer to "what is this operation, and may it happen now, given
            what already happened" -- independent of any track record
    context: Attached to AuthorityController as a ProposalGuard, so a scope or budget
             violation routes to DENY; and to ExecutionGateway as a RedemptionGuard, so
             the same checks run again under the consume lock against the policy that is
             current *then*, and budgets are charged only when a side effect is committed.
    constraint: Classification reads arguments and the registry-derived tool name only.
                It never reads the model's confidence, its stated risk class, or any
                proposal text. A proposal whose partition names a different class than
                this policy derives is denied as mislabeled, not re-filed.
    effect: Decision-time checks project a charge; redemption-time checks re-project
            against charges committed since. Tokens do not reserve budget, so several
            decisions may each look affordable and only the redemptions that fit commit.
            That is the in-process guarantee: committed charges never exceed a limit.
    """

    version: str
    envelopes: tuple[RiskEnvelope, ...] = ()
    budgets: tuple[SequenceBudget, ...] = ()
    conflicts: frozenset[tuple[str, str]] = frozenset()
    _charges: dict[tuple[str, Any], _Charges] = field(default_factory=dict, repr=False)
    _history: dict[PartitionKey, list[str]] = field(default_factory=dict, repr=False)

    def update(
        self,
        version: str,
        envelopes: tuple[RiskEnvelope, ...],
        budgets: tuple[SequenceBudget, ...] = (),
        conflicts: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        """
        intent: Replace the rules while keeping what has already been charged
        effect: A rollback to a stricter version takes effect at the next decision and
                the next redemption; outstanding tokens are re-checked against it
        """
        self.version = version
        self.envelopes = envelopes
        self.budgets = budgets
        self.conflicts = conflicts

    def classify(self, tool_name: str, parameters: Mapping[str, Any]) -> RiskEnvelope | None:
        candidates = sorted(
            (e for e in self.envelopes if e.tool_name == tool_name), key=lambda e: e.rank
        )
        for envelope in candidates:
            if envelope.admits(parameters):
                return envelope
        return None

    def risk_class(self, tool_name: str, parameters: Mapping[str, Any]) -> str | None:
        envelope = self.classify(tool_name, parameters)
        return envelope.risk_class if envelope else None

    def proposal_blockers(self, proposal: Proposal) -> tuple[str, ...]:
        """ProposalGuard: every reason this operation is outside policy right now."""
        reasons: list[str] = []
        envelope = self.classify(proposal.tool_name, proposal.parameters)
        if envelope is None:
            reasons.append("NO_RISK_ENVELOPE")
        else:
            if envelope.risk_class != proposal.partition.risk_class:
                reasons.append("RISK_CLASS_MISMATCH")
            if not proposal.requested_effects <= envelope.allowed_effects:
                reasons.append("EFFECT_OUTSIDE_ENVELOPE")
        for budget in self.budgets:
            if not budget.covers(proposal.tool_name):
                continue
            scope = budget.scope_key(proposal)
            if scope is None:
                # constraint: a proposal without the scoping argument gets no bucket of its
                # own and must not share one with every other such proposal; it is denied
                reasons.append(f"BUDGET_SCOPE_ERROR:{budget.budget_id}")
                continue
            charges = self._charges.get((budget.budget_id, scope), _Charges())
            # constraint: the metric is read here for every budget that has one, limit or
            # not, so a non-numeric argument is a denial now rather than an exception in
            # commit() after the token is spent
            amount = 0.0
            if budget.metric is not None:
                try:
                    amount = budget.amount(proposal)
                except ValueError:
                    reasons.append(f"BUDGET_METRIC_ERROR:{budget.budget_id}")
                    continue
            if budget.limit is not None and charges.total + amount > budget.limit:
                reasons.append(f"BUDGET_EXCEEDED:{budget.budget_id}")
            if budget.max_operations is not None and charges.count + 1 > budget.max_operations:
                reasons.append(f"OPERATION_LIMIT:{budget.budget_id}")
        for earlier in self._history.get(proposal.partition, ()):
            if (earlier, proposal.tool_name) in self.conflicts:
                reasons.append(f"CONFLICTING_ACTION:{earlier}")
                break
        return tuple(dict.fromkeys(reasons))

    def redemption_blockers(
        self, token: AuthorizationToken, proposal: Proposal
    ) -> tuple[str, ...]:
        """
        RedemptionGuard: the same question, asked again at the moment of use
        effect: The gateway has already matched the proposal to the token's digest, so
                re-classifying the proposal is re-classifying what was authorized. A
                policy rolled back since issuance, or a budget consumed by an earlier
                redemption, denies here even though the decision said yes.
        """
        return self.proposal_blockers(proposal)

    def commit(self, token: AuthorizationToken, proposal: Proposal) -> None:
        """
        RedemptionGuard: charge budgets for an operation the gateway has just consumed
        constraint: Called inside the gateway lock, after the token is marked used and
                    after every guard returned no blockers, so a charge never lands for
                    an operation that did not run and two redemptions never both charge.
                    Must not raise: every amount() read here was already read, for the
                    same proposal and budgets, in redemption_blockers() a moment earlier.
        """
        for budget in self.budgets:
            if not budget.covers(proposal.tool_name):
                continue
            key = (budget.budget_id, budget.scope_key(proposal))
            charges = self._charges.setdefault(key, _Charges())
            charges.total += budget.amount(proposal)
            charges.count += 1
        self._history.setdefault(proposal.partition, []).append(proposal.tool_name)

    def charged(self, budget_id: str, scope_key: Any) -> tuple[float, int]:
        charges = self._charges.get((budget_id, scope_key), _Charges())
        return charges.total, charges.count
