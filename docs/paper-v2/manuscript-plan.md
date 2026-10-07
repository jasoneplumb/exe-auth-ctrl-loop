# Manuscript plan: paper v2

Scope: a revised manuscript built only on evidence in this repository at `research/v2` `18dae89` (Steps 00–08, gate reports in `docs/v2/gates/`). Each section names the artifacts it draws on; `claims-matrix.md` is the authoritative map. Venue-agnostic. Nothing here is submitted, deposited, or tagged.

## Title and framing

Working title: *Earning Execution Authority from Zero: Shadow Evidence, Anytime-Valid Gating, and Redemption-Time Revocation for LLM Agent Operations.* Framing change from v1: v1 presented a mechanism that assumed a track record existed; v2 presents the full lifecycle, from an empty store through qualification, invalidation, and requalification, and reports where it fails.

## 1. Introduction

- The problem as in v1 (plan-level approval as a bearer credential), kept.
- New: the three objections v1 could not answer, stated plainly: (i) cold start (no path from `n=0` because human-approved outcomes are excluded), (ii) repeated looks at a fixed-sample bound, (iii) issued tokens outliving their justification. Each becomes a mechanism in §4–§6 and a result in §8.
- Contributions, each tied to a claims-matrix row: lifecycle with shadow evidence (C5–C9), anytime-valid gate (C10–C12), redemption-time invalidation and locked consume (C13–C16), host-owned risk scope (C17–C19), commit-before-act audit (C20–C23), reproducible evaluation (C24–C33).
- Non-claims, in the introduction, not buried: no guarantee against strategic defection (L2, C27); no real-outcome estimand (L1); in-process only (L3, L4); simulated evaluation (L5).

## 2. Threat model and trust boundary

Source: `docs/v2/threat-model.md`. Table of trusted (host), potentially adversarial (both models, prompt-injected), fallible (oracle, reviewer), and trusted-with-conditions (RNG, clock) components; the TM-1…TM-12 table; the explicit unsupported adversaries. The statistical/deterministic separation paragraph is to appear verbatim in spirit: the gate answers whether the oracle-positive rate is plausibly above r; the deterministic layer answers what happens when the gate's assumptions fail.

## 3. Architecture (rewritten to start at `n=0`)

Source: `docs/v2/spec.md` §2–§9, `docs/v2/lifecycle.md`. The four states and the transition table (reproduce T-01…T-21 as a figure from `docs/v2/lifecycle_fixture.json`). Provenance classes `SHADOW`, `AUTONOMOUS`, `AUDIT`, `HUMAN_APPROVED` with the rule for each, including inconclusive. The freeze point. The 12-field partition and the lineage rule. The worked example (`worked-example.md`) replaces v1's 198/2 opening; the paper must state that the first autonomous operation in that trace is preceded by 54 human reviews.

## 4. Shadow evidence and the selection-bias firewall

Source: `docs/v2/shadow-evidence.md`, C4, C6, C7. What a shadow trial is, when it is frozen, what the oracle is allowed to be (constraint check, sandbox replay, independent review), and the estimand: **oracle-adjudicated acceptability of the frozen proposal under the oracle's validity scope.** State A5 and that nothing in the system validates it. Report the reason codes a tampering reviewer receives. The admissibility argument is: the reviewer's action is appended after the freeze and refers to it; it cannot change it.

## 5. The statistical gate

Source: `docs/v2/statistical-method.md`. Present §2–§5 of that note: the Beta-mixture martingale, Ville's inequality, the proposition with assumptions A1–A5, the per-partition-per-epoch budget and the Bonferroni statement for joint claims, `n_min` as a floor independent of validity. Include the §9 reference vectors (or a subset) and the §10 table showing Wilson's inflation under continuous looks against the mixture bound's behaviour; label the table illustrative. Related-work placement: the construction is Robbins (1970) / Kaufmann & Koolen (2021); the contribution is its use as the gate in this lifecycle, not the bound. **Citations must be verified against the papers before submission** (gate report Step 04 flags that they were written from memory).

## 6. Redemption-time invalidation and scope

Source: `docs/v2/gateway-redemption.md`, `docs/v2/risk-envelopes.md`. The eight checks at redemption in order; the linearization point; the explicit statement of what does and does not invalidate a token (C13, C14); the in-process scope of the atomicity claim (L3); human approval as informed consent to the state the approver saw (C16). Risk envelopes: host-derived class from arguments, mislabel denial, sequence budgets charged at redemption (C17–C19). State that budgets are per process and have no time window.

## 7. Audit commitment and the ledger

Source: `docs/v2/audit-commitment.md`. The event-order contract (evaluate, commit, issue, execute), the fail-closed commit (C20), what "independent of results" means (R1–R3; C21), and what the hash chain does and does not detect (C22). The anchoring interface is described as a demonstration outside the guarantee. `AUDIT` semantics per O-2 (C23).

## 8. Evaluation (simulated)

Source: `experiments/README.md`, `results/`. Open with the disclaimer and the "unrealistically strong" list. Then:

- Table 1: the scenario table (what varies, assumptions violated).
- Table 2: the results table (qualified fraction, time to authority with censoring, executions, failures after autonomy, suspensions, human reviews, stale-token denials, final states), all 15 rows including the comparators.
- Figure 1: `results/stable/trace.svg` (bound and state over time, seed 1).
- Figure 2: `results/figures/qualified_fraction.svg`; Figure 3: `time_to_authority.svg`; Figure 4: `failures_after_autonomy.svg`; Figure 5: `intervention_delay.svg`.
- Findings, successes and failures side by side, in this order: cold start works and its cost (C24); below-threshold never qualifies (C25); turnover cost (C26); delayed defection contained, not prevented, with the un-suspended run named (C27); shift detected late with 3/10 not withdrawn (C28); censoring and oracle noise block qualification (C29); audit rate changes labelling not behaviour (C30); zero unauthorized effects (C31); the legacy comparator's zero stale denials (C32); bit-for-bit reproducibility (C33).

Every table and figure caption cites the config file, the results directory, the commit, and the canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a`.

## 9. Related work

To be written against sources, with no priority claims. Position relative to:

- Sequential testing and confidence sequences (Robbins; Kaufmann & Koolen; the anytime-valid inference literature): the bound is theirs; the lifecycle around it is the contribution.
- Accumulated-credence / lower-confidence-bound gating of automation and the "earned autonomy" idea in control and robotics: v1 overstated novelty here; v2 states that LCB-gated autonomy is established and that the contribution is externalized authority with evidence-snapshot-bound tokens, provenance-separated evidence, and commit-before-act audit in one coherent loop.
- Capability-based security and single-use tokens; MCP authorization metadata (the repository's own binding).
- Selection bias from human filtering in evaluation data (off-policy evaluation literature) for the firewall argument.
- Audit sampling and commitment schemes for the pre-outcome draw.

## 10. Limitations (honest, in the body, not an appendix)

- The estimand is oracle acceptability, not outcome success (L1). The experiments give the oracle the truth; real oracles do not have it.
- The statistical guarantee is for i.i.d. labels within an epoch (L2). The experiments show correlated bursts, shift, and defection breaking qualification or causing harm before containment (C27, C28). The gate is not a defence against a strategic agent; suspension and scope are, and they act after harm.
- Censoring is conservative by design and makes qualification impossible below roughly 95 % label coverage at these thresholds (C29).
- Single-process prototype: lock-based consume, in-memory evidence, ledger, budgets, and lifecycle (L3, L4). Production requirements are listed, not implemented.
- The ledger proves internal consistency only (C22).
- Two classifiers must be the same object across registry, controller, and gateway; nothing enforces it.
- Simulated evaluation only (L5); no field data; no cost model beyond counts.
- Citations in the method note are unverified at the time of writing.

## 11. Reproducibility appendix

`reproducibility.md` verbatim: environment, commands, expected test count, expected canonical hash, manifest fields.

## Manuscript-ready change log (v1 → v2)

| v1 (v0.2.0, August 2026) | v2 (`research/v2`, proposed 0.3.0) |
|---|---|
| Worked example opens with a hand-seeded 198/2 record | Opens at `n=0`; 54 reviews precede the first autonomous operation |
| No path from empty evidence; human-approved outcomes excluded and nothing else counted | Shadow trials frozen before review and labelled by an oracle; `SHADOW`/`AUDIT`/`AUTONOMOUS` count, `HUMAN_APPROVED` never does |
| Fixed-sample Wilson bound read at every decision | Beta-mixture confidence sequence, valid at every look under stated assumptions; Wilson retained as legacy comparator |
| "Provably independent" audit selection | Ordering guarantee under trusted RNG and controller; chain limits stated |
| Issued tokens survive suspension within TTL | Refused at redemption on suspension, invalidation, policy change, loss of autonomy, retirement, lineage; locked consume |
| Risk class carried in the key, derivation unspecified | Host-derived from arguments via envelopes; mislabel denied; sequence budgets |
| `AUDIT` = pre-execution human review | `AUDIT` = unattended execution plus post-hoc independent label |
| Evaluation: none beyond unit tests | 15 seeded scenarios, two comparators, bit-reproducible artifacts |
| Novelty framed around the bound | Novelty narrowed to the coherent externalized-authority lifecycle |

## Reviewer crosswalk (answers)

| Concern (from `03_REVIEWER_CROSSWALK.md`) | Answer in v2 | Remaining limitation |
|---|---|---|
| Cold start | §3–§4, C5, C24 | Cost: tens of reviews per partition; turnover restarts it (C26) |
| Selective labels | §4, C4, C6 | Reviewer-induced shift of the proposal stream (A3) not addressed |
| Repeated Wilson bounds | §5, C10–C12 | Validity only under A1–A5 |
| Dependence, drift, adaptive attacker | §8 scenarios tagged with violated assumptions, C27–C28 | Harm before containment is real and depends on severity detection |
| Invalidation of issued tokens | §6, C13–C15 | In-process atomicity only |
| Argument/value impact | §6, C17 | Numeric bounds and allow-lists only |
| Sequence-level policy | §6, C18 | No time windows; per process |
| Audit commitment threat model | §7, C20–C22 | Trusted controller; anchoring not provided |
| Weak evaluation | §8 | Simulated; oracle has the truth |
| Prior-art / novelty | §9 | To be written against sources |
| Unexplained 200 observations | `worked-example.md` | — |
