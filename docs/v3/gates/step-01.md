# Gate report — Step 01

**Status:** completed 2026-11-01. Criteria were pre-registered at Step 00 (letter-suffixed ones added from the PR #23 review, before any work) and are reported unchanged.

**Step objective:** v3 contract: spec, threat model, fixtures (serves all; see `docs/v3/plan.md` §4).

**Branch / SHA:** `docs/v3-step-01` from `mainline` @ `0c6e8ad`; Step 01 commit follows this report. Issue #24.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-01, macOS (Darwin 25.6.0), Python 3.14.8

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `spec.md` finalized: status line no longer "draft"; every MUST names the step that implements it | PASS | Status "Step 01 contract (final, 2026-11-01)"; `test_spec_is_final`, `test_every_normative_requirement_names_its_step` (every line with MUST carries a `(Step NN)` tag) |
| 2. Machine-readable fixture `docs/v3/revocation_fixture.json` for spec §5 (event, recorder, scope), checked by a new test that also asserts every row has a planned test name | PASS | 13 events RV-01…RV-13, 3 non-revoking cases, 1 non-event; `tests/test_v3_spec_fixture.py`: `test_fixture_matches_the_spec_table_row_for_row` (id, event, recorder against the spec table), `test_every_row_and_boundary_case_names_a_unique_planned_test`, and `test_planned_tests_exist_once_the_test_file_does` (skipped until Step 06 creates `tests/test_revocation.py`, binding after that) |
| 3. Owner has decided O-2, O-5, O-6, O-7; O-3, O-4, O-8 recorded as pending Step 09 | PASS | Spec §11: closed 2026-11-01 with the rejected alternatives named; O-3/O-4/O-8 "Open until Step 09"; `test_no_pending_rule_remains_for_a_decided_option` |
| 3b. The spec rules that depend on O-2, O-5, and O-6 (§6 rule 3, §7 rules 10–11) are rewritten as normative or removed per the decisions; no rule marked "pending" remains for a decided option | PASS | §6 rule 3 (O-5), §7 rules 10–12 (O-6, O-2), §2 rule 5 (O-7) normative with step tags; no "Pending O-2/5/6/7" anywhere in the spec (same test) |
| 4. Module names in `plan.md` §3 fixed; any rename recorded | PASS | "names fixed at Step 01 … no renames"; `revocation.py`'s role narrowed to the shared scoped revoke plus RV-07/RV-08; `test_module_names_are_fixed_in_the_plan` |
| 5. No `src/` change; global gate passes | PASS | Diff: `docs/v3/` and one new test file only |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 12 files; 225 passed, 1 skipped (above), 15 subtests; `example.py`, `denials.py`, `mcp_demo.py` exit 0 |
| Mutation check of the new test (not a pre-registered criterion; v3 plan §5 rule) | PASS | On a scratch copy, each defect made the suite fail: an untagged MUST, a reintroduced "Pending O-6", a dropped fixture row, `GATE_LOST` widened to all tokens, a duplicate planned-test name, a planned test missing once `tests/test_revocation.py` exists, and O-3 marked closed |

**Design and statistical assumptions:** One design addition, made without a separate owner decision and stated here for review:
- **New revoking event RV-04 `ORACLE_CHANGED`.** O-2 resets counts when the oracle changes, so an `AUTONOMOUS` partition falls below `n_min` and loses autonomy. Without an event, its tokens would survive until redemption, which breaks spec §5's purpose. Like `GATE_LOST`, it is scoped to evidence-based tokens; approval-based tokens are kept.
- `BUDGET_TIGHTENED` and `ENVELOPE_CHANGED`, one row at Step 00, are now RV-09 and RV-10, so each fixture row is one event.
- `PRINCIPAL_DEACTIVATED` records its cascade (grants, descendant grants, approvals) inside the one event rather than emitting `GRANT_REVOKED` per grant, consistent with the one-occurrence-one-event rule from the PR #23 review.
- §4 rules 1–2 and §3 rule 2, descriptive at Step 00, are now MUST so they carry step tags.

**Limitations / deviations:**
- `threat-model.md` is unchanged; criterion set did not require it, and the new RV-04 is covered by TM-7's "revoking events" wording.
- The fixture checks the spec table's id, event, and recorder columns, not the prose in the scope column; scope prose and fixture `scopes` descriptions are kept in agreement by hand.
- `mypy src` does not type-check `tests/`, as in CI.
- Python 3.10/3.13 not run locally; CI covers them.
- 5 files touched: `spec.md`, `plan.md`, `revocation_fixture.json`, `tests/test_v3_spec_fixture.py`, this report.

**Unexpected behavior / open risks:**
- Step 06 now has 13 revoking events plus 4 boundary cases to test, against 11 rows at Step 00; its size estimate (~350 LOC) is likely low.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 02, durable substrate, only on explicit request.
