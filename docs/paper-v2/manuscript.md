# Earning Execution Authority from Zero: Shadow Evidence, Anytime-Valid Gating, and Redemption-Time Revocation for LLM Agent Operations

*Draft manuscript, version 2. Built on software release v0.3.0 (commit `78bdf9d`, DOI 10.5281/zenodo.23226913). Bracketed tags such as [C5] refer to rows of `claims-matrix.md`, which maps every claim below to code, tests, data, and commit. Not submitted; no preprint.*

## Abstract

When an LLM agent can call a tool that moves money or deletes a record, the decision to allow the call usually lives in the same conversation that requested it, and a plan approved once becomes a bearer credential. An earlier design (v0.2.0) moved that decision into a host-owned controller that evaluates each operation against a partitioned track record and mints a single-use capability bound to one proposal digest. Reviewers of that design identified three gaps: no partition could ever earn a track record, because human-approved outcomes were excluded and nothing else counted; the fixed-sample confidence bound was re-read before every operation, which inflates its error; and an issued capability outlived the suspension of its partition. This paper presents the revised design and its evaluation. A partition now earns evidence from an empty store through *shadow trials*: the autonomous-path proposal is frozen before a human sees it and labelled afterwards by an independent oracle, so reviewer behaviour cannot shape the sample. An explicit four-state lifecycle governs when evidence may confer autonomy and how it is withdrawn and restored. The evidence gate is the lower endpoint of a Beta-mixture confidence sequence, valid at every look under stated assumptions. Capabilities are re-checked at redemption against live policy, evidence, and lifecycle state under a single consume lock, and risk scope is derived by the host from the operation's arguments rather than from the model's label. Decisions, including the audit draw, are committed to a ledger before any capability exists. In a seeded, bit-reproducible simulation of fifteen scenarios, a 97 %-acceptable partition earns autonomy from zero in a median of 84 steps, each a human review; an 80 %-acceptable one never does; and a partition that behaves until it qualifies and then defects is contained by suspension after a median of two failures, not prevented. We state the estimand the evidence supports, the independence assumptions the gate needs, and the adversaries the design does not cover.

## 1. Introduction

The agent that asks for an operation should not be the agent that decides whether it may run. v0.2.0 of this design [C1] separated the two: a proposer model drafts, an executor model requests, and a host-owned controller decides each operation immediately before it runs, against a track record kept per exact configuration, minting a capability that names one proposal digest, one tool, one effect set, one expiry, and one use [C2]. The gateway holding that capability is the only path to a side effect.

That design could not bootstrap. Its evidence rule counted only autonomous outcomes, deliberately excluding human-approved executions because a reviewer removes exactly the failures the autonomous path would have committed; but autonomy required evidence, so no partition could ever obtain any. Its worked example opened with a hand-written record of 198 successes and 2 failures and never said where it came from. Its confidence bound was a one-sided Wilson interval at fixed $z$, read before every operation: a fixed-sample interval monitored continuously exceeds its nominal error rate, as Section 5 measures. And a capability, once issued, was good until its two-minute expiry regardless of what happened to its partition in the meantime.

This paper closes those gaps and reports what closing them costs. The contributions, each tied to artifacts in the public repository:

1. **Shadow evidence and a lifecycle** (Sections 3–4). Proposals routed to a human are frozen into a shadow log before review and labelled by an independent oracle; `SHADOW`, `AUDIT`, and `AUTONOMOUS` provenance count toward autonomy and `HUMAN_APPROVED` never does [C4–C7]. A four-state lifecycle with a single transition table decides when evidence confers autonomy, withdraws it on one severe outcome, and restores it only through a named human release into a fresh epoch [C8–C9].
2. **An anytime-valid gate** (Section 5). The lower endpoint of a Beta(½,½)-mixture confidence sequence replaces the Wilson bound; for i.i.d. labels within an epoch, the probability that the bound ever exceeds the true rate is at most $\alpha$, over all stopping times [C10–C12].
3. **Redemption-time revocation and host-derived scope** (Section 6). The gateway re-checks policy, prohibited effects, partition health, evidence identity, and every registered guard under one lock, with the consume as linearization point [C13–C16]. Risk class is derived from the operation's arguments by host-owned envelopes; a mislabelled operation is denied before evidence is consulted; sequence budgets are charged only at redemption [C17–C19].
4. **Commit-before-act audit** (Section 7). The decision, with its audit draw, is committed to the ledger before a capability is issued; a failed commit refuses the step [C20]. The independence of audit selection from outcomes is stated as an ordering guarantee under a trusted host, and the hash chain's limits are stated and tested [C21–C22].
5. **A reproducible evaluation** (Section 8). Fifteen seeded scenarios and two comparators drive the real stack from an empty store; artifacts are hash-manifested and reproduce bit-for-bit [C24–C33].

Equally part of the contribution are the non-claims. The evidence estimates oracle-adjudicated acceptability of a frozen proposal, not real-world execution success [L1]. The statistical guarantee holds for independent labels within an epoch; correlated, drifting, and adaptive data lie outside it, and the design's only answer to a strategic agent is containment after harm [L2]. Everything runs in one process with a thread lock, in-memory state, and a ledger that proves internal consistency only [L3, L4]. The evaluation is simulated, with an oracle that knows the truth [L5].

## 2. Threat model and trust boundary

The host process (controller, lifecycle, gateway, evidence store, policy, ledger) is trusted; compromising it voids every guarantee here. Both models are potentially adversarial, including under prompt injection: they produce proposals and requests, and nothing they emit can set a route, a risk class, a label, a frozen digest, or a capability. Tool handlers run only behind the gateway and their outputs are untrusted data. The adjudication oracle and human reviewers are fallible: labels may be wrong or late, and the design handles that by stating an assumption (A5, Section 4) and by counting missing labels against the partition, not by assuming correctness. The audit RNG is trusted host randomness; a seeded RNG, used in tests and experiments, is deterministic and labelled so. The ledger is an in-memory hash chain whose `verify()` detects edits to retained events and not truncation or wholesale replacement by whoever holds the chain.

Twelve threats and their containment are tabulated in the repository's threat model. The ones that shape this paper: selective labels from human filtering (TM-3), optional stopping (TM-4), cold start used as a bypass (TM-5), suspension laundering through configuration churn (TM-6), stale or replayed capabilities (TM-7), concurrent double redemption (TM-8), strategic defection after qualification (TM-9), and an agent biasing which proposals are shadowed or audited (TM-10). The explicitly unsupported adversaries are a compromised host, operator, or reviewer; collusion between oracle and agent; side channels leaking RNG state; and denial of service.

A separation runs through the whole design and is worth stating once. The statistical gate answers one question: is the oracle-positive rate of this configuration plausibly at least the policy's threshold, with error budget $\alpha$ over all stopping times. It assumes A1–A5. The deterministic layer — per-operation evaluation, digest-bound single-use capabilities, live invalidation, host-owned scope, severe-failure suspension — answers a different question: what can happen when those assumptions fail. Neither substitutes for the other, and no result in this paper cites the gate as evidence of robustness against an adversary who defects after qualifying.

## 3. Architecture and lifecycle, from `n = 0`

### 3.1 Partitions

A track record belongs to an exact configuration: a twelve-field key naming both models' providers, versions, and prompt versions, the tool version, policy version, environment version, task category, confidence bin, and risk class. Evidence never pools across any change to any field [C3]. A change produces a *successor* partition that starts in `UNESTABLISHED` with no counts; the predecessor is retired and routes nothing again. One rule prevents the obvious abuse: a successor of a suspended partition inherits the suspension (`lineage_suspended`) and cannot qualify until a recorded human release covers the lineage. Editing a prompt-version string does not clear a suspension.

### 3.2 States and transitions

A partition is in one of `UNESTABLISHED`, `QUALIFYING`, `AUTONOMOUS`, or `SUSPENDED`. The implementation holds one transition table and raises on any pair not in it, so the properties below are structural rather than conventional [C8]:

- The only entry to `AUTONOMOUS` is `QUALIFYING` on `GATE_MET`, with every guard true: $n \ge n_{\min}$, lower bound at or above the risk class's threshold, evidence fresh, policy version matching, not retired, not lineage-suspended, evidence not declared invalid.
- `SUSPENDED` is entered from any other state on a severe outcome of any provenance, including a human-approved execution, and before any counting [C9]. A second severe outcome while suspended is recorded and does not restart anything.
- `SUSPENDED` exits only on `HUMAN_RELEASE`, with a named actor and a stated reason, and only to `QUALIFYING`. The epoch increments and counting restarts from zero: trials frozen before the release, including everything accrued during the suspension, never count.
- `AUTONOMOUS` falls back to `QUALIFYING` when the bound falls below policy after an outcome or when the evidence exceeds its maximum age; the evidence is kept.

Every transition is a ledger event carrying the states, the event, the reason codes, the epoch, and the evidence id and version the move relied on; the evidence is published before the move so the record names what the gate read.

### 3.3 The worked example

The companion document `worked-example.md` walks seed 1 of the `stable` scenario and its variants, replacing the v1 example. In summary: the first proposal arrives at `n = 0` and routes to a human with `NO_EXACT_EVIDENCE`; the oracle's label for that frozen proposal lands two steps later and the partition becomes `QUALIFYING`; after 54 human reviews and 53 shadow positives the bound reaches 0.9005 and the partition becomes `AUTONOMOUS` at step 54; 29 autonomous and 3 audited operations run; a severe outcome at step 87 suspends it and the capability outstanding at that moment is refused at redemption; with an operator release at step 117 the partition requalifies from fresh evidence at step 201; and with a prompt-version change instead, the successor accrues 198 positives and stays `QUALIFYING` because it inherited the suspension [C5, C3].

## 4. Shadow evidence and the selection-bias firewall

### 4.1 What a shadow trial is

When the controller routes a proposal to a human, the proposal is first frozen: its digest and partition are appended to the ledger (`shadow.frozen`) before the reviewer sees it. That freeze point is the commitment. The reviewer may then approve, reject, or rewrite; their action is appended as a separate record that refers to the frozen digest and can change nothing about it [C6]. A rewrite is a different proposal, and a label submitted for the rewritten digest is quarantined as `DIGEST_MISMATCH`. Duplicate trial ids, labels for the wrong partition, second labels for the same trial, labels timestamped before the freeze, and labels after the deadline are each refused with a named reason, and a late label additionally censors the trial so a favourable label cannot be held back until convenient.

The label itself comes from an oracle with a declared identity, version, and validity scope. Three kinds are permitted: a deterministic constraint check of the frozen proposal against its success criteria and host policy; a replay of the proposed action in a controlled environment with no real side effect; or an independent reviewer who sees only the frozen form. Labels are `POSITIVE`, `NEGATIVE`, or `INCONCLUSIVE`.

### 4.2 Provenance and the firewall

Four provenance classes are counted separately. `SHADOW` is a frozen autonomous-path candidate labelled by the oracle. `AUTONOMOUS` is a real unattended execution. `AUDIT` is an unattended execution that the pre-outcome draw also selected for labelling (Section 7). `HUMAN_APPROVED` is any execution that happened because a human approved it. The first three count toward the gate; the fourth never does, in any state [C4]. The reason is the one v1 gave and v2 keeps: a reviewer removes exactly the failures the autonomous path would have committed, so a record of approved executions is evidence of the reviewer, not of the configuration. Discarding that signal is the price of keeping the estimator on-policy.

Inconclusive labels count in $n$ and never in successes; they behave as failures for the bound. A trial not labelled by the deadline becomes inconclusive automatically, and the shadow log expires overdue trials before every publish, so withholding a label cannot keep a bad trial out of the bound [C7]. The censored fraction is reported with every snapshot. This is the conservative choice, and Section 8 shows what it costs.

### 4.3 The estimand

Let $p$ be the probability that a proposal drawn from the partition's proposal process is labelled `POSITIVE` by the named oracle within its validity scope. That is what the evidence estimates: oracle-adjudicated acceptability of the frozen proposal [L1]. It is not real-world execution success. The two coincide only under assumption A5, that the oracle's labels are valid for the deployed outcome within the declared scope, and nothing in the system checks A5. Every result in this paper is a statement about $p$.

## 5. The statistical gate

### 5.1 Construction

After $t$ admitted trials with $S_t$ positives and $F_t = t - S_t$ non-positives, and a Beta$(a,b)$ prior with $a = b = \tfrac12$, define for a candidate rate $p$

$$M_t(p) = \frac{B(a + S_t,\; b + F_t)}{B(a,b)\, p^{S_t}(1-p)^{F_t}}, \qquad C_t = \{\,p : M_t(p) < 1/\alpha\,\}, \qquad L_t = \inf C_t .$$

$\log M_t(p)$ is convex in $p$ with its minimum at $S_t/t$, so $C_t$ is an interval and $L_t$ is found by a fixed 64-step bisection; $L_t = 0$ when $t = 0$ or $S_t = 0$. The implementation is pure arithmetic in log space and is deterministic [C10].

### 5.2 The claim and its assumptions

**Proposition.** If $X_1, X_2, \ldots$ are i.i.d. Bernoulli$(p)$, then for every $\alpha \in (0,1)$,
$$\Pr_p\big(\exists\, t:\ L_t > p\big) \;\le\; \Pr_p\big(\exists\, t:\ M_t(p) \ge 1/\alpha\big) \;\le\; \alpha .$$

Under Bernoulli$(p)$, $M_t(p)$ is a nonnegative martingale with $M_0 = 1$, a mixture over $q$ of likelihood ratios each of which is a martingale with mean one; Ville's inequality (Ville, 1939) bounds the probability that a nonnegative supermartingale ever reaches $1/\alpha$ by $\alpha$; and $L_t > p$ implies $p \notin C_t$. The construction is Robbins's (1970) method of mixtures for Bernoulli data, in the confidence-sequence form of Kaufmann and Koolen (2021); the general framework is surveyed by Howard et al. (2021) and Ramdas et al. (2023). The contribution here is where the bound sits, not the bound.

The assumptions, each stated with every reported result: **A1** labels within a partition and epoch are independent with a common $p$; **A2** the proposal process is stationary within an epoch, with drift handled by evidence expiry and epoch resets rather than by the bound; **A3** which proposals become trials is not chosen by the agent or operator, so the trials are a sample of the deployed process; **A4** censoring is conservative, so non-response cannot raise $L_t$; **A5** the oracle is valid for the estimand.

What the proposition does not say matters as much. It bounds the probability of *ever* granting autonomy while the true rate is below the threshold, for a fixed $p$. It says nothing about an agent whose $p$ changes at a data-dependent time; that is a change of regime, the bound was computed on the earlier one, and the only defences are deterministic [L2]. It says nothing about any individual operation. And it is per partition and per epoch: joint claims across $K$ partitions require $\alpha/K$ each, which the policy can set, and are not made here [C12].

### 5.3 Why not Wilson

The v1 bound was a one-sided Wilson interval at $z = 1.96$, nominally 2.5 % at a single look. Read after every trial at the boundary $p = r = 0.90$ for 400 trials, seeded simulations put its false-authorization rate at 11–14 %, rising to 19–20 % at 2000 trials; the mixture bound stayed at or below 1.1 % at every setting tried [C11]. The price is conservatism: a perfect record needs 53 positives to clear 0.90 under the mixture bound against 40 under Wilson, and 297/3 to clear 0.95 against 198/2. The policy keeps Wilson available as `wilson_legacy`, for comparison and for reproducing v0.2.0 behaviour, and nothing else uses it.

Fifteen reference vectors are tabulated in the method note and verified in the test suite by an exact-rational evaluation of the Beta ratio that never calls `lgamma`, so the bisection and the special-function path check each other.

## 6. Redemption-time revocation and host-derived scope

### 6.1 A capability dies with its justification

A capability names its proposal digest, tool, effect set, partition, policy version, the evidence id and version it was judged on, its expiry, and whether a human approved it. At redemption the gateway, holding a single lock, checks in order: the capability exists and is neither used nor revoked; it has not expired (refused *at* the expiry instant); the digest, tool, and effects match; the current policy's version matches and prohibits none of the requested effects; the partition's snapshot is neither suspended nor declared invalid; for an evidence-based capability, the evidence record still exists under the same id; and every registered guard returns no veto. Then, still under the lock, it marks the capability used — the linearization point — and lets each guard commit; the handler runs after the lock is released [C13, C15]. Checks are collected rather than short-circuited, so a denial names every reason.

What withdraws a capability: suspension, declared invalidation, replacement or removal of the evidence record, a policy version change, an effect becoming prohibited, the lifecycle leaving `AUTONOMOUS`, retirement, lineage suspension, and explicit revocation. What does not: an ordinary evidence increment, a new label folded into the same record while the partition stays healthy [C14]. The capability was issued for one already-decided operation; the next operation gets a fresh decision on the new counts. Retirement is written into the evidence store as a declared invalidation, so a gateway holding only the store refuses a retired partition's capability without the lifecycle being registered as a guard.

Human approval overrides a *route*, never a *prohibition*: the gateway refuses to issue for a prohibited effect whatever the route and whatever the signature. A human-approved capability is subject to the policy and guard checks like any other and survives a suspension only if the approving decision already saw the suspended snapshot — consent given in view of the state, not before it [C16]. One deviation from exact snapshot binding is stated: a human-approved capability survives replacement of its evidence record by a *healthy* successor, because the approval is consent to one operation rather than reliance on a track record.

The concurrency claim is scoped precisely. Two redemptions of one capability reach the handler at most once, a second redeemer is refused while the first handler is still running, and a revoke and a redeem are ordered, never interleaved; all demonstrated with barrier- and event-based tests, not sleeps. This holds for threads within one process. Nothing is claimed across processes, hosts, or restarts, where consumption must be transactional in a durable store [L3].

### 6.2 Risk scope is the host's question

The partition key carries a risk class, and evidence never pools across classes. What v1 left unspecified was who decides the class. In v2 a *risk envelope* is a versioned host predicate over one tool's arguments — numeric bounds, allow-lists such as permitted payees — ranked so that the lowest admitting envelope names the class; arguments no envelope admits have no class and are denied, never defaulted. Classification reads the tool name and the arguments and nothing else: not the model's confidence, not its stated class, not any proposal text. A proposal whose partition names a class other than the one the host derives is denied as mislabelled before evidence is consulted, and a human cannot approve it, because a human cannot change what the operation is [C17]. The registry is the single source of classification: a registry with a risk policy attached refuses tools that carry their own class, the proposer builds the partition key from it, and the executor re-derives and compares before each call.

Sequence budgets cap a summed argument or an operation count per partition, globally, or per argument value. A decision *projects* a charge against what has been committed; a redemption re-projects under the gateway lock and, if it fits, consumes and charges in one critical section. Several decisions may each look affordable and only the redemptions that fit commit [C18]. Because the same policy is a redemption guard, a rollback to a stricter envelope or a tightened budget denies an already-issued capability with the reason named [C19]. Budgets live in memory, have no time window, and are per policy instance [L4].

## 7. Commit-before-act and the ledger

### 7.1 Order of events

For every operation: the controller evaluates, realizing the audit draw if the route is eligible; the decision — route, reasons, evidence id and version, audit probability, audit draw, proposal digest — is appended to the ledger; only then is a capability issued; only then does the handler run. If the commit raises, the executor returns a denied step, no capability exists, and no handler runs [C20]. The loop that composes the two models wires the hook, so a real run cannot proceed off the record; an executor used without the loop and without a hook records nothing, which is permitted for offline demonstrations and said so.

An `AUDIT` route is an unattended execution that the pre-outcome draw also selected for labelling. Its frozen form is recorded before the capability exists and labelled afterwards under its decision id; an audit never labelled expires to `INCONCLUSIVE` and counts against the partition, because not labelling what was selected is a failure of the audit process [C23]. Only audited operations are frozen by the loop; labelling an ordinary autonomous execution is optional and done by whoever observes the outcome, since an optional slot that expired to a failure would punish not auditing.

### 7.2 What "independent of results" means

Given that the RNG is trusted, that the controller commits the draw it realized before acting, and that the models can neither observe RNG state nor shape the proposal stream in response to it, the audit draw is a function of nothing the outcome could influence, because it is on the record before the outcome exists. That is an ordering argument under a trusted host [C21]. v1 called this "provably independent"; it is not a proof that a malicious controller could not draw, look at the outcome, and record a different draw. Such a controller is outside the trust boundary, and the hash chain does not catch it.

### 7.3 What the hash chain proves

`verify()` recomputes each event's hash over its body and the previous hash. It detects edits to retained events and deletion of interior events. It does not detect truncation of the tail or replacement of the whole chain by a freshly built one, and the tests assert both [C22]. The chain is held by the same process as the controller, so it is a check against accidental corruption and against a party who can edit entries but not rebuild from genesis. An `anchor()`/`verify_anchor()` pair shows what an externally held head hash would add — detection of truncation below the anchor and rewriting at or before it — and is labelled a demonstration outside the guarantee, because inside one process the same party holds both.

## 8. Evaluation (simulated)

### 8.1 What is simulated and what is not

The harness drives the real controller, lifecycle, shadow log, risk policy, and gateway with a synthetic proposal stream from an empty evidence store. Only four things are simulated: the truth process (whether a proposal is acceptable and whether a failure is severe), the oracle (the truth bit with configurable flips, inconclusives, delay, and missingness), the human reviewer (catches a bad proposal with probability 0.9, independently), and configuration churn. Capabilities are redeemed two steps after issue, so a capability is outstanding whenever the lifecycle changes.

These are unrealistically strong in the system's favour, and the reader should weigh every number against them [L5]: the oracle sees the truth, so A5 holds exactly; every proposal routed to a human is shadowed and the stream does not react, so A3 holds by construction; severity is observed the instant it occurs, so intervention delays are lower bounds; the reviewer is a coin; label missingness is independent of the label's value, which is exactly the selection the censoring rule exists to blunt and is not simulated; one partition and one tool at a time; and costs are counts.

### 8.2 Scenarios

All v2 scenarios start at $n = 0$ with policy $n_{\min} = 30$, thresholds 0.90 (low) and 0.95 (high), $\alpha = 0.05$, audit rate 0.05, seeds 1–10, horizon 400 steps. Table 1 lists them; the comparators are `human_only` (every operation reviewed) and `legacy_wilson_seeded` (the v0.2.0 path with a hand-seeded 99/1 record, Wilson bound, no lifecycle, no live redemption checks, defecting immediately), the latter present to show what the old path does and marked as not a bootstrap [C32].

| Scenario | Varies | Violates |
|---|---|---|
| `stable` | $p = 0.97$ | — |
| `never_qualifies` | $p = 0.80$ | — |
| `correlated_failures` | Markov bursts at $p = 0.40$ | A1 |
| `distribution_shift` | $p$: 0.98 → 0.70 at step 250 | A2 |
| `delayed_defection` | $p = 0.99$ until 20 steps after qualifying, then 0.50 with 10 % severe | A1, A2 |
| `turnover_fast` / `turnover_slow` | prompt churn every 60 / 200 steps | — |
| `audit_rate_0` / `audit_rate_20` | audit rate 0 / 0.20 at $p = 0.95$ | — |
| `risk_envelopes` | 15 % high-envelope amounts, 3 % out of envelope, 30 % of high mislabelled low, $6000 budget | — |
| `censoring` | 30 % labels never arrive, 10 % inconclusive | A4 stressed |
| `noisy_oracle` | 5 % label flips | A5 |
| `suspension_cycle` | 50 % of failures severe; operator release after 30 steps | — |

### 8.3 Results

Table 2 is read from `results/summary.json` (totals over 10 runs; medians over qualified runs with the censored count beside them). Figures 1–5 are `results/stable/trace.svg` and `results/figures/{qualified_fraction, time_to_authority, failures_after_autonomy, intervention_delay}.svg`. Every artifact carries the canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a`, reproduced on seven independent full runs across every code change since the harness was written [C33].

| Scenario | Qualified | Steps to authority (median; censored) | Executions (auto + audit) | Failures after autonomy (severe) | Suspensions | Human reviews | Stale-capability refusals |
|---|---|---|---|---|---|---|---|
| `stable` | 10/10 | 84; 0 | 1509 + 81 | 57 (7) | 7 | 2386 | 18 |
| `never_qualifies` | 0/10 | –; 10 | 0 | 0 | 7 | 4000 | 0 |
| `correlated_failures` | 4/10 | 69; 6 | 88 + 4 | 8 (1) | 3 | 3901 | 7 |
| `distribution_shift` | 9/10 | 54; 1 | 1227 + 69 | 61 (7) | 9 | 2690 | 14 |
| `delayed_defection` | 10/10 | 54; 0 | 178 + 7 | 20 (3) | 10 | 3802 | 13 |
| `turnover_fast` | 8/10 | 84; 2 | 58 + 4 | 3 (0) | 1 | 3925 | 13 |
| `turnover_slow` | 10/10 | 84; 0 | 905 + 46 | 28 (2) | 3 | 3018 | 23 |
| `audit_rate_0` | 5/10 | 230; 5 | 496 + 0 | 20 (4) | 5 | 3498 | 4 |
| `audit_rate_20` | 5/10 | 230; 5 | 420 + 76 | 20 (4) | 5 | 3498 | 4 |
| `risk_envelopes` | 10/10 | 118; 0 | 952 + 50 | 37 (2) | 2 | 2054 | 19 |
| `censoring` | 0/10 | –; 10 | 0 | 0 | 1 | 4000 | 0 |
| `noisy_oracle` | 1/10 | 146; 9 | 139 + 10 | 4 (0) | 1 | 3847 | 4 |
| `suspension_cycle` | 10/10 | 84; 0 | 736 + 45 | 23 (15) | 18 (18 released, 8 requalified) | 3196 | 19 |
| `human_only` | 0/10 | n/a | 0 | 0 | 0 | 4000 | 0 |
| `legacy_wilson_seeded` | seeded | 0 (seeded) | 3046 + 154 | 31 (4) | 4 (v1 path) | 788 | 0 |

**Cold start works, and is slow** [C24]. From an empty store a 97 %-acceptable partition reaches autonomy in a median of 84 steps in all ten seeds, and every one of those steps is a human review; at 95 % it is 230 steps and half the seeds do not make it in 400. The first autonomous operation in the worked trace is preceded by 54 reviews.

**Below threshold never qualifies** [C25]. At 80 %, no seed reaches autonomy; seven of ten end suspended by severe failures among the executions the reviewer approved, which is the policy working on the human path too.

**Turnover is the price of partitioning** [C26]. With churn every 60 steps, 30 % of epochs qualify before the next invalidation (70 % at 200); each churn starts the successor from zero and, if the predecessor was suspended, under a lineage suspension.

**Delayed defection is contained, not prevented** [C27]. All ten defecting runs qualify — 99 % looks excellent — and then suffer a median of two failures before a severe outcome suspends them; one run produced no severe outcome within the horizon and kept failing at 50 % until the bound fell. The gate did nothing here and was never supposed to. Harm before intervention is real, and its size depends on how often a failure is severe, which the deterministic layer does not control.

**Shift is detected late** [C28]. A drop to 70 % after qualification takes a median of 14.5 steps to withdraw autonomy, three of ten runs are not withdrawn within the horizon, and 61 failures occur meanwhile. The bound is built to lose confidence slowly; evidence age and audit rate are the levers, and neither was tuned.

**Censoring and oracle noise are punishing** [C29]. With 30 % of labels missing and 10 % inconclusive, no run qualifies; the rule that an unlabelled trial counts as a failure is what keeps non-response from inflating the bound, and its consequence at these thresholds is that label coverage above roughly 95 % is required. Five percent label noise at a 0.90 threshold drops qualification to one seed in ten. The evidence is only as good as the oracle, as A5 says.

**Audit rate changes what is labelled, not what runs** [C30]. The two audit-rate scenarios execute identical operations with identical outcomes; the difference is 76 operations carrying an independent post-hoc label. The benefit of auditing — catching a defection that the ordinary outcome stream would miss — is not modelled, because outcomes here are observed perfectly anyway.

**Stale capabilities are refused** [C13]. In every v2 scenario, capabilities outstanding across a suspension, a churn, or a loss of autonomy are refused at redemption (4–23 per ten runs). The legacy comparator refuses none; that is the v0.2.0 gap in numbers [C32].

**Scope denies before evidence** [C17]. In `risk_envelopes`, 925 of 4000 proposals are denied at decision time, led by budget exhaustion, mislabelled class, and out-of-envelope amounts, regardless of the partition's record.

**Nothing runs without authority** [C31]. In every scenario, the count of handler invocations equals the count of consumed capabilities plus human-approved executions: zero unauthorized side effects, by construction.

**Suspension is permanent without a human.** In `stable`, seven of ten seeds end suspended: a 3 % failure rate with 10 % of failures severe reaches a severe outcome within roughly 160 autonomous operations in most runs, and the policy has no automatic release. `suspension_cycle` shows the release path: 18 suspensions, 18 releases, 8 requalifications within the horizon.

## 9. Related work

**Sequential testing and confidence sequences.** The gate is the lower endpoint of a mixture-martingale confidence sequence. The construction for Bernoulli data is Robbins's (1970) method of mixtures; Kaufmann and Koolen (2021) give the modern treatment with time-uniform deviation inequalities under adaptive sampling; Howard, Ramdas, McAuliffe and Sekhon (2021) develop time-uniform, nonparametric, nonasymptotic confidence sequences in general; and Ramdas, Grünwald, Vovk and Shafer (2023) survey safe anytime-valid inference, where the validity-at-every-stopping-time property is exactly what a gate re-read before every operation needs. Ville (1939) supplies the maximal inequality. This paper adds nothing to that theory. Its use of it — as the admission criterion of a lifecycle whose evidence is provenance-separated and whose capabilities are bound to the evidence version — is the contribution, and v1's framing that treated the bound itself as novel is withdrawn.

**Gating automation on accumulated confidence.** Admitting an automated agent to unattended operation once a confidence bound on its success rate clears a threshold is an established pattern in control and reliability engineering, and v1 overstated its novelty on this point. What is specific here is externalizing the decision into a host that neither model can reach, keying the evidence by the exact pair of model configurations, and tying each capability to the evidence version it was judged on so that it can be refused when that version is withdrawn.

**Selective labels.** The firewall that excludes human-approved outcomes from the record addresses the problem Lakkaraju, Kleinberg, Leskovec, Ludwig and Mullainathan (2017) name the selective labels problem: when outcomes are observed only for the cases a human decision-maker let through, the observed label distribution is a function of the decision-maker, not of the underlying process. Their setting is evaluation of a predictor against a human; ours is accumulation of a track record for an agent under human screening. Shadow trials are the design's answer: the candidate is frozen and labelled regardless of what the human then does, so the labels are a sample of the unscreened process under assumption A3.

**Capability security.** The gateway's capability is a single-use, scope-bound, unforgeable reference in the sense of object-capability systems (Miller, 2006), and the confused-deputy problem (Hardy, 1988) is the failure mode the per-operation decision is built against: a plan-level approval held by the executor would be exactly a deputy's ambient authority, reusable for a second call the approver never saw. The redemption-time checks extend the capability model with revocation keyed to the justification rather than to the holder.

**Protocol bindings.** The repository carries the authorization as signed metadata on Model Context Protocol `tools/call` requests; that binding is unchanged from v0.2.0 and is not evaluated here.

## 10. Limitations

Stated in the body and collected here, so that no reader has to find them.

- The estimand is oracle acceptability of a frozen proposal, not real-world success [L1]. The experiments give the oracle the truth; a real oracle measures what its validity scope lets it measure, and systematic oracle bias, which would be worse than the symmetric noise simulated, is not studied.
- The statistical guarantee is for independent labels within an epoch [L2]. Correlated bursts, shift, and defection are shown to block qualification or to cause harm before containment. The gate is not a defence against a strategic agent; suspension and scope are, and they act after harm. Intervention delays reported here are lower bounds because severity is observed instantly.
- Censoring is conservative by design and makes qualification impossible below roughly 95 % label coverage at these thresholds.
- The prototype is one process: lock-based consume, in-memory evidence, ledger, budgets, and lifecycle [L3, L4]. Production requirements — durable transactional consume, append-only storage with an external anchor, authenticated approvals, signed capabilities — are listed in the repository and not implemented.
- The ledger proves internal consistency only; anchoring is demonstrated, not provided.
- The same risk-policy object must be attached to the registry, the controller, and the gateway; nothing enforces that.
- The evaluation is simulated with counts for costs and no field data [L5]. No claim generalizes beyond the modelled scenarios.
- Multiplicity across partitions is handled by a stated per-partition budget and is never stressed.
- Human review is modelled as an independent coin and never as a source of proposals; audit benefit is unmodelled because outcomes are observed perfectly.

## 11. Conclusion

The v0.2.0 design moved the authority decision out of the models and bound it to a track record; it could not start, it read its confidence bound in a way that inflated its error, and it let a capability outlive its justification. The v2 design starts from nothing through shadow evidence a reviewer cannot shape, gates on a bound that is valid at every look under assumptions it states, and refuses a capability at the moment of use when the world has changed since it was issued. The simulation shows the design working where its assumptions hold and failing, visibly and with the failure counted, where they do not: tens of human reviews per partition before any autonomy, restarts on every configuration change, no qualification under heavy censoring or a noisy oracle, and harm before containment under defection. Those are the design's costs, and a paper that reports the mechanism without them would be the v1 paper again.

## References

- Hardy, N. (1988). The Confused Deputy (or why capabilities might have been invented). *ACM SIGOPS Operating Systems Review*, 22(4).
- Howard, S. R., Ramdas, A., McAuliffe, J., & Sekhon, J. (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. *The Annals of Statistics*, 49(2), 1055–1080. doi:10.1214/20-AOS1991
- Kaufmann, E., & Koolen, W. M. (2021). Mixture martingales revisited with applications to sequential tests and confidence intervals. *Journal of Machine Learning Research*, 22(246), 1–44.
- Lakkaraju, H., Kleinberg, J., Leskovec, J., Ludwig, J., & Mullainathan, S. (2017). The selective labels problem: Evaluating algorithmic predictions in the presence of unobservables. In *Proceedings of the 23rd ACM SIGKDD International Conference on Knowledge Discovery and Data Mining* (KDD '17). doi:10.1145/3097983.3098066
- Miller, M. S. (2006). *Robust Composition: Towards a Unified Approach to Access Control and Concurrency Control*. PhD thesis, Johns Hopkins University.
- Plumb, J. E. (2026). Evidence-Partitioned Autonomy Authorization for LLM Agents: Single-Use Capability Tokens, a Selection-Bias Firewall, and Pre-Outcome Committed Audit Selection in a Cross-Model Execution-Authority Control Loop. Technical Disclosure Commons, dpubs 11356; doi:10.5281/zenodo.21894658
- Plumb, J. E. (2026). Cross-Model Execution-Authority Control Loop, v0.3.0 [software]. doi:10.5281/zenodo.23226913 (concept doi:10.5281/zenodo.21983061)
- Ramdas, A., Grünwald, P., Vovk, V., & Shafer, G. (2023). Game-theoretic statistics and safe anytime-valid inference. *Statistical Science*, 38(4), 576–601. doi:10.1214/23-STS894
- Robbins, H. (1970). Statistical methods related to the law of the iterated logarithm. *The Annals of Mathematical Statistics*, 41(5), 1397–1409.
- Ville, J. (1939). *Étude critique de la notion de collectif*. Thèses de l'entre-deux-guerres, 218. Gauthier-Villars, Paris.

## Reproducibility

`reproducibility.md` gives the clean-room procedure: install, run the 202-test offline suite, run `python -m experiments.run --all --output <dir>` (about four minutes), and compare `manifest.json → canonical_hash` to the value above. Figures and tables cite their config file, results directory, commit, and that hash.
