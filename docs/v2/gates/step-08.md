# Gate report — Step 08

**Branch / SHA:** `research/v2`, on top of `ce272f7` (O-2); Step 08 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-07, macOS, Python 3.14.4

**Step objective:** Offline, seeded, reproducible evaluation from `n = 0` across benign, adversarial, and comparator scenarios, with artifacts, figures, and a manifest.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. End-to-end trace from `n=0` to autonomy in an admissible scenario, no seeded history | PASS | `results/stable/trace.jsonl` (first record `n: 0`, state `UNESTABLISHED`; autonomy at step 69 for seed 1); `stable` 10/10 qualified, median 84 steps; `test_stable_reaches_autonomy_from_zero_without_seeding` asserts the store is empty before the run |
| 2. A below-threshold scenario never qualifies; a turnover scenario fails to qualify before invalidation; a delayed-defection scenario exposes a stated limitation | PASS | `never_qualifies` 0/10; `turnover_fast` qualified-before-turnover 0.3 (and `test_fast_turnover_prevents_qualification_before_invalidation` at churn 25); `delayed_defection` 10/10 qualify then 20 failures (3 severe) before suspension, median intervention delay 2, one run never suspended within the horizon; README "Delayed defection is contained, not prevented" |
| 3. Every scenario produces raw results, aggregates, config/seeds, reproducible figures, manifest | PASS | 15 scenarios × (`config.json`, `runs.csv`, `trace.jsonl`, `trace.svg`, `summary.json`) + `results/summary.json`, `results/figures/*.svg` (6), `results/manifest.json` (82 hashed files) |
| 4. Fresh rerun yields identical numeric artifacts; nondeterministic metadata excluded from canonical hash | PASS | Two full `--all` runs: canonical hash `2e9dc589…cbf1a` both times, `files` maps identical; `metadata_excluded_from_canonical_hash` holds `code_sha`, `python`, `platform`, `generated_at`; `test_rerun_reproduces_the_canonical_hash` on reduced config |
| 5. Results report unqualified partitions, errors, censored labels, adverse outcomes | PASS | `final_states` per scenario (e.g. `stable` 7/10 suspended); `time_to_authority.censored_runs`; `censored_fraction_mean` (`censoring` 0.377); `failures_after_autonomy_total`, `severe_after_autonomy_total`, `failures_human_missed_total`, `stale_token_denials_total`; `test_scenario_artifacts_exist_and_report_unqualified_runs` |
| 6. CI/offline tests pass; generalization claims disallowed | PASS | 189 passed (177 + 11 harness + 1 lifecycle) offline; `ruff` and `mypy src` clean; `experiments/README.md` opens with the simulation disclaimer and lists where adjudication is unrealistically strong |
| Required: `pytest -q`; `ruff check . && mypy src`; CLI run twice with hash comparison | PASS | see above |
| Comparators with honestly comparable scopes | PASS | `human_only`, `legacy_wilson_seeded` (marked not a bootstrap; v0.2.0 path, `stale_token_denials = 0`), v2 scenarios |
| Protocol metrics reported | PASS | qualification-before-turnover, time-to-first-authority with censoring, unauthorized side effects (0), policy denials by reason, audit overhead, failures after autonomy, intervention delay with censoring, suspensions/releases/requalifications, censored shadow fraction, human reviews |

**Design and statistical assumptions:** Per scenario in `assumptions_violated` (config and summary). Oracle labels are the truth bit with configurable noise (A5 unrealistically strong); shadow trials are a perfect sample (A3 by construction); severity is observed instantly; the reviewer is an independent coin. All stated in `experiments/README.md`.

**Limitations / deviations:**
- Synthetic throughout; no LLM, no real tool, no network. No claim generalizes beyond the modelled scenarios.
- Full run takes ~4 minutes (lifecycle re-reads all shadow trials per decision: O(horizon²)). Not optimized; it is the production path.
- Figures are hand-rendered SVG (no matplotlib in the environment; keeps bytes deterministic). Plain bar/line charts, not styled for print.
- `audit_rate_*` scenarios cannot show a benefit of auditing because outcomes are observed perfectly anyway; they show only that auditing changes labelling, not execution.
- Costs are counts; no monetary or latency model (protocol asked for "costs by risk class with explicit assumptions": reported as review counts and denial counts, nothing finer).
- The committed `results/` (1.9 MB) are tied to the code SHA in the manifest; any later change to `src/` must regenerate them or the manifest's `code_sha` is stale.
- 25 files touched (new: `experiments/` package of 4 modules + 15 configs + README, `tests/test_experiments.py`, `results/`, this report; modified: `lifecycle.py`, `test_lifecycle.py`, `pyproject.toml`). Far over the 5-file guideline; the harness is one unit.
- `results/manifest.json` as first committed recorded `code_sha = ce272f7` (HEAD at generation, with this step's `lifecycle.py` fix still uncommitted in the tree). The results were regenerated after the Step 08 commit, with an identical canonical hash, and the manifest naming that commit was committed separately.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- **Production bug found by the harness and fixed:** `LifecycleManager.record_severe` raised `illegal lifecycle transition: SUSPENDED on SEVERE_FAILURE` when a second severe outcome arrived during a suspension. Now recorded (`ALREADY_SUSPENDED`) without changing state; `test_severe_failure_while_suspended_is_recorded_not_raised`.
- Two harness fixture mistakes during development, both corrected before the committed run and neither affecting `src/`: (a) with a one-step redemption lag no token was ever outstanding when a suspension landed, so `stale_token_denials` was zero everywhere; lag is now 2 steps. (b) The policy's default 2-minute token TTL then expired every token at the 2-step lag (the expiry boundary check working as specified); the harness now sets `token_ttl` explicitly (5 steps) and `test_outstanding_token_is_refused_after_a_suspension` / the `stale < consumed` assertion guard both.
- `stable` ends with 7 of 10 seeds suspended: 3 % failure × 10 % severe over ~160 autonomous executions hits a severe outcome in most runs, and the policy has no automatic release. Correct per spec; a reader expecting "stable means stays autonomous" should read `suspension_cycle`.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 09 — `steps/09_publication.md`, only on explicit request.
