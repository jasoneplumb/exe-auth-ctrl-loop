# Gate report — Step 05

**Branch / SHA:** `research/v2`, on top of `a476011` (Step 04); Step 05 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-06, macOS, Python 3.14.4

**Step objective:** Tokens fail at redemption when their justification has been withdrawn; check-and-consume is serialized in-process; human approval cannot bypass prohibitions.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Token issued before suspension, policy change, or declared evidence invalidation cannot execute afterwards, within TTL | PASS | `test_issue_then_suspend_denies_within_ttl` (1 s into a 2-min TTL), `test_issue_then_policy_change_denies`, `test_issue_then_declared_invalidation_denies`, `test_issue_then_evidence_record_replaced_denies`, `test_lifecycle_suspension_denies_outstanding_token`, `test_config_churn_denies_old_partition_token` |
| 2. Simultaneous redemptions yield at most one handler invocation, with deterministic synchronization | PASS | `test_simultaneous_redemptions_invoke_handler_once` (8 threads on a barrier; 1 ok, 7 denied), `test_token_is_consumed_before_the_handler_runs` (event-gated handler), `test_revoke_and_redeem_cannot_both_win` |
| 3. Digest, tool, effect set, or scope changes fail closed; human override does not bypass prohibited effects | PASS | `test_digest_tool_and_effect_changes_fail_closed`, `test_effect_check_is_independent_of_digest`, `test_human_override_cannot_issue_for_prohibited_effect` (incl. a forged non-DENY route), `test_newly_prohibited_effect_denies_even_with_same_policy_version` |
| 4. Spec states whether ordinary evidence increments invalidate tokens; the boundary is tested | PASS | `docs/v2/gateway-redemption.md` "What does and does not invalidate"; `test_ordinary_evidence_increment_does_not_invalidate`, `test_ordinary_increment_without_reevaluation_keeps_token_good`, `test_gate_lost_before_redemption_denies` |
| 5. In-process vs durable atomicity documented accurately; global quality gate passes | PASS | SECURITY.md "Known limitations of the prototype gateway"; README limitations row; `ruff` clean, `mypy src` clean, 137 passed (110 + 27), three offline examples exit 0 |
| Required: `pytest -q tests/test_gateway_revocation.py tests/test_authority.py` | PASS | 36 passed |
| Linearization point specified | PASS | `gateway-redemption.md` "Linearization point"; `execute()` docstring |
| Expiry boundary | PASS | `test_expiry_boundary` (good at `expires_at - 1 µs`, denied at `expires_at`) |
| Revoked human-approved grant refused | PASS | `test_revoked_human_grant_is_refused` (reason recorded on the token) |

**Design and statistical assumptions:** None statistical. Design: a human approval is informed consent to the state the decision saw, so a human-approved token survives a suspension only if the approving decision already saw that suspended snapshot version (`test_human_approval_given_after_suspension_is_honored` vs `test_human_approval_before_suspension_is_refused`). Ordinary increments do not invalidate; suspension, invalidation, evidence replacement, policy change, newly prohibited effect, lifecycle leaving `AUTONOMOUS` (evidence-based tokens), retirement, lineage suspension, and explicit revocation do.

**Limitations / deviations:**
- Thread lock only. No cross-process, cross-host, or crash atomicity is claimed; stated in SECURITY.md and the contract doc.
- The live checks apply only when the gateway is constructed with `evidence`, `current_policy`, and/or `guards`. `examples/*.py` and `CrossModelAuthorityLoop` still construct a bare gateway, so they keep v0.2.0 semantics; wiring the pipeline is left for Step 07/08 where the ledger contract is revisited.
- `AuthorizationToken` gained a required `partition` field. Only `issue()` constructs tokens, so nothing else broke, but any external code constructing tokens directly must add it.
- The gateway raises denials; it does not ledger them. The pipeline records what the gateway raises (unchanged).
- 7 files touched (`authority.py`, `lifecycle.py`, `__init__.py`, `SECURITY.md`, `README.md`, new test, new doc, this report = 8 with the report). Over the 5-file guideline by the two documentation files the step requires.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- `test_revoke_and_redeem_cannot_both_win` accepts either ordering (handler once, or denied) because the lock orders them but does not choose; the assertion is that both cannot happen. Stated in the test.
- Guards run inside the consume lock. A slow or re-entrant guard would stall or deadlock the gateway; `LifecycleManager.redemption_blockers` is read-only and does not refresh, and the `RedemptionGuard` docstring says so.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 06 — `steps/06_risk.md`, only on explicit request.
