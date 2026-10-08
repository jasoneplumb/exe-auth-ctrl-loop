# Statistical method (Step 04)

Status: contract for the bound used by `Policy.lower_bound` when `estimator = "beta_mixture"` (the default since this step). Implementation: `src/exe_auth_ctrl_loop/sequential.py`. Tests: `tests/test_sequential_gate.py`.

## 1. Estimand and observed data

- Unit: one admitted trial in one partition and one epoch (spec §3).
- Observation: the trial's label, reduced to $X_i = 1$ if `POSITIVE`, else $0$ (`NEGATIVE` and `INCONCLUSIVE` both count as $0$, spec §3 rule 4).
- Estimand: $p$, the probability that a trial drawn from the partition's proposal process receives a `POSITIVE` label from the named oracle within its validity scope (spec §5). This is oracle-adjudicated acceptability. It is **not** real-world execution success; that equivalence is assumption A5 and is unvalidated.
- Statistic after $t$ trials: $S_t = \sum_{i \le t} X_i$, $F_t = t - S_t$.

## 2. Construction

Prior $\pi = \mathrm{Beta}(a, b)$ with $a = b = 1/2$ (Jeffreys). For a candidate value $p \in (0,1)$ define the mixture likelihood ratio

$$M_t(p) = \frac{\int_0^1 q^{S_t} (1-q)^{F_t}\, d\pi(q)}{p^{S_t} (1-p)^{F_t}} = \frac{B(a + S_t,\; b + F_t)}{B(a, b)\; p^{S_t} (1-p)^{F_t}}.$$

Confidence sequence and lower bound:

$$C_t = \{\, p : M_t(p) < 1/\alpha \,\}, \qquad L_t = \inf C_t .$$

$\log M_t(p)$ is convex in $p$ with minimum at $S_t / t$, so $C_t$ is an interval and $L_t$ is found by bisection on $(0, S_t/t]$ (64 steps, deterministic). $L_t = 0$ when $t = 0$ or $S_t = 0$.

## 3. Validity proposition and its assumptions

**Proposition.** Let $X_1, X_2, \dots$ be i.i.d. $\mathrm{Bernoulli}(p)$ for some fixed $p \in [0,1]$. Then for every $\alpha \in (0,1)$,

$$\Pr_p\big(\exists\, t \ge 1 :\ L_t > p\big) \;\le\; \Pr_p\big(\exists\, t \ge 1 :\ M_t(p) \ge 1/\alpha\big) \;\le\; \alpha .$$

*Why.* Under $\mathrm{Bernoulli}(p)$, $M_t(p)$ is a nonnegative martingale with $M_0(p) = 1$ (a mixture over $q$ of likelihood ratios $\prod_i q^{X_i}(1-q)^{1-X_i} / p^{X_i}(1-p)^{1-X_i}$, each of which is a martingale with expectation 1; the mixture preserves this by Fubini). Ville's maximal inequality for nonnegative supermartingales gives $\Pr(\sup_t M_t \ge 1/\alpha) \le \alpha$. The first inequality holds because $L_t > p$ implies $p \notin C_t$, that is $M_t(p) \ge 1/\alpha$.

*Sources (verified against the publications, 2026-10-08).* Ville (1939), *Étude critique de la notion de collectif*, Thèses de l'entre-deux-guerres 218, Gauthier-Villars, for the maximal inequality. Robbins (1970), "Statistical methods related to the law of the iterated logarithm", *Ann. Math. Statist.* 41(5):1397–1409, for Beta-mixture (method-of-mixtures) sequential tests for Bernoulli data. Kaufmann and Koolen (2021), "Mixture martingales revisited with applications to sequential tests and confidence intervals", *JMLR* 22(246):1–44, for the mixture-martingale confidence-sequence form used here. Howard, Ramdas, McAuliffe and Sekhon (2021), "Time-uniform, nonparametric, nonasymptotic confidence sequences", *Ann. Statist.* 49(2):1055–1080, and Ramdas, Grünwald, Vovk and Shafer (2023), "Game-theoretic statistics and safe anytime-valid inference", *Statist. Sci.* 38(4):576–601, for the general framing. The implementation follows the closed form directly; no result beyond the one stated is relied on.

**Assumptions required for the proposition.**

- **A1 (i.i.d. within an epoch).** Labels within one partition and epoch are independent with a common $p$. The proposition is stated only for this case. Correlated bursts (A1 violated) or drift (A2 violated) are outside it; the experiment protocol measures behaviour under them but the bound makes no promise there.
- **A2 (stationarity within an epoch).** Drift is handled by `max_evidence_age` and epoch resets, not by the bound.
- **A3 (no selection of which proposals are trialled).** If the agent or operator can choose which proposals become shadow or audit trials, the trials are not a sample from the proposal process and $p$ is not the deployed rate.
- **A4 (conservative censoring).** `INCONCLUSIVE` and expired labels are counted as $0$, so non-response can only lower $L_t$.
- **A5 (oracle validity).** The label is a valid proxy for the stated estimand within the oracle's scope. Nothing in the bound checks this.

**What the proposition does not say.** It bounds the probability of *ever* declaring $L_t \ge r$ while $p < r$, for a fixed $p$. It says nothing about:

- behaviour of the realised rate after qualification;
- an agent that is compliant until it qualifies and then defects (a change of $p$ at a data-dependent time violates A1/A2; the bound was computed on the earlier regime). The only defences against that are deterministic: per-operation checks, risk envelopes, single-use tokens, severe-failure suspension, and live invalidation (spec §7, Steps 05 and 06). The method MUST NOT be described as "guaranteed under arbitrary adaptation";
- any individual operation;
- partitions other than the one being evaluated (see §5).

## 4. Gate rule, minimum sample, failure response

Autonomy for a partition requires all of: lifecycle state `QUALIFYING`, $t \ge n_{\min}$, $L_t \ge r$ for the risk class, fresh evidence, matching policy version, not retired or lineage-suspended (Step 03). $n_{\min}$ is a policy floor independent of the bound; it does not affect validity but prevents a short lucky run from qualifying at a loose $\alpha$. When the bound is used as a *stopping rule* for qualification, validity is exactly the proposition: the stopping time is arbitrary.

Failure response: if $L_t$ falls below $r$ after qualification, the lifecycle withdraws autonomy (`GATE_LOST_OUTCOME`). This is a monitoring use of the same sequence; the same $\alpha$ covers the union of all looks, so no separate budget is spent.

Counts after an epoch reset start from $S = F = 0$. A new epoch is a new sequence with its own $\alpha$.

## 5. Multiplicity across partitions and risk classes

The error budget is **per partition and per epoch**. Each partition's $\alpha$ is the policy's `alpha` (default 0.05). No joint claim across partitions is made. If a joint statement over $K$ partitions is wanted, the union bound gives at most $K\alpha$; a policy that wants joint error $\alpha^\star$ must set `alpha = `$\alpha^\star / K$ (Bonferroni) and record $K$. Risk classes are fields of the partition key, so they are separate partitions with separate budgets and separate thresholds $r$. No evidence pools across them.

## 6. Time-varying selection and adjudication

Trials are frozen before review (spec §4) and labels are bound to the frozen digest, so a reviewer cannot change which trials exist or how they are labelled. Late labels are censored rather than accepted (Step 02). These are what make A3 and A4 enforceable in the prototype; they do not make A1 or A5 true.

## 7. Policy identity

`estimator` and `alpha` are `Policy` fields. Changing either is a policy change: it requires a new `policy_version`, which retires existing partitions (Step 03). Evidence accumulated under one method is never re-read under another. `wilson_legacy` remains available for comparison and for reproducing v0.2.0 behaviour; it is a fixed-sample interval and, read repeatedly, exceeds its nominal one-sided error rate (illustrated by `test_fixed_sample_wilson_exceeds_its_nominal_rate_when_read_repeatedly`).

## 8. Numerical behaviour

- All quantities are computed in log space with `math.lgamma`, so $t$ up to $10^7$ and $p$ within $10^{-12}$ of 0 or 1 do not overflow.
- $p = 0$ with successes, or $p = 1$ with failures, yields $\log M = +\infty$ (excluded from $C_t$).
- Bisection is fixed at 64 halvings from $[0, S_t/t]$; results are bit-reproducible across runs on the same platform. Cross-platform differences are confined to the last few ulps of `lgamma`.

## 9. Reference vectors

$a = b = 1/2$. Verified in `tests/test_sequential_gate.py` by an independent exact-rational evaluation of $B(a+S, b+F)/B(a,b)$ (no `lgamma`), checking that $\log M(L) = \log(1/\alpha)$ at the tabulated $L$.

| successes | failures | alpha | lower bound |
|---|---|---|---|
| 0 | 0 | 0.05 | 0.0000000000 |
| 1 | 0 | 0.05 | 0.0250000000 |
| 0 | 10 | 0.05 | 0.0000000000 |
| 10 | 0 | 0.05 | 0.6230126490 |
| 20 | 0 | 0.05 | 0.7759938880 |
| 40 | 0 | 0.05 | 0.8733738782 |
| 60 | 0 | 0.05 | 0.9106264163 |
| 99 | 1 | 0.05 | 0.9159776855 |
| 30 | 25 | 0.05 | 0.3327577461 |
| 1000 | 10 | 0.05 | 0.9740847543 |
| 5 | 5 | 0.05 | 0.1175629241 |
| 40 | 10 | 0.05 | 0.5858137807 |
| 60 | 0 | 0.01 | 0.8865245045 |
| 60 | 0 | 0.1 | 0.9212073855 |
| 100000 | 1000 | 0.05 | 0.9887198075 |

For comparison, the fixed-sample Wilson bound ($z = 1.96$) at 60/0 is 0.9398 and at 40/0 is 0.9124; the sequential bound is lower at every $n$, which is the price of validity at every look. With the default policy threshold of 0.90, qualification from a perfect record needs 60 trials under the sequential bound rather than 40.

## 10. Illustrative simulations

`IllustrativeSimulationTests` run fixed-seed Bernoulli sequences with a look after every trial (1500 sequences of 400 trials). They check that the false-authorization frequency at $p \in \{0.90, 0.88, 0.85\}$ against $r = 0.90$ stays well inside $\alpha$, that Wilson read the same way exceeds its nominal rate, and that a truly good partition ($p = 0.97$) qualifies within 400 trials. Margins are set several standard errors wide.

Measured in seeded runs during Step 04 (not a proof; seeds 21 and 22):

| $p$ | horizon | Wilson $z=1.96$, continuous looks | Beta-mixture, $\alpha = 0.05$ |
|---|---|---|---|
| 0.90 | 400 | 0.139, 0.111 | 0.008, 0.004 |
| 0.90 | 2000 | 0.200, 0.187 | 0.011, 0.009 |
| 0.89 | 400 | 0.067, 0.060 | 0.003, 0.002 |
| 0.88 | 400 | 0.039, 0.030 | 0.001, 0.001 |

The nominal one-sided rate for Wilson at $z = 1.96$ is 0.025. Its continuous-look rate at the boundary is four to eight times that and grows with the horizon, which is the optional-stopping problem the reviewers raised. The mixture bound stays under $\alpha$ at every setting, with room to spare: it is conservative, which is the price paid in the 60-versus-40 trial comparison above. These simulations support the proposition under its assumptions; they are not evidence about correlated, drifting, or adaptive data, which Step 08 covers as scenarios with explicit "assumption violated" labels.
