# Gate report — Step 09

**Status:** completed 2026-11-03. Criteria were pre-registered at Step 00 (2026-11-01) before any work and are reported unchanged.

**Step objective:** Statistical evaluation (serves F6; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-09-statistics` from `mainline` @ `99b6525`; phase 1 `03cc6de`, phase 2 `e65cd20`, phase 3 `dbd1368` (the code every committed artifact was generated from). Issue #40.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-03, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1, 8 cores

**Owner decision recorded (2026-11-03): two tiers.** Running every family through the shipped loop at 1000 sequences per cell was estimated at 1–2 days of 8-core time per run, times three runs for criterion 6. The owner chose two tiers instead:
1. **Families through a fast driver.** The scenario families run through an estimator-level driver (`experiments/statistics.py`). It reads the shipped Beta-mixture bound and implements each counting rule.
2. **Scenarios through the shipped loop.** The 15 scenarios run through the shipped loop. An agreement check shows that the driver, fed the shipped loop's own trials, reaches the same qualification decision.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Harness drives shipped components; the four L6 differences are gone (each checked by a test) | PASS | `experiments/simulate.py` runs every step through `CrossModelAuthorityLoop`: the shipped OpenAI proposer and Claude executor with fake clients, the controller, lifecycle, shadow log, risk policy, and gateway. `tests/test_experiments.py::ShippedLoopTests` has one test per difference: autonomous executions not frozen or labelled; decisions committed by the executor's hook; audits frozen before their token; human-approved executions through the gateway (counted from the token table). A fifth test is an AST check that the harness calls no `commit_decision`, `freeze`, `issue`, `publish`, `redeem_remote`, `accept_nonce`, `commit`, or `gateway.*`. Four mutations, each restoring one difference, each fail their test |
| 2. All five scenario families in `plan.md` Step 09 run at ≥1000 seeded sequences per cell; results committed with configs | PASS | `experiments/families.json` expands to the five pre-registered families (59 cells), plus one added family (below), 65 cells in all, each at 1000 sequences. `results-v3/statistics/` holds `summary.json`, `cells.csv`, a copy of `families.json`, and `manifest.json`. `test_statistics.py::GridTests` checks the grid against the plan's variants |
| 3. Monotonicity of the Beta-mixture lower bound in successes at fixed n proved in `statistical-method.md` (or the pessimistic-pending proposition withdrawn) | PASS | `docs/v2/statistical-method.md` §11.1 proves the lemma for any prior with a + b ≤ 1 (Jeffreys has a + b = 1). §11.2 states the pessimistic-pending proposition and proves it from the lemma and §3. `BoundTests` checks the computed bound is nondecreasing for every S + F ≤ 400 at α ∈ {0.05, 0.01}, and that the driver's threshold table matches direct evaluation |
| 4. For each candidate estimator change: false-authorization rate with and without, and median trials to qualify at p = 0.97; owner decides O-3, O-4, O-8 from this table | PASS (owner decision pending) | Table below |
| 5. Every scenario that violates A1–A5 is labelled so in its summary | PASS | Each family cell's summary carries `assumptions_violated`, `assumptions_hold`, and `claim_scope`; 12 of 65 cells are flagged. `GridTests::test_every_violation_is_labelled` checks the rule. The 15 scenarios keep their `assumptions_violated` from config |
| 6. Three independent full runs give one canonical hash | PASS | Three runs from `dbd1368` in separate processes and output directories, identical on all three outputs: scenarios `03f7babe8d9191dbc058e295eec209a7dfd2efdfdf02e1d7d5e2960441b7fd93`, families `9fbef597ddfaa4134ff1a43aa06c6ad4854aecde7e50056db3e4dab422b67fb7`, `agreement.json` SHA-256 `1c511b7380cadb3103b03aebb87e1858c5a5967af97ce2b449687d41aafb829b`. Both canonical hashes also equal the phase 1 and phase 2 runs, so neither refactor moved a number |
| 7. Global gate passes | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 15 files; 393 passed, 1733 subtests; `example.py`, `denials.py`, `mcp_demo.py` exit 0 |
| Agreement check (two-tier condition) | PASS | `results-v3/agreement/agreement.json`: three configs (fast labels; labels 5 steps late; 2 % missing labels censored at the deadline plus 1 % inconclusive), 50 seeds each. The driver's `first_crossing`, fed each shipped-loop run's own trials rebuilt from its ledger, gives the same time to authority in 150 of 150 runs; 112 of them qualify. `AgreementTests` repeats it on 3 seeds per config and shows a one-step timing error is detected |

## Criterion 4: decision table for O-3, O-4, O-8

1000 sequences per cell. "FA" is the false-authorization rate: the fraction of sequences in which any unit's gate opened while p ≤ r. The O-3 and correlated rows are at p = r = 0.90, the least favourable point of the null. The O-8 and O-4 rows are at p = 0.89, "just below r" as the plan says. "Median trials" is the median number of proposals before the gate opened for a p = 0.97 partition, over units that qualified within the 400-step horizon. The fraction of units that qualified is in parentheses; for multi-unit cells it is over all epochs or partitions. n_min = 30, r = 0.90, α = 0.05, deadline 250 steps. The shipped rule is v0.3.1's; each candidate exists only in the driver until the owner adopts it.

| Cell | Shipped rule: FA [95% CI] | Shipped: median trials (qualified) at p = 0.97 | Candidate: FA [95% CI] | Candidate: median trials (qualified) |
|---|---|---|---|---|
| **O-3 pending trials** (shipped: deadline-only; candidate: pessimistic) | | | | |
| negatives 0 late | 0.012 [0.007, 0.021] | 126 (0.99) | 0.008 [0.004, 0.016] | 126 (0.99) |
| negatives 10 late | 0.018 [0.011, 0.028] | 106 (0.99) | 0.006 [0.003, 0.013] | 126 (0.99) |
| negatives 50 late | 0.393 [0.363, 0.424] | 55 (1.00) | 0.007 [0.003, 0.014] | 126 (0.99) |
| negatives 200 late | 1.000 [0.996, 1.000] | 54 (1.00) | 0.012 [0.007, 0.021] | 126 (0.99) |
| censor negatives as inconclusive | 0.007 [0.003, 0.014] | 126 (0.99) | 0.004 [0.002, 0.010] | 126 (0.99) |
| censor negatives as withhold | 1.000 [0.996, 1.000] | 55 (1.00) | 0.004 [0.002, 0.010] | 126 (1.00) |
| censor positives as inconclusive | 0.000 [0.000, 0.004] | – (0.00) | 0.000 [0.000, 0.004] | – (0.00) |
| censor positives as withhold | 0.000 [0.000, 0.004] | – (0.00) | 0.000 [0.000, 0.004] | – (0.00) |
| censor random as inconclusive | 0.000 [0.000, 0.004] | – (0.00) | 0.000 [0.000, 0.004] | – (0.00) |
| censor random as withhold | 0.003 [0.001, 0.009] | 160 (0.49) | 0.000 [0.000, 0.004] | – (0.00) |
| all labels 2 late (not pre-registered) | 0.008 [0.004, 0.016] | 127 (0.99) | 0.002 [0.001, 0.007] | 163 (0.99) |
| all labels 10 late (not pre-registered) | 0.005 [0.002, 0.012] | 135 (0.99) | 0.000 [0.000, 0.004] | 349 (0.53) |
| all labels 50 late (not pre-registered) | 0.007 [0.003, 0.014] | 175 (0.98) | 0.000 [0.000, 0.004] | – (0.00) |
| **O-8 repeated epochs** (shipped: per-epoch α; candidate: lineage spending) | | | | |
| 1 epoch, lifetime | 0.002 [0.001, 0.007] | 126 (0.99) | 0.003 [0.001, 0.009] | 132 (0.99) |
| 3 epochs, lifetime | 0.007 [0.003, 0.014] | 126 (1.00) | 0.004 [0.002, 0.010] | 175 (0.97) |
| 10 epochs, lifetime | 0.039 [0.029, 0.053] | 126 (0.99) | 0.006 [0.003, 0.013] | 224 (0.91) |
| **O-4 multiplicity** (shipped: per-partition α; candidate: online spending) | | | | |
| K = 1, family-wise | 0.001 [0.000, 0.006] | 126 (0.99) | 0.004 [0.002, 0.010] | 152 (0.99) |
| K = 10, family-wise | 0.034 [0.024, 0.047] | 126 (1.00) | 0.004 [0.002, 0.010] | 224 (0.91) |
| K = 100, family-wise | 0.261 [0.235, 0.289] | 126 (0.99) | 0.001 [0.000, 0.006] | 296 (0.62) |
| O-4 alternative: Bonferroni with declared K (in the candidate columns) | | | | |
| K = 1, family-wise | 0.001 [0.000, 0.006] | 126 (0.99) | 0.005 [0.002, 0.012] | 126 (0.99) |
| K = 10, family-wise | 0.034 [0.024, 0.047] | 126 (1.00) | 0.001 [0.000, 0.006] | 194 (0.96) |
| K = 100, family-wise | 0.261 [0.235, 0.289] | 126 (0.99) | 0.000 [0.000, 0.004] | 243 (0.88) |
| **Correlated failures** (no decision ID; shipped: every trial; candidate: one per block) | | | | |
| block 1, ρ = 0.0 | 0.005 [0.002, 0.012] | 126 (0.99) | 0.006 [0.003, 0.013] | 126 (0.99) |
| block 1, ρ = 0.1 | 0.012 [0.007, 0.021] | 126 (0.99) | 0.008 [0.004, 0.016] | 126 (0.99) |
| block 1, ρ = 0.3 | 0.009 [0.005, 0.017] | 126 (0.99) | 0.011 [0.006, 0.020] | 126 (0.99) |
| block 1, ρ = 0.6 | 0.009 [0.005, 0.017] | 126 (0.99) | 0.011 [0.006, 0.020] | 126 (0.99) |
| block 5, ρ = 0.0 | 0.007 [0.003, 0.014] | 126 (0.99) | 0.003 [0.001, 0.009] | 261 (0.21) |
| block 5, ρ = 0.1 | 0.023 [0.015, 0.034] | 106 (0.99) | 0.004 [0.002, 0.010] | 261 (0.20) |
| block 5, ρ = 0.3 | 0.073 [0.059, 0.091] | 83 (0.98) | 0.002 [0.001, 0.007] | 261 (0.19) |
| block 5, ρ = 0.6 | 0.201 [0.177, 0.227] | 53 (0.96) | 0.002 [0.001, 0.007] | 261 (0.20) |
| block 20, ρ = 0.0 | 0.005 [0.002, 0.012] | 126 (0.99) | 0.000 [0.000, 0.004] | – (0.00) |
| block 20, ρ = 0.1 | 0.111 [0.093, 0.132] | 83 (0.96) | 0.000 [0.000, 0.004] | – (0.00) |
| block 20, ρ = 0.3 | 0.372 [0.343, 0.402] | 53 (0.95) | 0.000 [0.000, 0.004] | – (0.00) |
| block 20, ρ = 0.6 | 0.589 [0.558, 0.619] | 53 (0.95) | 0.000 [0.000, 0.004] | – (0.00) |

What the table says:

- **O-3 (pending trials).**
  - **Shipped rule fails under label-dependent latency.** Under the shipped deadline-only rule, false authorization reaches 0.393 when negatives are 50 trials late, which independently reproduces the claims matrix L2's "about 0.39". It is 1.000 at 200 late, and 1.000 when negatives are withheld to the deadline.
  - **Pessimistic pending holds.** Counting pending trials as failures keeps FA at or below 0.012 in every delay and censoring cell, as §11.2 proves.
  - **Its cost appears only when positives are late too.** That is measured only by the added `symmetric_delays` family. With all labels 10 steps late, 53 % of good partitions qualify within 400 trials (median 349), against 99 % (median 135) under the shipped rule. With all labels 50 late, none qualify.
  - **So the rule is usable only where labels are fast relative to the horizon.**
  - **A labeller suppressing positives** (as inconclusive, or withheld) blocks qualification under both rules. That is a liveness attack, not a safety one.
- **O-8 (repeated epochs).** Per-epoch α gives a lineage lifetime FA of 0.039 over 10 epochs (95 % CI up to 0.053, so not shown to be within α) and growing with E. Lineage spending (α·6/(π²e²) for epoch e) gives 0.006. The cost: the median trials to qualify, over all 10 epochs, rise from 126 to 224, and 91 % of epochs qualify within the horizon.
- **O-4 (multiplicity).** Per-partition α gives a family-wise FA of 0.034 at K = 10 and 0.261 at K = 100, so the C12 caveat is real.
  - **Online spending** by registration order gives 0.001 at K = 100, at a median of 296 trials, and only 62 % of partitions qualify within 400.
  - **Bonferroni** with a declared K gives 0.000 at a median of 243 trials, with 88 % qualifying, but K must be declared in the policy up front.
- **Correlated failures (no decision ID).** With trial-level counting, FA grows with block size and ρ, up to 0.589 at block 20 and ρ = 0.6. One counting trial per block restores FA to at most 0.011, at a large cost. With blocks of 5, 20 % of good partitions qualify within 400 steps. With blocks of 20 none do, because a block-level n reaches only 20 by the horizon, below n_min.

No estimator change is adopted in this step. Each adopted change becomes a `Policy` field, and so a policy-version change (`statistical-method.md` §7), after the owner decides.

**Shipped-loop scenario results:** `results-v3/scenarios/`, with a table in `experiments/README.md`.
- **Defection costs more before it is caught.** `delayed_defection` has 106 failures after autonomy against 20 under the v0.3.x harness, and its median intervention delay is 17.5 steps against 2, because under autonomy only audits are labelled.
- **Suspension and budgets now stop human-approved executions.** Human approvals go through the gateway: `stable` has 1347 approved-then-refused executions on suspended partitions, and in `risk_envelopes` 2462 of 2578 denials carry `BUDGET_EXCEEDED`.
- **Cold start unchanged.** It is identical to v0.3.x: `stable` qualifies in all 10 seeds, median 84 steps.
- **Unauthorized effects are measured.** They are read from the token table and are 0 in every scenario.

**Design and statistical assumptions:**
- **Driver semantics.** One trial per step, and the gate reads trials frozen before the step. A label with delay d is visible from step i + max(d, 1). An unlabelled trial is censored, as a failure, from step i + deadline + 1. These are the shipped harness's semantics, which the agreement check confirms on 150 runs.
- **Exact threshold.** The driver reads the bound through a per-n success threshold. This is exact by §11.1 and is checked against direct evaluation.
- **Seeds.** Each sequence's seed is a string seed derived from the cell id and index (`random.Random(str)`, SHA-512 based). Results do not depend on the number of workers or the chunking (tested).
- **Block counting** freezes the first trial of each block as the counting trial. Blocks are independent, so A1 holds across them. The cost is a factor of B in counted trials.
- **α spending** uses α·6/(π²k²), summing to α over k ≥ 1, for both epochs (O-8) and partitions (O-4).

**Limitations / deviations:**
- **Two tiers** (owner decision, above). The candidate rules are measured only in the driver; through the shipped loop only the shipped rule is measured and agreed.
- **Added, not pre-registered:**
  - The `symmetric_delays` family (6 cells). The pre-registered family delays negatives only, so it cannot show O-3's cost. It is marked `pre_registered: false` in every summary.
  - A Bonferroni variant in `error_budget`, because O-4 names it as the alternative.
  - No pre-registered variant was dropped or changed.
- **Replaced harness tests:**
  - `test_outstanding_token_is_refused_after_a_suspension`: the shipped loop issues and redeems inside one run, so no token is outstanding. Revocation of an outstanding token stays tested in `tests/test_gateway_revocation.py`. The replacement checks that every token is spent or refused in the step it was issued.
  - The `risk_envelopes` test's `RISK_CLASS_MISMATCH` assertion: the shipped proposer derives the risk class, so no mislabel can occur. It now asserts the proposer's refusal of out-of-envelope amounts. `mislabel_rate` is kept so the configs load, and has no effect.
- **Source change:** `executor.py` gains an `Authority` protocol (`evaluate` and `policy`), because the shipped loop already passes a `LifecycleManager` where the type said `AuthorityController`. It is a type change only.
- **Budget:** the plan allows 5 source and test files. This step touched 7: `statistics.py`, `families.json`, `simulate.py`, `run.py`, `executor.py`, `test_statistics.py`, `test_experiments.py`. It also touched `statistical-method.md`, `experiments/README.md`, README, the threat model, this report, and the generated `results-v3/`.
- **`results/` is unchanged** (v0.3.x) until Step 10, because a test pins its hash to the paper-v2 claims matrix, which is not edited.
- **The suite is slower:** about 5 minutes, up from about 2, because harness and agreement tests run through the shipped loop.
- Python 3.10 and 3.13 were not run locally; CI covers them.

**Unexpected behavior / open risks:**
- **Earlier mistakes, all caught:**
  - **Visibility bug:** the first driver draft never made a label with delay 0 visible, because the label was filed under the current step after that step's bucket had been read. It was fixed before any committed run.
  - **Test expectation:** one test expected 63 where the driver correctly gives 62 (delay 10 means visible from step i + 10). The test was wrong.
  - **Fixture choice:** an `audit_rate_20` fixture never qualified at seed 1. It was replaced by `stable` with audit rate 0.2.
  - None of these was a code defect in `src/`.
- **Cost of pessimistic pending.** It is large with slow positive labels and will matter for O-3.
- **Open:** epoch-0 and release epochs are still bound lazily by the first admitted label. The owner decided on 2026-11-03 to bind explicitly at every opening (issue #41), after this step. No Step 09 measurement depends on it, because every run uses one oracle.

**Decision: GO** (recommended; reviewer to confirm). O-3, O-4, O-8 are the owner's to decide from the table above.

**Next permitted step (GO only):** issue #41 (explicit oracle binding), then Step 10, reconciliation and release plan, only on explicit request.
