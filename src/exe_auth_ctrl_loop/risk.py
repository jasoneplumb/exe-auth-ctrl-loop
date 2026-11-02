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
        the risk class. Budgets charge at redemption, in the consume transaction.
Future: Durable since v3 Step 02: charges and the per-partition sequence history are rows
        of append-only tables in the policy's Store, so a restart keeps every charge and
        several policy objects over one database share one account. Since Step 03 a
        charge is written in the gateway's consume transaction when the policy and the
        gateway share a Store, so a charge and a consume commit together or not at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, is_dataclass
from typing import Any, Mapping, TypeGuard

from .authority import AuthorizationToken, Proposal
from .store import Store, canonical_json, partition_text


def _finite_number(value: object) -> TypeGuard[int | float]:
    """
    intent: An int, or a float that is not NaN or infinite. Booleans are not numbers.
    constraint: A true result does not mean float() succeeds: an int too large for a
                float passes, and float() raises OverflowError on it. Compare the value
                directly, as Bound.admits() does, or catch OverflowError, as amount() does.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # math.isfinite() raises on an int too large for a float, so only floats are tested
    return isinstance(value, int) or math.isfinite(value)


@dataclass(frozen=True)
class Bound:
    """
    intent: A numeric argument must lie within [minimum, maximum]
    constraint: A value outside the finite numbers is admitted by no bound. Every
                comparison with NaN is false, so a NaN would otherwise satisfy any limit.
                An omitted minimum is 0, not unbounded: an envelope that forgot its floor
                denies a negative amount instead of classifying it. A field that may be
                negative declares a negative minimum.
    """

    field: str
    maximum: float | None = None
    minimum: float | None = None

    def __post_init__(self) -> None:
        for name, limit in (("maximum", self.maximum), ("minimum", self.minimum)):
            if limit is not None and not _finite_number(limit):
                raise ValueError(f"bound {self.field}: {name} must be a finite number")
        if self.maximum is not None and self.maximum < self.floor:
            raise ValueError(
                f"bound {self.field}: maximum {self.maximum} is below the floor {self.floor}"
            )

    @property
    def floor(self) -> float:
        return 0.0 if self.minimum is None else self.minimum

    def admits(self, parameters: Mapping[str, Any]) -> bool:
        value = parameters.get(self.field)
        if not _finite_number(value):
            return False
        if value < self.floor:
            return False
        if self.maximum is not None and value > self.maximum:
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
        if self.limit is not None and not (_finite_number(self.limit) and self.limit >= 0):
            raise ValueError("a limit must be a finite, non-negative number")

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
        """
        constraint: A charge is a finite, non-negative number. A NaN total makes every
                    later comparison against the limit false, and a negative charge
                    refunds the budget. Raising is a denial: proposal_blockers() reads every
                    amount before a token exists, so commit() never meets a bad one.
        """
        if self.metric is None:
            return 0.0
        value = proposal.parameters.get(self.metric)
        if not _finite_number(value):
            raise ValueError(f"budget {self.budget_id}: {self.metric} is not a finite number")
        try:
            charge = float(value)
        except OverflowError:
            raise ValueError(f"budget {self.budget_id}: {self.metric} is too large") from None
        if charge < 0:
            raise ValueError(f"budget {self.budget_id}: {self.metric} is negative")
        return charge


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
             the same checks run again in the consume transaction against the policy that is
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
    # effect: conflicts are read from the current policy at every check, against the
    # durable history of tool names. Removing a pair in update() stops it blocking at the
    # next check; the history itself is never cleared, so re-adding the pair blocks again.
    conflicts: frozenset[tuple[str, str]] = frozenset()
    # constraint: keyword-only and required. A policy with no store would keep charges in
    # memory, which v3 removes (docs/v3/spec.md §2 rule 5, O-7).
    store: Store = field(kw_only=True, repr=False, compare=False)

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
            charges = self._totals(budget.budget_id, scope)
            # constraint: the metric is read here for every budget that has one, limit or
            # not, so a non-numeric, non-finite, or negative argument is a denial now
            # rather than an exception in commit() after the token is spent
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
        for earlier in self._history(proposal):
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
        constraint: Called inside the gateway's consume transaction, after the token is
                    consumed and every guard returned no blockers, so a charge never lands
                    for an operation that was refused and two redemptions never both charge.
                    Should not raise: every amount() read here was already read, for the
                    same proposal and budgets, in redemption_blockers() a moment earlier.
                    If it does, the transaction rolls back and the token stays unspent.
        """
        with self.store.transaction() as conn:
            for budget in self.budgets:
                if not budget.covers(proposal.tool_name):
                    continue
                conn.execute(
                    "INSERT INTO risk_charges"
                    " (policy_version, budget_id, scope_key, amount, token_id)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (self.version, budget.budget_id, _scope_text(budget.scope_key(proposal)),
                     budget.amount(proposal), token.token_id),
                )
            conn.execute(
                "INSERT INTO risk_history (policy_version, partition, tool_name, token_id)"
                " VALUES (?, ?, ?, ?)",
                (self.version, partition_text(proposal.partition), proposal.tool_name,
                 token.token_id),
            )

    def charged(self, budget_id: str, scope_key: Any) -> tuple[float, int]:
        charges = self._totals(budget_id, scope_key)
        return charges.total, charges.count

    def _totals(self, budget_id: str, scope_key: Any) -> _Charges:
        """
        intent: The committed total and count for one budget scope
        constraint: Summed here in commit order, not with SQL SUM, so the total is the same
                    float the v0.3.1 running sum produced; a different summation order or
                    SQLite's compensated SUM could move a total across an exact limit
        """
        rows = self.store.connection().execute(
            "SELECT amount FROM risk_charges WHERE budget_id = ? AND scope_key = ?"
            " ORDER BY charge_id",
            (budget_id, _scope_text(scope_key)),
        ).fetchall()
        total = 0.0
        for (amount,) in rows:
            total += amount
        return _Charges(total, len(rows))

    def _history(self, proposal: Proposal) -> list[str]:
        return [
            row[0] for row in self.store.connection().execute(
                "SELECT tool_name FROM risk_history WHERE partition = ? ORDER BY entry_id",
                (partition_text(proposal.partition),),
            )
        ]


def _scope_text(scope: Any) -> str:
    """
    intent: A column value that names one budget scope and nothing else
    effect: A partition scope and an argument value can never collide, because the two
            kinds carry different prefixes
    """
    if is_dataclass(scope) and not isinstance(scope, type):
        return "partition:" + partition_text(scope)
    return "value:" + canonical_json(scope)
