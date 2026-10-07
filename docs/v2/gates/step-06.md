# Gate report — Step 06

**Branch / SHA:** `research/v2`, on top of `9eecc27` (Step 05); Step 06 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-07, macOS, Python 3.14.4

**Step objective:** Host-owned risk envelopes and sequence budgets, enforced at decision time and at redemption, separate from and interacting with the evidence policy.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Host derives risk class/envelope from registry/policy, not model labels or confidence | PASS | `RiskPolicy.classify` reads tool name and arguments only; `test_class_comes_from_arguments_not_labels_or_confidence` (confidence 0.999 on a $5000 refund labelled `low` is `DENY`); executor re-derives the class from the registry (`executor.py`, "risk class does not match registry") |
| 2. Same operation type with larger amount/recipient/effect is denied or needs separately qualified scope | PASS | `test_mislabeled_large_refund_cannot_borrow_low_risk_evidence` ($100 autonomous, $101 labelled `low` is `DENY`), `test_correctly_labeled_large_refund_needs_its_own_partition_evidence` (`high` starts at `NO_EXACT_EVIDENCE`, then fails its 0.99 threshold on 990/10), `test_altered_payee_is_denied`, `test_effect_outside_envelope_is_denied`, `test_boundary_amounts` ($100 ok, $100.01 and $-1 denied) |
| 3. Individually permissible operations cannot exceed a declared aggregate budget in-process | PASS | `test_individually_legal_operations_cannot_exceed_the_aggregate`, `test_budget_is_charged_at_redemption_not_decision` (three affordable decisions, two redeem, third refused under the lock), `test_per_payee_budget_is_scoped_by_argument`, `test_operation_count_limit`, `test_conflicting_action_within_partition`, `test_denied_redemption_charges_nothing` |
| 4. Risk policy changes propagate to the Step 05 revocation contract | PASS | `RiskPolicy` is a `RedemptionGuard`; `test_rollback_to_stricter_envelope_denies_outstanding_token`, `test_tightened_budget_denies_outstanding_token`, `test_charges_survive_a_policy_update` |
| 5. Tests offline; global quality gate passes | PASS | No network in any test; `ruff` clean, `mypy src` clean (12 files), 157 passed (137 + 20), three offline examples exit 0 |
| Required: `pytest -q tests/test_risk_envelopes.py tests/test_gateway_revocation.py` | PASS | 47 passed |
| Cross-partition leakage | PASS | `test_budgets_do_not_leak_across_partitions`, `test_low_risk_autonomy_does_not_extend_to_the_high_envelope` |
| Human override cannot widen scope | PASS | `test_human_approval_cannot_override_a_scope_denial` (route is `DENY`, `issue()` refuses) |
| Proposal digest, least privilege, denial routes preserved | PASS | No change to `Proposal`, digest, or routes; scope vetoes are added `DENY` reasons |

**Design and statistical assumptions:** None statistical. Design: scope is a host question answered before evidence and never overridable by a human; budgets charge at redemption under the gateway lock and do not reserve at decision time; per-`parameter:` budgets are global across partitions by design, partition budgets are not.

**Limitations / deviations:**
- The in-process guarantee is per `RiskPolicy` instance per process: committed charges never exceed a limit. No time windows, no persistence, no cross-process sharing; stated in `docs/v2/risk-envelopes.md`.
- Two classifiers exist: `ToolDefinition.risk_class` (registry, checked by the executor) and `RiskPolicy` (checked by controller and gateway). They are not forced to agree; the doc says a deployment should make the registry delegate to the policy. Unifying them touches `providers.py` and `tools.py` and was left out to keep the diff reviewable.
- `RedemptionGuard` grew a `commit()` method and `redemption_blockers()` now takes the proposal. `LifecycleManager` was updated; any external guard must be too.
- 9 files touched (new: `risk.py`, `test_risk_envelopes.py`, `risk-envelopes.md`, this report; modified: `authority.py`, `lifecycle.py`, `executor.py`, `__init__.py`). Over the 5-file guideline by the required doc and report.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- Four of the first-draft tests failed on my own fixture arithmetic (ops that broke the per-payee budget or the $100 envelope while meant to test something else) and one seeded a partition twice. All were test bugs; no production change resulted. Noted because a reviewer should not read the final green as "first try".
- `conflicts` are checked at decision and redemption but `commit()` records history only on successful redemption, so a conflicting op denied at decision time leaves no history entry. Intended.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 07 — `steps/07_audit.md`, only on explicit request.
