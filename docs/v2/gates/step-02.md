# Gate report — Step 02

**Branch / SHA:** `research/v2`, on top of `4935031` (Step 01); Step 02 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-06, macOS, Python 3.14.4

**Step objective:** Frozen shadow trials, oracle labels, provenance-separated counts, offline.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `n=0` partition accumulates admissible shadow evidence, successes and failures | PASS | `test_accumulates_from_zero_with_successes_and_failures`, `test_empty_store_qualifies_from_shadow_evidence_alone` (40 shadow positives take an empty store from `HUMAN_APPROVAL` to `AUTONOMOUS`) |
| 2. Human approval/edits cannot create, mutate, discard, or relabel a frozen trial; inflation and cherry-picking denied | PASS | `test_human_rewrite_cannot_inflate_a_label`, `test_human_rejection_cannot_discard_a_negative_trial`, `test_review_never_creates_or_counts_evidence`, `test_human_approved_provenance_is_recorded_but_never_counted` |
| 3. Duplicate IDs, wrong keys, late/replayed labels rejected with reason codes | PASS | `test_duplicate_trial_id_is_quarantined`, `test_wrong_partition_label_is_rejected`, `test_replayed_label_is_rejected`, `test_late_label_is_quarantined_and_censors_the_trial`, `test_label_before_freeze_is_rejected` |
| 4. Inconclusive never a success; missing-label counts and coverage observable | PASS | `test_inconclusive_is_never_a_success_and_is_observable`, `test_publish_expires_overdue_trials_so_withholding_does_not_help` |
| 5. `tests/test_shadow_evidence.py` offline; global gate green | PASS | 19 passed; `ruff`, `mypy src`, full `pytest`, three offline examples: see handoff output |
| Pre-review commitment ordering; trace replay | PASS | `test_pre_review_commitment_precedes_review_and_label`, `test_trace_replay_reproduces_counts_and_survives_quarantine_noise`, `test_replay_rejects_tampered_ledger` |

**Design and statistical assumptions:** Spec A1-A5. Pending trials are excluded from `n` until labeled or expired; `publish()` expires overdue ones first. Inconclusive and expired trials count as failures in the snapshot.

**Limitations / deviations:**
- The plan's G02 evidence names a "recorded trace fixture". Replay is tested against a ledger built in-test rather than a checked-in file; a stored fixture can be added in Step 08.
- The bound is still fixed-`z` Wilson (Step 04). `n=0` bootstrap demonstrates reachability, not a valid guarantee.
- No oracle implementation; labels are caller-supplied. State machine, suspension, and epochs are Step 03.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:** Nothing yet prevents a caller from publishing a shadow snapshot over a seeded or v1-adjudicated snapshot (documented, not enforced).

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 03 — `steps/03_lifecycle.md`, only on explicit request.
