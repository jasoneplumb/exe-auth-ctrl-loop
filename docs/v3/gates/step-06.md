# Gate report — Step 06

**Status:** completed 2026-11-02. Criteria were pre-registered at Step 00 (letter-suffixed ones added from the PR #23 review, before any work) and are reported unchanged.

**Step objective:** Synchronous invalidation (serves F4, closes L12, changes C13/C14; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-06-revocation` from `mainline` @ `1cf34e4`; Step 06 `6edb9ce`, PR #35 review fixes follow. Issue #34.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-02, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1

**Owner decision taken at the start of this step (2026-11-02):** synchronous revocation may change simulation behaviour. `results/` stays as the v0.3.x artifacts and is regenerated once at Step 09; this report records the new canonical hash and what moved. Rejected: regenerating now; a switch that turns the mechanism off for the harness.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. For every row of `revocation_fixture.json`: issue a token, record the event, then redeem with no intervening `evaluate()`; redemption denied with the event as reason | PASS | `tests/test_revocation.py::RevokingEventTests`, one test per row under its `planned_test` name (RV-01…RV-13). Each asserts the denial reads `revoked by <EVENT>`, the token is `revoked` with that reason, and an `authority.revoked` ledger record names it. The Step 01 forward check `test_planned_tests_exist_once_the_test_file_does` is now active (no longer skipped) and passes for all 13 rows and 4 boundary cases |
| 2. Revocation and the event commit in one transaction (fault-injection test: a failure between them leaves neither) | PASS | `TransactionTests`: `EventLedger.append` patched to fail on the `authority.revoked` record during `invalidate()`; afterwards the evidence is still valid at its old version, the token is still `issued`, and it redeems |
| 3. An admitted label that drops $L_t$ below $r$ revokes outstanding evidence-based tokens in the same transaction; one that keeps $L_t \ge r$ revokes nothing | PASS | `test_gate_lost_revokes_evidence_based_tokens_only` (negative labels until the partition leaves AUTONOMOUS; the evidence-based token is revoked by `GATE_LOST`); `test_label_that_keeps_the_gate_revokes_nothing` |
| 4. `redemption_blockers` recomputes the gate from current counts | PASS | `RedemptionRecomputeTests`: with the label listener detached, negative labels leave the record AUTONOMOUS and the token issued, and redemption is still refused with `BOUND_BELOW_POLICY`; `test_stale_evidence_is_refused_at_redemption` (`EVIDENCE_STALE`, token left `issued`: staleness is a time condition, not an event) |
| 5. Policy-version successors inherit lineage suspension (closes L12) | PASS | `LineageTests::test_policy_version_successor_inherits_suspension` (suspended partition, policy change, successor key is `lineage_suspended` and its decisions carry `LINEAGE_SUSPENDED`); `test_an_unsuspended_predecessor_passes_nothing_on` |
| 6. Grant revocation and principal deactivation cascade to descendant tokens | PASS | `test_grant_revocation_cascades_to_descendant_tokens` (revoking the middle of a three-grant chain revokes its and its child's tokens and keeps the root's); `test_principal_deactivation_cascades_as_one_event` (tokens under a grant the principal holds, and tokens on approvals it gave, revoked; one record listing the grants) |
| 6b. A revoking event whose scope contains a token already in `handler_started` leaves it un-revoked and un-re-run, lists it in the event's ledger record as effect unknown, and `effect_unknown()` returns it | PASS | `EffectUnknownTests::test_a_started_token_is_listed_not_revoked` |
| 6c. A policy-version change emits one `POLICY_CHANGED` event (no per-partition `PARTITION_RETIRED`), retires every partition on the old version, and records lineage edges for their successors | PASS | `test_policy_change_is_one_event_retiring_every_old_partition` (two partitions; exactly one `POLICY_CHANGED` record and no `PARTITION_RETIRED` record; both retired; both edges point at the `pol2` key) |
| 6d. `GATE_LOST` revokes evidence-based tokens and leaves approval-based tokens of the same partition redeemable | PASS | `test_gate_lost_revokes_evidence_based_tokens_only` (the approval-based token stays `issued` and redeems); `test_oracle_change_opens_epoch_and_revokes_evidence_based_tokens` likewise for RV-04 |
| 7. Global gate passes | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 15 files; 337 passed, 0 skipped, 85 subtests (after the PR #35 review fixes); three offline examples exit 0 and `denials.py` still matches the README |
| Mutation check (v3 plan §5 rule) | PASS | Each fails `test_revocation.py`: `revoke()` changes no state (13 failures); gate loss does not revoke (1); `evidence_only` ignored (2); no gate recompute at redemption (2); no lineage inheritance (1); a deferred cross-partition callback dropped instead (1, `test_censoring_another_partitions_trials_revokes_its_tokens_at_once`) |

**Experiment reproduction (owner decision above):** canonical hash `4cc7a0e38ed7d60dd064fbf8b62fa6f3b04864c9b8c6aaa1fd131d6ea6785ea6` at `6edb9ce` and again after the PR #35 review fixes, against `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a` in `results/manifest.json`.

| What changed | Detail |
|---|---|
| Files | `risk_envelopes/trace.jsonl` and its `trace.svg` only. Every `summary.json`, every `runs.csv`, every other trace, and the top-level summary are byte-identical |
| In the trace | 36 of 512 lines (seed 1): the `n` field, the published evidence count of the low-risk partition, is 1–2 higher. Every route, decision, denial reason, execution, and outcome is the same |
| Why | The lifecycle now listens to label admission and republishes its snapshot in the label's transaction (RV-03 plumbing). `risk_envelopes` alternates low- and high-risk proposals, so the low-risk snapshot used to lag until that partition's next evaluation; the trace reads the published snapshot. Other scenarios evaluate their partition every step, so their published counts were already current |
| Why no metric moved | Revocations now happen at the label rather than at the next evaluation, but the harness redeems due tokens after each step's evaluation, so a token the old code refused at redemption is now refused because it was revoked, in the same step and counted the same way (`stale_token_denials`) |

So, measured: synchronous invalidation changes *when* authority is withdrawn, not any outcome the fifteen scenarios measure. Scenarios that would separate the two (a token redeemed between a label and the next evaluation) are Step 09's to design.

**Design and statistical assumptions:**
- **One record type for every revocation.** Each revoking event appends one `authority.revoked` record (event, revoked tokens, effect-unknown tokens, details) in the event's transaction. For RV-11…RV-13 this is in addition to the permission event, so `test_principals.py`'s "exactly one event of its type" now counts permission events only (its criterion's wording).
- **RV-03 plumbing.** The shadow log calls listeners inside the label (or censor) transaction; `LifecycleManager` registers itself and re-walks the partition, and the `GATE_LOST_OUTCOME` transition revokes. A re-walk can censor other partitions' overdue trials; their callbacks are deferred and walked before the listener returns, in the same transaction (found in the PR #35 review: they were being dropped, leaving that partition's revocation to its next refresh, with redemption still refusing). The fixture names `shadow.py` as the recorder: the revocation is triggered there and committed in its transaction.
- **RV-04 API.** `LifecycleManager.change_oracle(key, oracle_id, oracle_version, by=)` opens a new epoch (counts reset), adds the `ORACLE_CHANGED` transition (AUTONOMOUS → QUALIFYING; other states unchanged), and revokes evidence-based tokens. Binding labels to the epoch's oracle is Step 08.
- **RV-07/RV-08 API.** `revocation.PolicyAuthority` is the host's one holder of the current policy (`current_policy=authority.current`). A policy object swapped by assignment is still caught lazily at refresh and at redemption, as before.
- **RV-09/RV-10.** Tokens now store their proposal (schema v5), so `RiskPolicy.update()` can ask the new rules about each outstanding token of the tools it governs. One update is one event: `ENVELOPE_CHANGED` if the envelopes changed, else `BUDGET_TIGHTENED`.
- **Retirement by key churn** revokes as `PARTITION_RETIRED`; retirement by a policy change does not, because `POLICY_CHANGED` revokes every old-version token at once. Invalidation inside a retirement passes `revoke_tokens=False` for the same reason (one occurrence, one event).
- **Lineage.** A new partition record inherits suspension from any recorded predecessor, by key churn or by policy change. `on_churn` flags only a successor that already existed.

**Limitations / deviations:**
- **Changed assertions in earlier tests** (the tokens are now revoked at the event, so denials name the event):
  - `test_gateway_revocation`: `test_lifecycle_suspension_denies_outstanding_token` expects `revoked by SEVERE_FAILURE` (was `PARTITION_SUSPENDED`, `LIFECYCLE_SUSPENDED`); `test_gate_lost_before_redemption_denies` expects `revoked by GATE_LOST` (was `LIFECYCLE_QUALIFYING`).
  - `test_lifecycle::test_retired_partition_token_is_refused_without_the_lifecycle_guard` expects `revoked by` (was `EVIDENCE_INVALID`).
  - `test_risk_envelopes`: `revoked by ENVELOPE_CHANGED` (was `RISK_CLASS_MISMATCH`), `revoked by BUDGET_TIGHTENED` (was `BUDGET_EXCEEDED:daily-refunds`).
  - `test_principals::test_a_revoked_ancestor_refuses_redemption` expects the token `revoked` (was `issued`).
  - `test_principals::test_each_change_writes_exactly_one_event_of_its_type` counts permission events only.
- **Kept, not changed:** `test_denial_names_every_reason` and `test_suspended_and_invalid_names_both_reasons` exist to prove the redemption-time second line names every reason; they now make their change with `invalidate(..., revoke_tokens=False)` (a change that bypassed the revoking path) and keep their assertions.
- **Transition-table test:** compares v2 transitions with the v2 fixture (which stays the v2 record) and pins the new `ORACLE_CHANGED` transitions separately.
- **README and SECURITY.md:** the C13/C14 limitation text, the reproduction claim (now marked as the v0.3.x artifacts), and two SECURITY bullets closed on `mainline` by Steps 04 and 06 are updated; 0.3.1 behaviour stays stated.
- Files touched: 8 source files (new `revocation.py`), 7 test files (new `test_revocation.py`), README, SECURITY.md, this report.
- Python 3.10/3.13 not run locally; CI covers them.

**Unexpected behavior / open risks:**
- The reproduction moved far less than expected (above): the fifteen scenarios cannot observe the timing this step changes. Step 09 should include a scenario that redeems between a label and the next evaluation.
- Every label now re-walks its partition's lifecycle in the label's transaction. Suite time is unchanged within noise (≈ 49 s); the full experiment run takes ≈ 3 minutes as before.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 07, MCP server-side redemption, only on explicit request.
