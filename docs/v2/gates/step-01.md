# Gate report — Step 01

**Branch / SHA:** `research/v2`, on top of `0b479e1` (Step 00); Step 01 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-06, macOS, Python 3.14.4

**Step objective:** Lifecycle, evidence-admission, and threat-model contract plus a machine-readable fixture. No behavior change.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Spec names every state, entry/exit guard, provenance field, failure response, transition; no silent path to autonomy | PASS | `docs/v2/spec.md` §2, §3, §9, §10; `test_only_qualifying_gate_met_enters_autonomous`, `test_empty_store_cannot_reach_autonomy_directly` |
| 2. At least 12 transitions/blocked transitions enumerated, incl. `n=0`, severe, policy change, inconclusive, churn | PASS | `docs/v2/lifecycle_fixture.json`: 23 rows (T-01 to T-21 with T-15a/b/c); `test_enough_transitions_and_unique_ids` |
| 3. Threat model separates trusted host, adversarial models, fallible adjudication, audit RNG/ledger trust, unsupported adversaries | PASS | `docs/v2/threat-model.md`; `test_threat_model_covers_required_classes` |
| 4. Provenance SHADOW / AUTONOMOUS / HUMAN_APPROVED / AUDIT each with a rule, incl. inconclusive | PASS | `spec.md` §3 table and rules 1-6; `evidence_rules` in fixture |
| 5. Reviewer can state the exact event the statistical gate bounds | PASS | `spec.md` §6: $\Pr(\exists t: L_t \ge r \wedge p < r) \le \alpha$ under A1-A5, over oracle-positive rate |
| `test -s` spec and threat model | PASS | both non-empty |
| `ruff check .`, `mypy src`, `pytest -q` | PASS | clean; clean; 54 passed (41 baseline + 13 new) |

**Design and statistical assumptions:** A1-A5 in `spec.md` §6. Inconclusive labels are treated as failures for the bound (conservative). The estimand is oracle-adjudicated acceptability, not real-world execution success.

**Limitations / deviations:**
- The fixture is validated for internal consistency against the spec, not against code; no code implements the lifecycle yet (Step 03).
- `AUDIT` evidence is defined on the frozen proposal, which differs from v1's pre-execution approval flow (open item O-2).
- The specific time-uniform mechanism is not chosen (O-1, Step 04); the claim in §6 is the contract it must meet.
- T-19 is shown for `AUTONOMOUS` only; churn applies to any state per spec §9.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:** The v1 Wilson bound at fixed `z` does not satisfy the §6 claim; that is the reason for Step 04.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 02 — `steps/02_shadow.md`, only on explicit request.
