# Simulated evaluation (v2 Step 08; shipped loop since v3 Step 09)

**This is a simulation.** Two result sets live in this repository. `results-v3/scenarios/` (v3 Step 09) comes from a synthetic proposal stream driven through the shipped `CrossModelAuthorityLoop`: the OpenAI proposer and Claude executor with fake clients, the controller, lifecycle, shadow log, risk policy, and gateway. `results/` (v0.3.x, kept unchanged until v3 Step 10 regenerates it) came from the earlier harness, which wired the same components differently; see "Where the v0.3.x harness differed from the shipped loop". `results-v3/statistics/` is the estimator-level driver of v3 Step 09 (`experiments/statistics.py`, `families.json`), at 1000 sequences per cell. The models, the human reviewer, the adjudication oracle, and configuration churn are simulated by the few lines in `experiments/simulate.py` that say so. The results establish that the implemented components behave as implemented under the modelled assumptions, wired the way the harness wires them, which differs from the shipped loop in ways some results depend on (see "Where the harness differs from the shipped loop"). Neither set is field validation, not evidence about real LLM agents, and not evidence about safety for high-impact side effects. No claim in this directory generalizes beyond the modelled scenarios.

## Reproduce

```sh
python -m pip install -e '.[dev]'
python -m experiments.run --all --output results-v3/scenarios   # every scenario, shipped loop
python -m experiments.statistics --output results-v3/statistics  # scenario families, 1000 seqs/cell
python -m experiments.run --config experiments/configs/stable.json --output /tmp/stable
pytest -q tests/test_experiments.py tests/test_statistics.py     # reduced-size checks
```

Run `--all` twice into two directories and compare `manifest.json → canonical_hash`. They must be identical; `tests/test_experiments.py::ArtifactTests::test_rerun_reproduces_the_canonical_hash` does this on a reduced configuration. Reduced runs via `--seeds N --horizon H` are for development only; the committed `results/` and `results-v3/` use the full configs. Full-size reproduction has been checked on one platform only (macOS):
- `results/` (v0.3.x): three runs on Python 3.14.4, recorded in `docs/v2/gates/step-08.md` and `step-09.md`.
- `results-v3/`: three runs on Python 3.14.8, recorded in `docs/v3/gates/step-09.md` and `step-10.md`.

CI runs the reduced tests on Linux and does not regenerate either set or compare it with the committed manifests.

The full scenario run takes about ten minutes through the shipped loop (four with the v0.3.x harness), because the lifecycle re-reads every shadow trial at every decision (quadratic in the horizon) and every step goes through the proposer, executor, and gateway; that is the production code path, not a harness shortcut. The scenario families take about five minutes on 8 cores.

## What one simulated step is

One proposal for the current partition, through the shipped loop. In order:

1. Due oracle labels are delivered to the shadow log, signed by the registered oracle principal `sim-oracle`. The host binds each partition's first epoch to `sim-oracle` when it first sees the partition, and each release binds the oracle the operator signs, so no label binds an epoch (issue #41).
2. If a suspension is `release_after` steps old, a simulated operator releases it with a signed release (new epoch).
3. Every `churn_every` steps the proposer's prompt version changes: the old partition is retired and the new one starts at `n = 0`.
4. The simulated OpenAI client returns a draft (amount, payee); the shipped proposer turns it into a bundle and derives the partition, including the risk class, from the host's envelopes. A draft no envelope admits is refused here (`PROPOSER: ...` in `denial_reasons`). The bundle is ledgered by `loop.propose`.
5. The truth is drawn: acceptable with probability `p_good` (modified by `shift_at`, bursts, or defection), severe with probability `severe_given_bad` if not.
6. The simulated Claude client requests the proposal and `loop.execute` runs it. The executor checks it, `LifecycleManager.evaluate` decides, the loop's commit hook records the decision and freezes what the loop freezes (audits as `AUDIT`, human-routed proposals on a non-autonomous partition as `SHADOW`), and the gateway issues and redeems the token in the same run.
   - `DENY`: counted, with the reason codes. An executor refusal before authority is `EXECUTOR: ...`.
   - `AUTONOMOUS` / `AUDIT`: the handler ran. A severe outcome calls `record_severe`. An audit's frozen trial gets an oracle label `label_delay` steps later (or never, with probability `label_missing`); an autonomous execution is not frozen and gets no label. A token refused at redemption (a budget, a lifecycle guard) is `stale_token_denials`.
   - `HUMAN_APPROVAL`: the run returns awaiting approval. If the loop froze it, its label is scheduled. A simulated reviewer catches a bad proposal with probability `reviewer_catch` and rejects it; otherwise the reviewer signs an `Approval` and the bundle runs again through the gateway on that approval. A human-approved execution the gateway refuses (suspended partition, budget) is `approved_refused`. Review actions on a frozen trial are recorded, never changing it.
7. The lifecycle state is observed and the metrics updated.

Oracle: the truth bit, flipped with probability `oracle_flip`, `INCONCLUSIVE` with probability `oracle_inconclusive`, delivered after `label_delay` steps, missing with probability `label_missing` (then expiring to `INCONCLUSIVE` at `label_deadline`). `tokens_consumed` is read from the gateway's token table, so `unauthorized_side_effects` (`handler_calls` minus it) is measured, not zero by construction.

## Where the simulation is unrealistically strong

Read these before reading any number.

- **The oracle sees the truth.** A shadow label is the true acceptability bit with optional symmetric noise. A real oracle (constraint check, sandbox replay, reviewer) measures proposal acceptability under its validity scope, not real-world outcome (spec assumption A5). The `noisy_oracle` scenario shows how little noise it takes to block qualification; it does not model systematic oracle bias, which would be worse.
- **Shadow trials are a perfect sample of the deployed process.** Every proposal routed to a human is shadowed, and the proposal stream does not react to being shadowed (A3 holds by construction). An adaptive agent could violate this.
- **Severity is observed instantly and perfectly.** A severe outcome calls `record_severe` at redemption. Real detection has delay and misses; `intervention_delay` is therefore a lower bound on real intervention time.
- **The reviewer is a coin.** A human catches a bad proposal with fixed probability `reviewer_catch` (0.9) independent of everything else, and never introduces proposals of their own.
- **Labels arrive on a fixed schedule.** Delay and missingness are independent of the label's value. Non-random missingness (bad outcomes less likely to be labelled) is exactly the selection problem the censoring rule is meant to blunt, and it is not simulated.
- **One partition at a time, one tool.** No cross-partition interactions beyond churn lineage; the multiplicity policy (one α per partition and epoch) is never stressed.
- **Costs are counts.** Review cost is `human_reviews`; harm is `failures_after_autonomy`. No monetary model, no latency.

## Where the v0.3.x harness differed from the shipped loop

Since v3 Step 09 the harness calls no authority API itself: every commit, freeze, issue, and redemption happens inside the shipped loop. `tests/test_experiments.py::ShippedLoopTests` checks one difference per test, and four mutations, each restoring one difference, each fail them. The numbers in `results/` (and in the v0.3.x tables below) came from the earlier wiring, and some depend on it:

- **It labelled every autonomous execution.** The shipped loop freezes only audits, so under autonomy a failure reaches the bound only if it was audited. The v0.3.x withdrawal speeds in `delayed_defection` and `distribution_shift`, and the finding that the audit rate changes nothing that executes, depend on this.
- **It committed decisions itself** rather than through the executor's commit-before-act hook.
- **It froze audits after execution**; the shipped loop freezes an audit before the token is issued.
- **Its human path bypassed the gateway**, so a human-approved execution ran even on a suspended partition or over budget, and `unauthorized_side_effects` was zero by construction on that path.
- **It held tokens across steps** (`redeem_delay`), which the shipped loop never does: it issues and redeems inside one run. Revocation of an outstanding token is tested on the gateway (`tests/test_gateway_revocation.py`), not in the harness.
- **It injected mislabelled proposals** (`mislabel_rate`). The shipped proposer derives the risk class from the host's envelopes, so no proposal can carry a mislabel; the field is kept so the configs load and has no effect.

## Scenarios

All v2 scenarios start every partition at `n = 0` with an empty evidence store. Policy defaults: `n_min = 30`, required bound 0.90 (`low`) / 0.95 (`high`), α = 0.05, Beta-mixture bound, audit rate 0.05, token TTL 5 steps. Seeds 1–10, horizon 400 steps.

| Scenario | What it varies | Assumptions violated |
|---|---|---|
| `stable` | `p_good = 0.97` | none |
| `never_qualifies` | `p_good = 0.80` | none |
| `correlated_failures` | Markov bursts, `p_good_in_burst = 0.40` | A1 |
| `distribution_shift` | `p_good` 0.98 → 0.70 at step 250 | A2 |
| `delayed_defection` | `p_good = 0.99` until 20 steps after qualifying, then 0.50 with 10 % severe | A1, A2 |
| `turnover_fast` / `turnover_slow` | prompt version churn every 60 / 200 steps | none |
| `audit_rate_0` / `audit_rate_20` | audit rate 0 / 0.20 at `p_good = 0.95` | none |
| `risk_envelopes` | 15 % high-envelope amounts, 3 % out of envelope, 30 % of high mislabelled `low`, $6000 partition budget | none |
| `censoring` | 30 % labels never arrive, 10 % inconclusive, delay 5 | A4 stressed |
| `noisy_oracle` | 5 % label flips | A5 |
| `suspension_cycle` | 50 % of failures severe; operator release after 30 steps | none |
| `human_only` | comparator: every operation reviewed, no autonomy | n/a |
| `legacy_wilson_seeded` | comparator: v0.2.0 path, hand-seeded 99/1, Wilson bound, no lifecycle, no live redemption checks, defects immediately | A1, A2 |

`legacy_wilson_seeded` is **not a bootstrap strategy**. Its partition is autonomous at step 0 because a snapshot was written by hand, which is what the v0.2.0 examples did and what the reviewers objected to. It is here to show what that path does, including that its gateway never refuses an outstanding token after a suspension (`stale_token_denials = 0`).

## v3 results (`results-v3/scenarios/`: seeds 1–10, horizon 400, the shipped loop, the decided rules)

Same configs, seeds, and metrics as the v0.3.x table below, through the shipped loop, under the default `Policy`. Since issue #43 that default is pessimistic pending, online α spending by partition registration order, and lineage spending across epochs (owner decisions O-3, O-4, O-8 from v3 Step 09). Step 09's run of the same scenarios under the v0.3.1 rules is recorded in `docs/v3/gates/step-09.md`. Totals over the 10 runs; medians over qualified runs, censored count beside them. The canonical hash is in `results-v3/scenarios/manifest.json`.

| Scenario | Qualified | Steps to authority (median; censored) | Autonomous + audit executions | Failures after autonomy (severe) | Suspensions | Human reviews | Human-approved executions | Approved, then refused | Redemption refusals | Unauthorized effects | Final states |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `stable` | 10/10 | 139; 0 | 1006 + 39 | 32 (4) | 4 | 2950 | 1963 | 904 | 5 | 0 | 5 autonomous, 1 qualifying, 4 suspended |
| `never_qualifies` | 0/10 | –; 10 | 0 | 0 | 7 | 4000 | 1979 | 1280 | 0 | 0 | 3 qualifying, 7 suspended |
| `correlated_failures` | 1/10 | 139; 9 | 172 + 10 | 9 (1) | 3 | 3815 | 3330 | 189 | 3 | 0 | 7 qualifying, 3 suspended |
| `distribution_shift` | 9/10 | 118; 1 | 912 + 47 | 52 (6) | 7 | 3036 | 1737 | 918 | 5 | 0 | 3 qualifying, 7 suspended |
| `delayed_defection` | 10/10 | 94; 0 | 361 + 11 | 76 (10) | 10 | 3628 | 1052 | 1419 | 0 | 0 | 10 suspended |
| `turnover_fast` | 0/10 | –; 10 | 0 | 0 | 1 | 4000 | 3731 | 164 | 0 | 0 | 10 qualifying |
| `turnover_slow` | 9/10 | 139; 1 | 343 + 15 | 13 (4) | 5 | 3642 | 2639 | 908 | 0 | 0 | 3 autonomous, 5 qualifying, 2 suspended |
| `audit_rate_0` | 4/10 | 284; 6 | 449 + 0 | 23 (0) | 1 | 3551 | 3225 | 161 | 0 | 0 | 4 autonomous, 5 qualifying, 1 suspended |
| `audit_rate_20` | 4/10 | 284; 6 | 98 + 14 | 7 (0) | 1 | 3876 | 3531 | 161 | 12 | 0 | 9 qualifying, 1 suspended |
| `risk_envelopes` | 2/10 | 83; 8 | 47 + 0 | 1 (0) | 1 | 1380 | 1121 | 228 | 2 | 0 | 9 qualifying, 1 suspended |
| `censoring` | 0/10 | –; 10 | 0 | 0 | 1 | 4000 | 3761 | 143 | 0 | 0 | 9 qualifying, 1 suspended |
| `noisy_oracle` | 1/10 | 212; 9 | 111 + 4 | 0 (0) | 1 | 3883 | 3619 | 164 | 2 | 0 | 9 qualifying, 1 suspended |
| `suspension_cycle` | 10/10 | 166.5; 0 | 502 + 18 | 17 (9) | 13 | 3478 | 3052 | 340 | 2 | 0 | 2 autonomous, 6 qualifying, 2 suspended |
| `human_only` | 0/10 | n/a | 0 | 0 | 0 | 4000 | 3890 | 0 | 0 | 0 | human only |
| `legacy_wilson_seeded` | seeded | 0 (seeded) | 2410 + 121 | 23 (6) | 0 | 1469 | 1456 | 0 | 0 | 0 | 4 seeded-autonomous, 6 suspended |

What the decided rules change, against the v0.3.x harness and Step 09's v0.3.1-rule run:

- **Qualification costs more, as Step 09 predicted.**
  - **Cold start:** `stable` qualifies in all 10 seeds at a median of 139 steps, against 84. Partition 1's α is 0.05·(6/π²)² ≈ 0.018, so a perfect record needs 63 trials, not 53.
  - **Pending trials:** a pending trial counts as a failure until its label arrives two steps later.
- **Churn is expensive under online spending.** Every churned partition registers anew, with a smaller α.
  - `turnover_fast` (churn every 60 steps) now qualifies in no seed, against 8 of 10 under v0.3.1.
  - `turnover_slow` qualifies in 9 of 10.
  - This is the liveness price of a family-wise bound across partitions. It falls hardest on deployments that churn configuration often.
- **Defection is caught sooner.** `delayed_defection` has 76 failures after autonomy, against 106 under v0.3.1, and its median intervention delay is 14 steps, against 17.5.
- **A new effect: redemption refusals of audits.** An audit is frozen at commit, between the decision and the redemption, and under pessimistic pending that new trial counts at once as a failure. On a partition just above the threshold, the gate the lifecycle recomputes at redemption can therefore refuse the audit's own token: 12 refusals in `audit_rate_20`, none in `audit_rate_0`.
  - It fails safe; the refused audit's trial stays a failure until labelled.
  - It means audits on marginal partitions often do not execute. The harness schedules no label for an audit that did not execute, so such a trial expires to `INCONCLUSIVE`.
- **Unchanged:** below-threshold never qualifies; censoring and oracle noise block qualification; human-only and the legacy comparator are unaffected; 0 unauthorized effects everywhere.

## v0.3.x results (`results/`: seeds 1–10, horizon 400, the v0.3.x harness at the commit in `results/manifest.json`)

Totals are over the 10 runs; medians are over qualified runs only, with the censored count beside them.

| Scenario | Qualified | Steps to authority (median; censored) | Autonomous + audit executions | Failures after autonomy (severe) | Suspensions | Human reviews | Redemption refusals | Final states |
|---|---|---|---|---|---|---|---|---|
| `stable` | 10/10 | 84; 0 | 1509 + 81 | 57 (7) | 7 | 2386 | 18 | 3 autonomous, 7 suspended |
| `never_qualifies` | 0/10 | –; 10 | 0 | 0 | 7 | 4000 | 0 | 3 qualifying, 7 suspended |
| `correlated_failures` | 4/10 | 69; 6 | 88 + 4 | 8 (1) | 3 | 3901 | 7 | 7 qualifying, 3 suspended |
| `distribution_shift` | 9/10 | 54; 1 | 1227 + 69 | 61 (7) | 9 | 2690 | 14 | 1 qualifying, 9 suspended |
| `delayed_defection` | 10/10 | 54; 0 | 178 + 7 | 20 (3) | 10 | 3802 | 13 | 10 suspended |
| `turnover_fast` | 8/10 | 84; 2 | 58 + 4 | 3 (0) | 1 | 3925 | 13 | 10 qualifying |
| `turnover_slow` | 10/10 | 84; 0 | 905 + 46 | 28 (2) | 3 | 3018 | 23 | 4 autonomous, 5 qualifying, 1 suspended |
| `audit_rate_0` | 5/10 | 230; 5 | 496 + 0 | 20 (4) | 5 | 3498 | 4 | 1 autonomous, 4 qualifying, 5 suspended |
| `audit_rate_20` | 5/10 | 230; 5 | 420 + 76 | 20 (4) | 5 | 3498 | 4 | same |
| `risk_envelopes` | 10/10 | 118; 0 | 952 + 50 | 37 (2) | 2 | 2054 | 19 | 8 autonomous, 2 suspended |
| `censoring` | 0/10 | –; 10 | 0 | 0 | 1 | 4000 | 0 | 9 qualifying, 1 suspended |
| `noisy_oracle` | 1/10 | 146; 9 | 139 + 10 | 4 (0) | 1 | 3847 | 4 | 9 qualifying, 1 suspended |
| `suspension_cycle` | 10/10 | 84; 0 | 736 + 45 | 23 (15) | 18 (18 released, 8 requalified) | 3196 | 19 | 2 autonomous, 8 qualifying |
| `human_only` | 0/10 | n/a | 0 | 0 | 0 | 4000 | 0 | human only |
| `legacy_wilson_seeded` | seeded | 0 (seeded) | 3046 + 154 | 31 (4) | 4 (v1 path) | 788 | 0 | 6 seeded-autonomous, 4 suspended |

"Redemption refusals" is `stale_token_denials_total`: every token the gateway refused at redemption, whatever the reason, so it includes budget refusals as well as stale tokens.

Other reported quantities (all in `results/<scenario>/summary.json` and per run in `runs.csv`):

- `qualified_before_turnover_fraction` (0.3 fast, 0.7 slow): the fraction of *runs* whose partition is `AUTONOMOUS` at the moment of the first churn. It is set once per run, so it says nothing about later epochs.
- `audit_overhead_mean` and `failures_human_missed_total`.
- `policy_denials_total`, with a per-reason breakdown (`risk_envelopes`: 925, led by `BUDGET_EXCEEDED`, `RISK_CLASS_MISMATCH`, `NO_RISK_ENVELOPE`).
- `censored_fraction_mean` (`censoring`: 0.377): inconclusive plus still-pending shadow trials over all shadow trials, so it counts trials pending at the horizon. The shadow log's own `Counts.censored_fraction`, which counts inconclusive trials only, averages 0.342.
- `intervention_delay`: *steps*, not failures, from the first failure after defection or shift to the first step the partition is no longer `AUTONOMOUS`, whether it went to `QUALIFYING` or `SUSPENDED`. The median is 2 for `delayed_defection` (1 censored: suspended before defecting) and 14.5 for `distribution_shift` (3 censored: suspended before the shift).
- `unauthorized_side_effects_total`: `handler_calls` minus `tokens_consumed`, 0 everywhere. This is zero by construction rather than measured, because the human path increments both counts without calling the gateway.

## What the v0.3.x numbers say, and what they do not

- **Cold start works and is slow.** From an empty store, a 97 %-acceptable partition qualifies in a median of 84 steps, all 10 seeds; at 95 % it is 230 steps and half the seeds do not make it within 400. Every one of those steps is a human review. Gate 1 of the plan is met by `stable` with no seeded history (`results/stable/trace.jsonl` begins at `n = 0`).
- **Below threshold never qualifies.** `never_qualifies` (80 %) reaches autonomy in no seed; 7 of 10 are suspended by severe failures the reviewer missed, which is the policy working, not a defect.
- **Turnover is the cost of partitioning.** With churn every 60 steps, only 3 of 10 runs are autonomous at the first churn, and of all 70 epochs only 13 (19 %) ever reach `AUTONOMOUS`: the fastest qualification takes 54 steps, so an epoch has little room. With churn every 200 steps, 7 of 10 runs are autonomous at the first churn. Every churn starts the partition over. This is the price the design pays for refusing to pool evidence across configurations.
- **Delayed defection is not prevented.** All 10 runs qualify (p = 0.99 looks excellent). Seed 5 is suspended at t=63 by a severe outcome before its defection begins. Of the 9 that defect, 7 lose autonomy to `QUALIFYING` 2 steps after their first failure, when that failure's label arrives and one negative takes the bound below 0.90, and are suspended 24–208 steps later; seeds 1 and 10 are suspended at once because their first failure is severe. All 10 end suspended. The fast withdrawal comes from the harness labelling every autonomous execution (see "Where the harness differs from the shipped loop"); the shipped loop would see the failure only through an audit or an outcome the host chooses to label. Harm before intervention is real, and its size depends on how many failures are labelled and on `severe_given_bad`, neither of which the deterministic layer controls.
- **Shift is detected, with delay.** Six runs are autonomous at the shift (step 250); all six lose autonomy 0–32 steps after their first post-shift failure (median 14.5), four by a severe outcome and two by the bound, with 32 failures in those windows. The three censored runs were suspended before the shift (t=87, 178, 233), one run never qualified, and no run ends autonomous. The 61 failures in the table cover whole runs, before the shift included. Partitions near the threshold also move between `AUTONOMOUS` and `QUALIFYING` repeatedly (up to three times in one run), because a partition qualifies just above 0.90 and one labelled failure takes it back below. `max_evidence_age` and audit rate are the levers, and neither was tuned here.
- **Censoring is punishing.** With 30 % of labels missing and 10 % inconclusive, no run qualifies in 400 steps, and none could: about 0.97 × 0.7 × 0.9 ≈ 0.61 of trials end positive, far below 0.90. The rule that an unlabelled trial counts as a failure is what keeps non-response from inflating the bound; the consequence is that label coverage above roughly 95 % is required at these thresholds.
- **The oracle must be good.** 5 % label noise at a 0.90 threshold drops qualification to 1 in 10. The design's evidence is only as good as the oracle, exactly as A5 says.
- **Audit rate changes a tag, not what runs.** `audit_rate_0` and `audit_rate_20` execute the same operations with the same outcomes, by construction: the harness labels every redeemed operation, so the audit draw decides only whether 76 of those labels carry `AUDIT` rather than `AUTONOMOUS` provenance. The two `trace.jsonl` files are identical only because seed 1 never qualifies in either. The benefit of auditing (a label on an execution that would otherwise have none, and with it a defection the ordinary outcome stream would miss) is not modelled.
- **Outstanding tokens are refused.** Tokens outstanding across a suspension, churn, or loss of autonomy are refused at redemption in every v2 scenario where a partition qualifies (4–23 per 10 runs; 0 in `never_qualifies` and `censoring`, where nothing qualifies). The count includes budget refusals at redemption: 7 of the 19 in `risk_envelopes` are `BUDGET_EXCEEDED`. The legacy comparator refuses none, by construction, because its gateway has no live checks; that shows baseline gap 2 rather than measuring it.
- **Risk scope denies regardless of evidence.** 925 denials in `risk_envelopes` across 4000 proposals, with mislabelled and out-of-envelope amounts refused at decision time regardless of the partition's record.
- **Suspension is permanent without a human.** In `stable`, 7 of 10 seeds end suspended because a severe failure occurred (3 % failures × 10 % severe over ~160 autonomous operations). That is the policy as written. `suspension_cycle` shows the release path: 18 suspensions, 18 releases, 8 requalifications within the horizon.

## Artifacts and manifest

```
results/
  <scenario>/config.json     the exact ScenarioConfig used
  <scenario>/runs.csv        one row per seed (fields = RunMetrics.row())
  <scenario>/trace.jsonl     per-step trace for the first seed
  <scenario>/trace.svg       first-seed trace: lower bound at issue, n, state bands
  <scenario>/summary.json    aggregates over seeds
  summary.json               all scenario summaries
  figures/*.svg              cross-scenario bar charts
  manifest.json              see below
```

`manifest.json` fields: `canonical_hash` (SHA-256 over sorted `<path> <sha256>` lines of every `.csv/.json/.jsonl/.svg` under `results/` except the manifest), `canonical_hash_covers` (that sentence), `files` (the per-file hashes), `scenarios`, `seeds`, `horizon`, and `metadata_excluded_from_canonical_hash` (`code_sha`, `package_version`, `python`, `platform`, `generated_at`). Nothing in the canonical set contains a timestamp, a random id, or a platform string; decision and token ids are deliberately absent from the trace.

`runs.csv` columns are the fields of `RunMetrics` in `experiments/simulate.py` plus `unauthorized_side_effects`, `audit_overhead`, `censored_fraction`, `tta_censored`, and `denial_reasons` (`CODE=count;...`). `time_to_authority` and `intervention_delay` are empty when censored.

No simulation was discarded and no seed was changed to obtain these results. Two fixture mistakes during development (a token TTL shorter than the redemption lag, and a one-step lag that could never leave a token outstanding) were corrected in the harness before the committed run; both are described in the gate report.
