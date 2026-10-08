# Worked example, from an empty evidence store

Replaces the v1 manuscript's example, which opened with an unexplained record of 198 successes and 2 failures. Every number below is read from `results/stable/trace.jsonl`, `results/suspension_cycle/trace.jsonl`, and `results/turnover_slow/trace.jsonl` (seed 1, the first seed of each scenario; the three share one random stream until the scenarios diverge). Reproduce with `python -m experiments.run --all --output results`.

Setting: one tool (`create_refund`), one partition (`low` risk class: refunds up to $100 to an allow-listed payee), policy `n_min = 30`, required bound 0.90, α = 0.05, audit rate 0.05, token TTL 5 steps, tokens redeemed 2 steps after issue. The true acceptability rate is 0.97 and 10 % of unacceptable outcomes are severe. The oracle labels a frozen proposal with its true acceptability two steps after the freeze.

## 1. Admission (steps 0–53)

- **t = 0.** The first proposal arrives. The store has no snapshot for this partition. The controller returns `HUMAN_APPROVAL` with reason `NO_EXACT_EVIDENCE`; the lifecycle creates the partition in `UNESTABLISHED` (transition T-01). Before the reviewer sees the proposal it is frozen into the shadow log as a `SHADOW` trial (ledger event `shadow.frozen`, with the proposal digest). The reviewer approves; the refund runs as a human-approved execution. **It counts for nothing.**
- **t = 2.** The oracle's label for the t = 0 trial lands on the frozen digest: `POSITIVE`. The partition moves to `QUALIFYING` (T-02) with `n = 1`.
- **t = 2 … 53.** Every proposal is reviewed by a human and, independently, shadow-labelled. 54 proposals are reviewed; none is rejected (none was unacceptable in this stretch). The shadow count climbs to 53 positives, 0 negatives.

## 2. Qualification (step 54)

At t = 54 the decision reads the published snapshot: `n = 53`, 53 positives. The Beta-mixture lower bound is 0.9005, just above the required 0.90 (`L(53, 0) = 0.9005`; one step earlier, `L(52, 0)` was below it). `n ≥ n_min`, the evidence is fresh, the policy version matches, the partition is not retired or lineage-suspended. The lifecycle moves `QUALIFYING → AUTONOMOUS` (T-07), records the evidence id and version it relied on, and the controller routes `AUTONOMOUS` with reason `AUTHORITY_SUFFICIENT`. The decision is committed to the ledger, a token is issued, and at t = 56 it is redeemed: the handler runs with no human involved. The outcome is acceptable, is frozen as an `AUTONOMOUS` trial, and is labelled two steps later.

For comparison, the fixed-sample Wilson bound would have cleared 0.90 at 40/40; the sequential bound needs 53/53 because it must remain valid when read after every one of those steps (`docs/v2/statistical-method.md` §9).

## 3. Operation under authority (steps 54–86)

29 autonomous and 3 audited executions. The audited ones differ in exactly one way: the pre-outcome draw fell below 0.05, so the proposal was frozen as an `AUDIT` trial before its token existed and the oracle labelled it afterwards. One autonomous outcome in this stretch is unacceptable but not severe; the bound after it is still above 0.90 and autonomy continues.

## 4. Invalidation (step 87)

At t = 87 a redeemed autonomous operation produces a **severe** outcome. `record_severe` moves the partition `AUTONOMOUS → SUSPENDED` (T-15c) before anything is counted; the snapshot is republished with `suspended = True`. The token issued at t = 86 was still outstanding; at t = 88 the gateway refuses it: `authorization withdrawn: PARTITION_SUSPENDED, LIFECYCLE_SUSPENDED` (one `stale_token_denials` in `runs.csv`). From here every proposal routes to a human again, and the shadow log keeps freezing and labelling them, but nothing counts: `SUSPENDED` admits no evidence (T-16).

In the `stable` scenario nobody releases the suspension, so this run ends at t = 400 still `SUSPENDED`, with 83 trials on record and a final bound of 0.93 that no longer matters. That is the policy as written: autonomy is earned over many outcomes and withdrawn by one, and restoring it is a human act.

## 5. Requalification (`suspension_cycle`, steps 87–201)

The `suspension_cycle` scenario is the same stream with an operator who releases a suspension 30 steps after it begins.

- **t = 117.** `release(key, "operator", "simulated post-incident review")`. The partition moves `SUSPENDED → QUALIFYING` (T-18), never directly to `AUTONOMOUS`. The epoch increments and the shadow log opens a new epoch: the 30 trials frozen during the suspension, and everything before, no longer count. The snapshot is republished at `n = 0`.
- **t = 117 … 200.** Human review and shadow labelling again, from zero.
- **t = 201.** 53 fresh positives; `QUALIFYING → AUTONOMOUS` a second time (`requalifications = 1` after the next cycle completes). The run goes on to suspend at 216, release at 246, requalify at 300, and suspend again at 331, ending `QUALIFYING` with 37 trials in its fourth epoch. Three suspensions, three releases, two requalifications, three stale-token denials, four unacceptable autonomous outcomes in 400 steps.

## 6. Configuration churn and lineage (`turnover_slow`, step 200)

Same stream, no operator, and the proposer's prompt version changes at t = 200 while the partition is still suspended from t = 87.

- **t = 200.** `on_churn(old, new)`. The old partition is retired (T-19); the successor is created `UNESTABLISHED` with no counts carried over — **and `lineage_suspended = True`** (T-20), because its predecessor was suspended.
- **t = 202 … 400.** The successor accrues 198 shadow trials with a bound of 0.917, above the threshold, and stays `QUALIFYING`. Its decisions carry `LINEAGE_SUSPENDED`. Editing a version string does not clear a suspension; only a release covering the lineage does.

## What the example shows that v1's did not

- Authority is reachable from nothing, through evidence a reviewer could not shape.
- The first autonomous operation is preceded by 54 human reviews, and the paper should say so.
- Withdrawal happens on one severe outcome, invalidates an outstanding token, and requires a human to undo.
- Requalification restarts from zero evidence.
- A configuration change neither carries evidence forward nor launders a suspension.

## What it does not show

The oracle here knows the truth. The reviewer catches 90 % of bad proposals by coin flip. Severity is observed the instant the outcome occurs. See `experiments/README.md` for why each of these flatters the system.
