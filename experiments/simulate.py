"""
Intent: Drive the shipped authority loop with a synthetic proposal stream and record what
        it does, step by step, from an empty evidence store
Context: One simulated step is one proposal for the current partition. Since v3 Step 09 the
        proposal goes through the shipped CrossModelAuthorityLoop: the OpenAI proposer and
        the Claude executor run with fake clients, the executor's commit hook records the
        decision and freezes what the loop freezes, and the gateway issues and redeems the
        token inside the same run. The oracle labels frozen trials after a delay, and the
        lifecycle reacts. Nothing is seeded unless the comparator is the legacy
        demonstration, which is labelled so.
Pattern: The simulated parts are explicit and few: the two model clients (they propose and
        request exactly the synthetic proposal), the truth process (is this proposal
        acceptable, is a failure severe), the oracle (noisy, delayed, sometimes silent), the
        human reviewer (catches a fraction of bad proposals, signs an approval otherwise),
        and configuration churn. The harness never commits, freezes, issues, or redeems
        anything itself (claims matrix L6; tests/test_experiments.py::ShippedLoopTests).
Future: Everything else is the production code path. If the stack changes, these numbers
        change, which is the point.
"""

from __future__ import annotations

import json
import random
import tempfile
import weakref
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from exe_auth_ctrl_loop import (
    Approval,
    AuthorityController,
    Bound,
    ClaudeExecutionAgent,
    CrossModelAuthorityLoop,
    EventLedger,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    GrantScope,
    HandlerTable,
    Keyring,
    LifecycleManager,
    LifecycleState,
    OneOf,
    OpenAIProposalGenerator,
    Oracle,
    OracleKind,
    Outcome,
    OutcomeStatus,
    PartitionKey,
    Policy,
    PrincipalKind,
    Principals,
    Proposal,
    ProposalBundle,
    ProposalDraftModel,
    ProposalGenerationError,
    ProposedActionModel,
    Provenance,
    ReviewAction,
    RiskEnvelope,
    RiskPolicy,
    Route,
    SequenceBudget,
    ShadowEvidenceLog,
    SignedLabel,
    Store,
    ToolCatalog,
    ToolDefinition,
    TrialLabel,
    label_payload,
)
from exe_auth_ctrl_loop.executor import ExecutionStep
from exe_auth_ctrl_loop.principals import approval_payload, release_payload, root_grant_payload

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
PAYEES = frozenset({"acct-100", "acct-200", "acct-300"})
ORACLE = Oracle("sim-oracle", "1", OracleKind.SANDBOX_REPLAY, "synthetic truth with noise")
SIM_KEYS = {"operator": "k-operator", "reviewer": "k-reviewer", "sim-oracle": "k-oracle"}
SIM_AGENT = "agent"
SIM_PROPOSER = "openai"  # the proposer's ledger actor
# what the fake proposer and executor report as their pinned versions; make_key matches
PROPOSER_MODEL, EXECUTOR_MODEL, EXECUTOR_PROMPT, ENVIRONMENT = (
    "proposer-m1", "executor-m1", "exec-p1", "sim-env",
)
SIM_GRANT = "g-sim"
SIM_GRANT_EXPIRY = datetime(2100, 1, 1, tzinfo=timezone.utc)
# The ledger key a simulation run supplies (v3 Step 08). A fixed value keeps runs
# reproducible; a deployment supplies its own secret and never commits it.
SIM_LEDGER_KEY = b"sim-ledger-key"


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
    token_ttl: int = 5  # steps; the shipped loop redeems in the step it issues
    # risk
    amount_high_rate: float = 0.0
    amount_out_rate: float = 0.0
    # constraint: no effect since v3 Step 09. The shipped proposer derives the risk class
    # from the host's envelopes, so no proposal can carry a mislabel; the field stays so
    # the v0.3.x configs load unchanged (the draw is still made, keeping the RNG stream)
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
    # approved by the reviewer, then refused by the gateway (e.g. suspended, over budget)
    approved_refused: int = 0
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
        "openai", PROPOSER_MODEL, prompt_version, "anthropic", EXECUTOR_MODEL, EXECUTOR_PROMPT,
        "refund-api-v2", "pol1", ENVIRONMENT, "refund", "0.95-1.00", risk_class,
    )


def make_risk_policy(cfg: ScenarioConfig, store: Store) -> RiskPolicy:
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
    return RiskPolicy("risk-v1", envelopes, budgets, store=store)


class FakeOpenAI:
    """The simulated proposer model: returns the next synthetic draft, as Structured Output."""

    def __init__(self) -> None:
        self.draft: ProposalDraftModel | None = None
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(output_parsed=self.draft)


class FakeClaude:
    """The simulated executor model: requests the bundle's one proposal, then stops."""

    def __init__(self) -> None:
        self.proposal: Proposal | None = None
        self.messages = SimpleNamespace(create=self._create)
        self._turn = 0

    def ask(self, proposal: Proposal) -> None:
        self.proposal, self._turn = proposal, 0

    def _create(self, **_: Any) -> SimpleNamespace:
        self._turn += 1
        if self._turn > 1 or self.proposal is None:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")])
        p = self.proposal
        return SimpleNamespace(content=[SimpleNamespace(
            type="tool_use", id=f"toolu-{p.proposal_id}", name=p.tool_name,
            input={"proposal_id": p.proposal_id, **p.parameters},
        )])


def _discard(db: Store, directory: tempfile.TemporaryDirectory[str]) -> None:
    db.close()
    directory.cleanup()


class Simulation:
    """One scenario, one seed, from n=0 to the horizon. run() returns the metrics."""

    def __init__(self, cfg: ScenarioConfig, seed: int, trace: bool = False) -> None:
        self.cfg = cfg
        self.seed = seed
        self.rng = random.Random(seed)
        self.t = 0
        self.metrics = RunMetrics(cfg.name, cfg.comparator, seed, cfg.horizon)
        self.trace: list[dict[str, Any]] | None = [] if trace else None

        # One database per run, in a throwaway directory removed when the run object is
        # collected. Every component shares it, as the lifecycle requires.
        directory = tempfile.TemporaryDirectory()
        self.db = Store(Path(directory.name) / "run.db", ledger_key=SIM_LEDGER_KEY)
        weakref.finalize(self, _discard, self.db, directory)
        self.ledger = EventLedger(self.db)
        # Principals (v3 Step 05): fixed simulation keys; the operator grants the agent
        # unrestricted scope; the reviewer (who approves) and the oracle (who labels) are
        # distinct principals, so REVIEWER_IS_ORACLE never fires
        self.keyring = Keyring({key_id: f"sim-{key_id}".encode() for key_id in SIM_KEYS.values()})
        self.principals = Principals(self.db, self.keyring, self.clock)
        for name, key_id in SIM_KEYS.items():
            kind = PrincipalKind.ORACLE if name == ORACLE.oracle_id else PrincipalKind.HUMAN
            self.principals.register(name, kind, by="controller", key_id=key_id)
        for name in (SIM_AGENT, SIM_PROPOSER):
            self.principals.register(name, PrincipalKind.MODEL, by="controller")
        self.principals.grant_root(
            "operator", SIM_AGENT, GrantScope(), SIM_GRANT_EXPIRY,
            self.keyring.sign(SIM_KEYS["operator"], root_grant_payload(
                SIM_GRANT, "operator", SIM_AGENT, GrantScope(), SIM_GRANT_EXPIRY,
            )),
            grant_id=SIM_GRANT,
        )
        self.store = EvidenceStore(self.db)
        self.shadow = ShadowEvidenceLog(
            self.ledger, timedelta(minutes=cfg.label_deadline), self.clock,
            principals=self.principals,
        )
        self.policy = Policy(
            "pol1", {"low": cfg.required_low, "high": cfg.required_high}, cfg.n_min,
            timedelta(minutes=cfg.max_evidence_age), cfg.audit_rate,
            token_ttl=timedelta(minutes=cfg.token_ttl),
            alpha=cfg.alpha, estimator=cfg.estimator,
        )
        self.risk = make_risk_policy(cfg, self.db)
        # The host's catalog and handler table. The schema is permissive; the risk policy's
        # envelopes do the classifying, for the proposer and the executor alike.
        self.catalog = ToolCatalog(risk=self.risk)
        self.catalog.register(ToolDefinition(
            "create_refund", "refund", {"type": "object"}, frozenset({"refund:write"}),
            "refund-api-v2", "refund", None,
        ))
        handlers = HandlerTable(self.catalog)
        handlers.register("create_refund", self.handler)
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(seed * 7919 + 1), self.clock, guards=(self.risk,)
        )
        self.manager = LifecycleManager(
            self.shadow, self.store, self.policy, self.controller, self.clock,
            principals=self.principals,
        )
        legacy = cfg.comparator == "legacy_wilson_seeded"
        if legacy:
            # The v0.2.0 path: bare controller, hand-seeded evidence, no lifecycle, no live
            # redemption checks. Not a bootstrap; a demonstration of the old behaviour.
            self.gateway = ExecutionGateway(
                self.clock, store=self.db, handlers=handlers, principals=self.principals
            )
        else:
            self.gateway = ExecutionGateway(
                self.clock, evidence=self.store, current_policy=lambda: self.policy,
                guards=(self.manager, self.risk), store=self.db, handlers=handlers,
                principals=self.principals,
            )
        self.openai, self.claude = FakeOpenAI(), FakeClaude()
        self.prompt_version = "prop-p1"
        self.proposer = OpenAIProposalGenerator(
            PROPOSER_MODEL, self.prompt_version, self.catalog, client=self.openai
        )
        agent = ClaudeExecutionAgent(
            EXECUTOR_MODEL, EXECUTOR_PROMPT, self.controller if legacy else self.manager,
            self.gateway, self.catalog, client=self.claude,
            principal_id=SIM_AGENT, grant_id=SIM_GRANT,
        )
        # constraint: the shadow log is attached only where evidence is earned. The human-
        # only comparator never accumulates any, and the legacy one folds outcomes into its
        # seeded snapshot instead (legacy_adjudicate)
        self.loop = CrossModelAuthorityLoop(
            self.proposer, agent, self.ledger, self.policy.version, ENVIRONMENT,
            shadow=self.shadow if cfg.comparator == "v2" else None,
        )
        self.key = make_key(self.prompt_version, "low")
        if cfg.seeded_successes or cfg.seeded_failures:
            self.store.put(EvidenceSnapshot(
                "seeded", 1, self.key, cfg.seeded_successes, cfg.seeded_failures, T0, T0,
            ))

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

    def next_draft(self) -> dict[str, Any]:
        """The arguments the simulated proposer puts forward this step."""
        cfg = self.cfg
        self.counter += 1
        u = self.rng.random()
        if u < cfg.amount_out_rate:
            usd = round(self.rng.uniform(10_001, 20_000), 2)
        elif u < cfg.amount_out_rate + cfg.amount_high_rate:
            usd = round(self.rng.uniform(100.01, 5_000), 2)
            self.rng.random()  # the v0.3.x mislabel draw; see ScenarioConfig.mislabel_rate
        else:
            usd = round(self.rng.uniform(5, 100), 2)
        payee = sorted(PAYEES)[self.rng.randrange(len(PAYEES))]
        return {"order_id": self.counter, "usd": usd, "payee": payee}

    def propose(self, arguments: dict[str, Any]) -> ProposalBundle:
        """One bundle from the shipped proposer, through loop.propose (ledgered)."""
        self.openai.draft = ProposalDraftModel(
            readiness="executable", summary="refund order", missing_information=[],
            actions=[ProposedActionModel(
                action_id=f"p-{self.seed}-{self.counter}", intent="refund order",
                tool_name="create_refund", arguments_json=json.dumps(arguments),
                declared_effects=["refund:write"], declared_confidence=0.96,
                preconditions=[], success_criteria=[], assumptions=[],
                unresolved_questions=[],
            )],
        )
        return self.loop.propose("refund order")

    def execute(self, bundle: ProposalBundle, approval: Approval | None = None) -> ExecutionStep:
        """The executor asks for the bundle's one proposal; the loop does the rest."""
        self.claude.ask(bundle.proposals[0])
        run = self.loop.execute(bundle, approvals=(approval,) if approval else ())
        return run.steps[0]

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
            # signed by the registered oracle (v3 Step 08); it claims the time it labels,
            # and the host's receipt time, the same instant here, is what admission uses
            claimed_at = self.clock()
            payload = label_payload(trial_id, key, digest, label, ORACLE, claimed_at)
            mac = self.keyring.sign(SIM_KEYS[ORACLE.oracle_id], payload)
            self.shadow.adjudicate(
                SignedLabel(trial_id, key, digest, label, ORACLE, claimed_at, mac)
            )

    def approval(self, proposal: Proposal) -> Approval:
        """The simulated reviewer's signed, single-use approval of exactly this proposal."""
        approval_id = f"ap-{self.seed}-{self.counter}"
        expires = self.clock() + timedelta(days=1)
        payload = approval_payload(approval_id, "reviewer", proposal.proposal_digest,
                                   proposal.partition, expires)
        return Approval(approval_id, "reviewer", proposal.proposal_digest, proposal.partition,
                        expires, self.keyring.sign(SIM_KEYS["reviewer"], payload))

    # -- the host path -------------------------------------------------------------

    def handler(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.metrics.handler_calls += 1
        return {"receipt": dict(arguments)}

    def legacy_adjudicate(self, proposal: Proposal, good: bool, severe: bool,
                          autonomous: bool) -> None:
        """v0.2.0 evidence path: fold the outcome straight into the seeded snapshot."""
        status = OutcomeStatus.ACCEPTABLE if good else OutcomeStatus.UNACCEPTABLE
        self.store.adjudicate(
            proposal.partition,
            Outcome(proposal.proposal_id, status, severe, "sim", self.clock(), "d"),
            autonomous=autonomous,
        )

    def deny(self, proposal: Proposal | None, reasons: tuple[str, ...],
             route: Route | None = None) -> None:
        self.metrics.policy_denials += 1
        for reason in reasons:
            self.metrics.denial_reasons[reason] = self.metrics.denial_reasons.get(reason, 0) + 1
        if proposal is not None:
            self.record_trace("deny", proposal, route, False, None, None, reasons=reasons)

    def executed(self, step: ExecutionStep, proposal: Proposal, good: bool,
                 severe: bool) -> None:
        """An AUTONOMOUS or AUDIT step the gateway ran: count it and observe its outcome."""
        assert step.decision is not None
        audited = step.route == Route.AUDIT
        if audited:
            self.metrics.audit_executions += 1
        else:
            self.metrics.autonomous_executions += 1
        if not good:
            self.metrics.failures_after_autonomy += 1
            if self.first_bad_after_change is None and (self.defected or (
                self.cfg.shift_at is not None and self.t >= self.cfg.shift_at
            )):
                self.first_bad_after_change = self.t
        if severe:
            self.metrics.severe_after_autonomy += 1
        if self.cfg.comparator == "legacy_wilson_seeded":
            self.legacy_adjudicate(proposal, good, severe, autonomous=True)
        else:
            if severe:
                provenance = Provenance.AUDIT if audited else Provenance.AUTONOMOUS
                self.manager.record_severe(proposal.partition, provenance, "severe_outcome")
            if audited:
                # the loop froze it at commit, under the decision id; only its label is
                # the oracle's to give. An AUTONOMOUS execution is not frozen (L6)
                self.schedule_label(step.decision.decision_id, proposal.partition,
                                    proposal.proposal_digest, good)
        self.record_trace(step.route.value, proposal, step.route, True, good, severe,
                          lower=step.decision.lower_bound)

    def human_path(self, bundle: ProposalBundle, step: ExecutionStep, good: bool,
                   severe: bool) -> None:
        """A step awaiting approval: the reviewer rejects it or approves and re-runs it."""
        assert step.decision is not None
        proposal = bundle.proposals[0]
        trial_id = step.decision.decision_id
        trial = self.shadow.trial(trial_id)
        frozen = trial is not None and trial.provenance == Provenance.SHADOW
        if frozen:
            # the loop froze it before the run returned to ask for approval (O-6)
            self.metrics.shadow_trials += 1
            self.schedule_label(trial_id, proposal.partition, proposal.proposal_digest, good)
        self.metrics.human_reviews += 1
        caught = (not good) and self.rng.random() < self.cfg.reviewer_catch
        if frozen:
            action = ReviewAction.REJECT if caught else ReviewAction.APPROVE
            self.shadow.record_human_review(trial_id, "reviewer", action)
        if caught:
            self.metrics.human_rejections += 1
            self.record_trace("human_reject", proposal, Route.HUMAN_APPROVAL, False, good, severe)
            return
        approved = self.execute(bundle, self.approval(proposal))
        if not approved.executed:
            self.metrics.approved_refused += 1
            self.record_trace("approved_refused", proposal, approved.route, False, good, severe,
                              reasons=(approved.error or "",))
            return
        self.metrics.human_approved_executions += 1
        if not good:
            self.metrics.failures_human_missed += 1
        if severe and self.cfg.comparator == "v2":
            self.manager.record_severe(
                proposal.partition, Provenance.HUMAN_APPROVED, "severe_outcome"
            )
        elif self.cfg.comparator == "legacy_wilson_seeded":
            self.legacy_adjudicate(proposal, good, severe, autonomous=False)
        self.record_trace("human_approve", proposal, Route.HUMAN_APPROVAL, True, good, severe)

    def step(self) -> None:
        self.step_burst()
        self.deliver_labels()
        self.maybe_release()
        self.maybe_churn()

        arguments = self.next_draft()
        good, severe = self.draw_truth()
        try:
            bundle = self.propose(arguments)
        except ProposalGenerationError as exc:
            # the host's registry refused the draft (e.g. an amount outside every envelope)
            self.deny(None, (f"PROPOSER: {exc}",))
            self.observe_state()
            self.t += 1
            return
        proposal = bundle.proposals[0]
        if proposal.partition != make_key(self.prompt_version, proposal.partition.risk_class):
            raise AssertionError("the proposer's partition key drifted from make_key")
        step = self.execute(bundle)
        decision = step.decision
        if step.executed:
            self.executed(step, proposal, good, severe)
        elif decision is None:
            self.deny(proposal, (f"EXECUTOR: {step.error}",))  # refused before authority
        elif decision.route == Route.DENY:
            self.deny(proposal, decision.reason_codes, decision.route)
        elif decision.route == Route.HUMAN_APPROVAL:
            self.human_path(bundle, step, good, severe)
        elif step.error == "authorization withdrawn before redemption":
            # issued and refused at redemption in the same run (a guard such as a budget);
            # the shipped loop holds no token across steps
            self.metrics.stale_token_denials += 1
            self.record_trace("redeem_refused", proposal, decision.route, False, None, None)
        else:
            self.metrics.issue_denials += 1
            self.record_trace("issue_denied", proposal, decision.route, False, None, None,
                              reasons=(step.error or "",))
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
        self.proposer.prompt_version = self.prompt_version  # the new partition's key
        self.key = make_key(self.prompt_version, "low")
        self.manager.on_churn(old, self.key)
        self.qualified_this_epoch_at = None
        self.pending_labels = [item for item in self.pending_labels if item[2] != old]

    def maybe_release(self) -> None:
        cfg = self.cfg
        if cfg.release_after is None or self.suspended_at is None:
            return
        if self.t - self.suspended_at >= cfg.release_after:
            reason = "simulated post-incident review"
            record = self.manager.record(self.key)
            epoch = record.epoch if record is not None else 0
            self.manager.release(self.key, "operator", reason, self.keyring.sign(
                SIM_KEYS["operator"], release_payload(self.key, epoch, reason)
            ))
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
        # the gateway's own record of tokens spent; handler_calls minus this is the number of
        # effects that ran without one (unauthorized_side_effects)
        m.tokens_consumed = self.db.connection().execute(
            "SELECT COUNT(*) FROM tokens WHERE state IN"
            " ('consumed', 'handler_started', 'handler_completed', 'handler_failed')"
        ).fetchone()[0]
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
