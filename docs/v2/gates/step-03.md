# Gate report — Step 03

**Branch / SHA:** `research/v2`, on top of `205fc45` (Step 02); Step 03 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-06, macOS, Python 3.14.4

**Step objective:** Explicit lifecycle state machine over admissible shadow evidence, with recorded transitions.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Deterministic offline `UNESTABLISHED -> QUALIFYING -> AUTONOMOUS` from zero, no seeding | PASS | `test_empty_store_to_autonomy_from_shadow_evidence_only` (store asserted empty first; ledger shows FIRST_PROPOSAL, TRIAL_ADMITTED, GATE_MET; token then redeemed) |
| 2. 100% human-approved history, zero shadow data, never earns autonomy | PASS | `test_all_human_approved_history_never_earns_autonomy` |
| 3. Severe failure, stale/invalid evidence, config/policy mismatch remove autonomy with reason codes | PASS | `test_severe_failure_suspends_immediately_and_persists`, `test_stale_evidence_withdraws_autonomy`, `test_failures_withdraw_autonomy_with_reason`, `test_policy_change_retires_matching_authority`, `test_config_churn_does_not_pool_evidence` |
| 4. No path from `SUSPENDED` to `AUTONOMOUS` bypasses restoration and evidence gates | PASS | `test_suspended_cannot_reach_autonomous_without_release`, `test_full_suspension_to_requalification_cycle`, `test_churn_cannot_launder_a_suspension`, `TransitionTableTests`, `test_table_agrees_with_the_step_01_fixture` |
| 5. Existing immutability and single-use tests green; lifecycle tests and global gate pass | PASS | `tests/test_authority.py` unchanged and passing; 92 passed total; `ruff`, `mypy src` clean; examples exit 0 (see handoff) |
| Hand-seeded snapshot cannot bypass lifecycle via the manager | PASS | `test_hand_seeded_snapshot_cannot_bypass_the_lifecycle` |

**Design and statistical assumptions:** Spec A1-A5 unchanged. Release opens a new evidence epoch; trials frozen earlier, including those accrued during suspension, never count.

**Limitations / deviations:**
- Issued tokens still outlive suspension and retirement (Step 05).
- Statistical gate is still fixed-`z` Wilson (Step 04).
- This phase touched 6 files (new: `lifecycle.py`, `test_lifecycle.py`, `docs/v2/lifecycle.md`, this report; modified: `shadow.py`, `__init__.py`), one over the 5-file phase guideline. The extra file is the focused doc the step requires.
- Severity input and churn reports are caller-supplied; no detection.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- Found and fixed during the step: a retired `AUTONOMOUS` partition kept its state and `evaluate()` still returned `AUTONOMOUS`. Now covered by `test_config_churn_does_not_pool_evidence`.
- Found and fixed: Step 02's `collected_until` was the publish time, so re-publishing refreshed stale evidence. It is now the last label or censor time.
- The Step 02 gate report stands; neither fix changed any Step 02 test result.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 04 — `steps/04_statistics.md`, only on explicit request.
