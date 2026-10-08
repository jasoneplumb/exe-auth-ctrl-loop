"""
Intent: Drive the real authority stack with a synthetic proposal stream and record what
        it does, step by step, from an empty evidence store
Context: One simulated step is one proposal for the current partition. The host decides,
        tokens are redeemed one step later (so a state change in between is visible), the
        oracle labels frozen trials after a delay, and the lifecycle reacts. Nothing is
        seeded unless the comparator is the legacy demonstration, which is labelled so.
Pattern: The simulated parts are explicit and few: the truth process (is this proposal
        acceptable, is a failure severe), the oracle (noisy, delayed, sometimes silent),
        the human reviewer (catches a fraction of bad proposals), and configuration churn.
Future: Everything else is the production code path. If the stack changes, these numbers
        change, which is the point.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from exe_auth_ctrl_loop import (
    AuthorityController,
    Bound,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    LifecycleManager,
    LifecycleState,
    OneOf,
    Oracle,
    OracleKind,
    Outcome,
    OutcomeStatus,
    PartitionKey,
    Policy,
    Proposal,
    Provenance,
    ReviewAction,
    RiskEnvelope,
    RiskPolicy,
    Route,
    SequenceBudget,
    ShadowEvidenceLog,
    TrialLabel,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
PAYEES = frozenset({"acct-100", "acct-200", "acct-300"})
ORACLE = Oracle("sim-oracle", "1", OracleKind.SANDBOX_REPLAY, "synthetic truth with noise")


@dataclass(frozen=True)
class ScenarioConfig:
    """One experiment. Field names are the public contract documented in README.md."""

    name: str
    comparator: str = "v2"  # v2 | human_only | legacy_wilson_seeded
    seeds: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    horizon: int = 400
    assumptions_violated: tuple[str, ...] = ()
    # truth process
    p_good: float = 0.97
    severe_given_bad: float = 0.10
    shift_at: int | None = None
    p_after_shift: float | None = None
    defect_after_qualified: int | None = None
    p_defect: float | None = None
    severe_defect: float | None = None
    burst_enter: float = 0.0
    burst_exit: float = 1.0
    p_good_in_burst: float | None = None
    churn_every: int | None = None
    release_after: int | None = None
    # oracle and reviewer
    oracle_flip: float = 0.0
    oracle_inconclusive: float = 0.0
    label_delay: int = 2
    label_missing: float = 0.0
    label_deadline: int = 20
    reviewer_catch: float = 0.9
    # policy
    n_min: int = 30
    required_low: float = 0.90
    required_high: float = 0.95
    alpha: float = 0.05
    estimator: str = "beta_mixture"
    audit_rate: float = 0.05
    max_evidence_age: int = 1000
    redeem_delay: int = 2  # steps between issue and redemption; >1 leaves a token outstanding
    token_ttl: int = 5  # steps; must exceed redeem_delay or every token expires unredeemed
    # risk
    amount_high_rate: float = 0.0
    amount_out_rate: float = 0.0
    mislabel_rate: float = 0.0
    budget_limit: float | None = None
    seeded_successes: int = 0
    seeded_failures: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScenarioConfig:
        data = dict(raw)
        for key in ("seeds", "assumptions_violated"):
            if key in data:
                data[key] = tuple(data[key])
        return cls(**data)


@dataclass
class RunMetrics:
    scenario: str
    comparator: str
    seed: int
    horizon: int
    qualified: bool = False
    time_to_authority: int | None = None
    qualified_before_turnover: bool | None = None
    turnovers: int = 0
    autonomous_executions: int = 0
    audit_executions: int = 0
    human_reviews: int = 0
    human_approved_executions: int = 0
    human_rejections: int = 0
    failures_human_missed: int = 0
    policy_denials: int = 0
    issue_denials: int = 0
    stale_token_denials: int = 0
    shadow_trials: int = 0
    shadow_positive: int = 0
    shadow_negative: int = 0
    shadow_censored: int = 0
    failures_after_autonomy: int = 0
    severe_after_autonomy: int = 0
    suspensions: int = 0
    releases: int = 0
    requalifications: int = 0
    intervention_delay: int | None = None
    handler_calls: int = 0
    tokens_consumed: int = 0
    final_state: str = ""
    final_n: int = 0
    final_lower_bound: float | None = None
    denial_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def unauthorized_side_effects(self) -> int:
        return self.handler_calls - self.tokens_consumed

    @property
    def audit_overhead(self) -> float | None:
        total = self.autonomous_executions + self.audit_executions
        return self.audit_executions / total if total else None

    @property
    def censored_fraction(self) -> float | None:
        return self.shadow_censored / self.shadow_trials if self.shadow_trials else None

    def row(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("denial_reasons")
        row["unauthorized_side_effects"] = self.unauthorized_side_effects
        row["audit_overhead"] = self.audit_overhead
        row["censored_fraction"] = self.censored_fraction
        row["tta_censored"] = self.time_to_authority is None
        row["denial_reasons"] = ";".join(f"{k}={v}" for k, v in sorted(self.denial_reasons.items()))
        return row


def make_key(prompt_version: str, risk_class: str) -> PartitionKey:
    return PartitionKey(
        "openai", "proposer-m1", prompt_version, "anthropic", "executor-m1", "exec-p1",
        "refund-api-v2", "pol1", "sim-env", "refund", "0.95-1.00", risk_class,
    )


def make_risk_policy(cfg: ScenarioConfig) -> RiskPolicy:
    envelopes = (
        RiskEnvelope("create_refund", "low", 0, frozenset({"refund:write"}),
                     (Bound("usd", maximum=100.0, minimum=0.0), OneOf("payee", PAYEES))),
        RiskEnvelope("create_refund", "high", 1, frozenset({"refund:write"}),
                     (Bound("usd", maximum=10_000.0, minimum=0.0), OneOf("payee", PAYEES))),
    )
    budgets: tuple[SequenceBudget, ...] = ()
    if cfg.budget_limit is not None:
        budgets = (SequenceBudget("refund-total", frozenset({"create_refund"}), "partition",
                                  "usd", cfg.budget_limit),)
    return RiskPolicy("risk-v1", envelopes, budgets)


class Simulation:
    """One scenario, one seed, from n=0 to the horizon. run() returns the metrics."""

    def __init__(self, cfg: ScenarioConfig, seed: int, trace: bool = False) -> None:
        self.cfg = cfg
        self.seed = seed
        self.rng = random.Random(seed)
        self.t = 0
        self.metrics = RunMetrics(cfg.name, cfg.comparator, seed, cfg.horizon)
        self.trace: list[dict[str, Any]] | None = [] if trace else None

        self.ledger = EventLedger()
        self.store = EvidenceStore()
        self.shadow = ShadowEvidenceLog(
            self.ledger, timedelta(minutes=cfg.label_deadline), self.clock
        )
        self.policy = Policy(
            "pol1", {"low": cfg.required_low, "high": cfg.required_high}, cfg.n_min,
            timedelta(minutes=cfg.max_evidence_age), cfg.audit_rate,
            token_ttl=timedelta(minutes=cfg.token_ttl),
            alpha=cfg.alpha, estimator=cfg.estimator,
        )
        self.risk = make_risk_policy(cfg)
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(seed * 7919 + 1), self.clock, guards=(self.risk,)
        )
        self.manager = LifecycleManager(
            self.shadow, self.store, self.policy, self.controller, self.clock
        )
        if cfg.comparator == "legacy_wilson_seeded":
            # The v0.2.0 path: bare controller, hand-seeded evidence, no lifecycle, no live
            # redemption checks. Not a bootstrap; a demonstration of the old behaviour.
            self.gateway = ExecutionGateway(self.clock)
        else:
            self.gateway = ExecutionGateway(
                self.clock, evidence=self.store, current_policy=lambda: self.policy,
                guards=(self.manager, self.risk),
            )
        self.prompt_version = "prop-p1"
        self.key = make_key(self.prompt_version, "low")
        if cfg.seeded_successes or cfg.seeded_failures:
            self.store.put(EvidenceSnapshot(
                "seeded", 1, self.key, cfg.seeded_successes, cfg.seeded_failures, T0, T0,
            ))

        self.pending_tokens: list[tuple[int, str, Proposal, bool]] = []
        self.pending_labels: list[tuple[int, str, PartitionKey, str, TrialLabel]] = []
        self.in_burst = False
        self.qualified_at: int | None = None
        self.qualified_this_epoch_at: int | None = None
        self.defected = False
        self.first_bad_after_change: int | None = None
        self.suspended_at: int | None = None
        self.was_autonomous = False
        self.counter = 0

    # -- simulated world -----------------------------------------------------------

    def clock(self) -> datetime:
        return T0 + timedelta(minutes=self.t)

    def p_good_now(self) -> float:
        cfg = self.cfg
        p = cfg.p_good
        if cfg.shift_at is not None and self.t >= cfg.shift_at and cfg.p_after_shift is not None:
            p = cfg.p_after_shift
        if self.defected and cfg.p_defect is not None:
            p = cfg.p_defect
        if self.in_burst and cfg.p_good_in_burst is not None:
            p = cfg.p_good_in_burst
        return p

    def step_burst(self) -> None:
        if self.in_burst:
            if self.rng.random() < self.cfg.burst_exit:
                self.in_burst = False
        elif self.rng.random() < self.cfg.burst_enter:
            self.in_burst = True

    def draw_truth(self) -> tuple[bool, bool]:
        """(acceptable, severe). Severe implies unacceptable."""
        good = self.rng.random() < self.p_good_now()
        if good:
            return True, False
        severe_rate = self.cfg.severe_given_bad
        if self.defected and self.cfg.severe_defect is not None:
            severe_rate = self.cfg.severe_defect
        return False, self.rng.random() < severe_rate

    def next_proposal(self) -> Proposal:
        cfg = self.cfg
        self.counter += 1
        u = self.rng.random()
        if u < cfg.amount_out_rate:
            usd = round(self.rng.uniform(10_001, 20_000), 2)
            label = "high"
        elif u < cfg.amount_out_rate + cfg.amount_high_rate:
            usd = round(self.rng.uniform(100.01, 5_000), 2)
            label = "low" if self.rng.random() < cfg.mislabel_rate else "high"
        else:
            usd = round(self.rng.uniform(5, 100), 2)
            label = "low"
        payee = sorted(PAYEES)[self.rng.randrange(len(PAYEES))]
        return Proposal(
            f"p-{self.seed}-{self.counter}", "refund order", "create_refund",
            {"order_id": self.counter, "usd": usd, "payee": payee}, 0.96,
            frozenset({"refund:write"}), make_key(self.prompt_version, label), "openai",
        )

    def oracle_label(self, good: bool) -> TrialLabel:
        if self.rng.random() < self.cfg.oracle_inconclusive:
            return TrialLabel.INCONCLUSIVE
        if self.rng.random() < self.cfg.oracle_flip:
            good = not good
        return TrialLabel.POSITIVE if good else TrialLabel.NEGATIVE

    def schedule_label(self, trial_id: str, key: PartitionKey, digest: str, good: bool) -> None:
        if self.rng.random() < self.cfg.label_missing:
            return
        self.pending_labels.append(
            (self.t + self.cfg.label_delay, trial_id, key, digest, self.oracle_label(good))
        )

    def deliver_labels(self) -> None:
        due = [item for item in self.pending_labels if item[0] <= self.t]
        self.pending_labels = [item for item in self.pending_labels if item[0] > self.t]
        for _, trial_id, key, digest, label in due:
            self.shadow.adjudicate(trial_id, key, digest, label, ORACLE, self.clock())

    # -- the host path -------------------------------------------------------------

    def decide(self, proposal: Proposal):
        if self.cfg.comparator == "human_only":
            decision = self.controller.evaluate(proposal)
            return decision if decision.route == Route.DENY else None
        if self.cfg.comparator == "legacy_wilson_seeded":
            return self.controller.evaluate(proposal)
        return self.manager.evaluate(proposal)

    def handler(self, proposal: Proposal) -> dict[str, Any]:
        self.metrics.handler_calls += 1
        return {"receipt": proposal.proposal_id}

    def redeem_pending(self) -> None:
        """Tokens come due redeem_delay steps after issue; what changed in between shows."""
        due = [item for item in self.pending_tokens if item[0] <= self.t]
        self.pending_tokens = [item for item in self.pending_tokens if item[0] > self.t]
        for _, token_id, proposal, audited in due:
            try:
                self.gateway.execute(token_id, proposal, self.handler)
            except PermissionError:
                self.metrics.stale_token_denials += 1
                continue
            self.metrics.tokens_consumed += 1
            if audited:
                self.metrics.audit_executions += 1
            else:
                self.metrics.autonomous_executions += 1
            good, severe = self.draw_truth()
            if not good:
                self.metrics.failures_after_autonomy += 1
                if self.first_bad_after_change is None and (self.defected or (
                    self.cfg.shift_at is not None and self.t >= self.cfg.shift_at
                )):
                    self.first_bad_after_change = self.t
            if severe:
                self.metrics.severe_after_autonomy += 1
            provenance = Provenance.AUDIT if audited else Provenance.AUTONOMOUS
            trial_id = f"x-{proposal.proposal_id}"
            if self.cfg.comparator == "legacy_wilson_seeded":
                self.legacy_adjudicate(proposal, good, severe, autonomous=True)
            else:
                if severe:
                    self.manager.record_severe(proposal.partition, provenance, "severe_outcome")
                self.shadow.freeze(trial_id, proposal, provenance)
                self.schedule_label(trial_id, proposal.partition, proposal.proposal_digest, good)
            self.record_trace("redeem", proposal, None, True, good, severe)

    def legacy_adjudicate(self, proposal: Proposal, good: bool, severe: bool,
                          autonomous: bool) -> None:
        """v0.2.0 evidence path: fold the outcome straight into the seeded snapshot."""
        status = OutcomeStatus.ACCEPTABLE if good else OutcomeStatus.UNACCEPTABLE
        self.store.adjudicate(
            proposal.partition,
            Outcome(proposal.proposal_id, status, severe, "sim", self.clock(), "d"),
            autonomous=autonomous,
        )

    def human_path(self, proposal: Proposal, shadow: bool) -> None:
        good, severe = self.draw_truth()
        if shadow:
            trial_id = f"s-{proposal.proposal_id}"
            self.shadow.freeze(trial_id, proposal, Provenance.SHADOW)
            self.metrics.shadow_trials += 1
            self.schedule_label(trial_id, proposal.partition, proposal.proposal_digest, good)
        self.metrics.human_reviews += 1
        caught = (not good) and self.rng.random() < self.cfg.reviewer_catch
        if caught:
            self.metrics.human_rejections += 1
            if shadow:
                self.shadow.record_human_review(
                    f"s-{proposal.proposal_id}", "reviewer", ReviewAction.REJECT
                )
            self.record_trace("human_reject", proposal, Route.HUMAN_APPROVAL, False, good, severe)
            return
        self.metrics.human_approved_executions += 1
        self.metrics.handler_calls += 1  # a human-approved execution still runs the handler
        self.metrics.tokens_consumed += 1  # through a human-approved token in the real loop
        if not good:
            self.metrics.failures_human_missed += 1
        if severe and self.cfg.comparator == "v2":
            self.manager.record_severe(
                proposal.partition, Provenance.HUMAN_APPROVED, "severe_outcome"
            )
        elif self.cfg.comparator == "legacy_wilson_seeded":
            self.legacy_adjudicate(proposal, good, severe, autonomous=False)
        if shadow:
            self.shadow.record_human_review(
                f"s-{proposal.proposal_id}", "reviewer", ReviewAction.APPROVE
            )
        self.record_trace("human_approve", proposal, Route.HUMAN_APPROVAL, True, good, severe)

    def step(self) -> None:
        cfg = self.cfg
        self.step_burst()
        self.redeem_pending()
        self.deliver_labels()
        self.maybe_release()
        self.maybe_churn()

        proposal = self.next_proposal()
        decision = self.decide(proposal)
        if decision is None:
            self.human_path(proposal, shadow=False)
        elif decision.route == Route.DENY:
            self.metrics.policy_denials += 1
            for reason in decision.reason_codes:
                self.metrics.denial_reasons[reason] = self.metrics.denial_reasons.get(reason, 0) + 1
            self.record_trace("deny", proposal, decision.route, False, None, None,
                              reasons=decision.reason_codes)
        elif decision.route in {Route.AUTONOMOUS, Route.AUDIT}:
            try:
                token = self.gateway.issue(decision, proposal, self.policy)
            except PermissionError:
                self.metrics.issue_denials += 1
                self.record_trace("issue_denied", proposal, decision.route, False, None, None)
            else:
                audited = decision.route == Route.AUDIT
                self.pending_tokens.append(
                    (self.t + cfg.redeem_delay, token.token_id, proposal, audited)
                )
                self.record_trace("issued", proposal, decision.route, False, None, None,
                                  lower=decision.lower_bound)
        else:
            self.human_path(proposal, shadow=cfg.comparator == "v2")
        self.observe_state()
        self.t += 1

    # -- lifecycle events ----------------------------------------------------------

    def maybe_churn(self) -> None:
        cfg = self.cfg
        if not cfg.churn_every or self.t == 0 or self.t % cfg.churn_every:
            return
        if cfg.comparator != "v2":
            return
        self.metrics.turnovers += 1
        if self.metrics.qualified_before_turnover is None:
            self.metrics.qualified_before_turnover = self.qualified_this_epoch_at is not None
        old = self.key
        self.prompt_version = f"prop-p{self.metrics.turnovers + 1}"
        self.key = make_key(self.prompt_version, "low")
        self.manager.on_churn(old, self.key)
        self.qualified_this_epoch_at = None
        self.pending_labels = [item for item in self.pending_labels if item[2] != old]

    def maybe_release(self) -> None:
        cfg = self.cfg
        if cfg.release_after is None or self.suspended_at is None:
            return
        if self.t - self.suspended_at >= cfg.release_after:
            self.manager.release(self.key, "operator", "simulated post-incident review")
            self.metrics.releases += 1
            self.suspended_at = None

    def observe_state(self) -> None:
        if self.cfg.comparator != "v2":
            return
        state = self.manager.state(self.key)
        snapshot = self.store.get(self.key)
        if state == LifecycleState.AUTONOMOUS:
            if self.qualified_at is None:
                self.qualified_at = self.t
                self.metrics.qualified = True
                self.metrics.time_to_authority = self.t
            if self.qualified_this_epoch_at is None:
                self.qualified_this_epoch_at = self.t
                if self.was_autonomous and self.metrics.releases:
                    self.metrics.requalifications += 1
            self.was_autonomous = True
            cfg = self.cfg
            if (cfg.defect_after_qualified is not None and not self.defected
                    and self.t - self.qualified_at >= cfg.defect_after_qualified):
                self.defected = True
        else:
            if self.first_bad_after_change is not None and self.metrics.intervention_delay is None:
                self.metrics.intervention_delay = self.t - self.first_bad_after_change
            self.qualified_this_epoch_at = None
        if state == LifecycleState.SUSPENDED and self.suspended_at is None:
            self.suspended_at = self.t
            self.metrics.suspensions += 1
        if snapshot is not None:
            self.metrics.final_n = snapshot.n
            self.metrics.final_lower_bound = round(
                self.policy.lower_bound(snapshot.successes, snapshot.failures), 6
            )
        self.metrics.final_state = state.value

    def record_trace(self, event: str, proposal: Proposal, route: Route | None, executed: bool,
                     good: bool | None, severe: bool | None, reasons: tuple[str, ...] = (),
                     lower: float | None = None) -> None:
        if self.trace is None:
            return
        snapshot = self.store.get(self.key)
        self.trace.append({
            "t": self.t, "event": event, "route": route.value if route else None,
            "risk_class": proposal.partition.risk_class, "usd": proposal.parameters["usd"],
            "executed": executed, "acceptable": good, "severe": severe,
            "reasons": list(reasons), "lower_bound": lower,
            "state": self.manager.state(self.key).value if self.cfg.comparator == "v2" else None,
            "n": snapshot.n if snapshot else 0,
        })

    def finish(self) -> RunMetrics:
        m = self.metrics
        for provenance in (Provenance.SHADOW,):
            counts = self.shadow.counts(self.key, provenance)
            m.shadow_positive += counts.positive
            m.shadow_negative += counts.negative
            m.shadow_censored += counts.inconclusive + counts.pending
        if self.cfg.comparator == "legacy_wilson_seeded":
            snapshot = self.store.get(self.key)
            m.final_state = "suspended" if snapshot and snapshot.suspended else "seeded_autonomous"
            m.final_n = snapshot.n if snapshot else 0
            m.qualified = True  # by seeding, not by evidence: see README
            m.time_to_authority = 0
        if self.cfg.comparator == "human_only":
            m.final_state = "human_only"
        return m

    def run(self) -> RunMetrics:
        for _ in range(self.cfg.horizon):
            self.step()
        return self.finish()
