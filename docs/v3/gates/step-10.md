# Gate report — Step 10

**Status:** completed 2026-11-04. Criteria were pre-registered at Step 00 (2026-11-01) before any work and are reported unchanged.

**Step objective:** Reconciliation and release plan (serves all; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-10-reconciliation` from `mainline` @ `e644eae`; phase 1 `bab5607`, phase 2 `a82eeb8` (the code every `results-v3/` artifact was generated from in this step's three runs), phase 3 follows. Issue #46.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-04, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1, 8 cores

**Owner decision recorded (2026-11-04): keep both result sets.** `results/` stays the v0.3.0 artifacts the paper-v2 claims matrix cites, unchanged; that matrix is not edited, and a test pins its hash to `results/manifest.json`. `results-v3/` is the 0.4.0 artifact set of record, reproduced below. This replaces the plan's "Results regenerated" (§4, Step 10) and is a recorded deviation.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `docs/v3/claims-delta.md` marks every C and L row kept, changed, or closed with the step and test; new rows for F5 and Step 09 | PASS | All 46 rows: 24 changed, 17 closed, 5 kept, each with its 0.4.0 statement, where it changed, test(s), and data in `results-v3/`. New rows: N1 delegation (F5), N2 MCP 0.2, N3 the Step 09 families, N4 the agreement check, N5 the costs of the decided rules. `tests/test_claims_delta.py` checks one verdict per matrix row and that every cited test (95, including `::name` continuations) and path exists; a planted bad citation is caught |
| 2. README (including the test count), SECURITY.md, threat model, and CHANGELOG `[Unreleased] - proposed 0.4.0` agree with the code | PASS | README: 417 tests (the suite's count), `results/` and `results-v3/` roles, invariant 3 for `mainline`, a proposed 0.4.0 row. SECURITY.md: the #43 scope and its liveness limit. `docs/v2/threat-model.md` points to the v3 rows, and the v3 rows are now the operative ones. CHANGELOG: Breaking, Added, Changed, Results |
| 3. Breaking-change list in `plan.md` §6 verified against the final API | PASS | The signature of every public callable in `__all__` was compared between a `v0.3.1` worktree and `mainline`. All nine planned rows hold, with the verified 0.4.0 API recorded on each. Seven breaking changes the list missed were added (marked *added at Step 10*). The `Policy` row states the adopted fields and defaults. The upgrade note is corrected: 0.3.1 state was in memory, so nothing on disk migrates |
| 4. Release plan for 0.4.0 written; no tag, push, release, or DOI without separate authorization | PASS | `docs/v3/release-plan.md`: identifiers and their status, six gated steps, draft release notes, and a checklist with three items open for the owner. Nothing was tagged, released, or deposited; versions still read `0.3.1` |
| 5. Global gate and experiment reproduction pass | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 15 files; 417 passed, 1941 subtests; `example.py`, `denials.py`, `mcp_demo.py` exit 0 |
| Experiment reproduction | PASS | Three independent full runs from `a82eeb8`, in separate processes and output directories, are identical on all three outputs. **Scenarios** (shipped loop, default policy): canonical hash `622911c2045df4dff2717e76b3fa7c5fc89226d8ec233eb0bf22d540787cded2`. **Families**: `9fbef597ddfaa4134ff1a43aa06c6ad4854aecde7e50056db3e4dab422b67fb7`. **`agreement.json`** SHA-256: `9d1aaca9d6ffca81618599a5a171b544262cdfb07d6be0239e4720a6ca38967a`, with 250/250 agreeing across both pending rules. All three equal the values committed by #43, so this step changed no result; only the manifest's `code_sha` and `generated_at` metadata moved |

**Design and statistical assumptions:**
- **Verdict meanings.** "Kept" means the v0.3.0 statement still holds as written. "Changed" means it holds with different conditions, mechanism, or figures. "Closed" means the caveat or limitation no longer applies on `mainline`, and the 0.4.0 column states what remains.
- **Experimental rows.** The C24–C33 statements quote `results-v3/scenarios/` (the decided rules) and `results-v3/statistics/`. Step 09's run under the v0.3.1 rules is recorded in its own gate report.

**Limitations / deviations:**
- **`results/` is not regenerated** (owner decision, above). The paper-v2 matrix, manuscript, and their `results/` paths still describe v0.3.0, deliberately.
- **Release-time README pass.** The README still phrases 0.4.0 behaviour as "On `mainline` (unreleased)". Rewriting it to "Since 0.4.0" is in the release plan's release commit, because before a release it would misstate what is published.
- **Initial errors corrected before commit:**
  - The C11 row first said "all 32 cells whose assumptions hold are at or below 0.012". The data has 53 such cells, three above 0.012, which measure lifetime or family-wise rates. The row now says so.
  - The first plan §6 upgrade note said pre-0.4.0 database files fail `verify()`; only unreleased pre-Step-08 `mainline` files would.
  - The first bad-citation probe of the delta test planted nothing, because the token was written differently. It was redone and the planted citation is caught.
- **Files:** `docs/v3/claims-delta.md`, `docs/v3/release-plan.md`, `docs/v3/plan.md`, this report, README, SECURITY.md, CHANGELOG, `docs/v2/threat-model.md`, `docs/v3/threat-model.md`, one new test (`tests/test_claims_delta.py`), and the regenerated `results-v3/scenarios/manifest.json`. No source file changed.
- Python 3.10 and 3.13 were not run locally; CI covers them.

**Unexpected behavior / open risks:**
- **Interpreter reflection.** The execution boundary is closed except to interpreter reflection in the same process (C1). Only process separation closes that (O-1 chose same-process).
- **Undetected ledger rewrites.** Tail truncation and rewrites by a key holder remain undetected by `verify()` (C22, TM-19), and the derived tables are not chained.
- **Cost of the default rules.** They make cold start slower, make fast configuration churn qualify nothing, and refuse audits on marginal partitions (N5). These costs were accepted with O-3, O-4, and O-8.
- **Release-checklist item for the owner.** Confirm that nothing from `exe-auth-ctrl-loop-ip` is in the public repository.

**Decision: GO** (recommended; reviewer to confirm). This closes the v3 program; the 0.4.0 release follows `docs/v3/release-plan.md`, each step on separate authorization.

**Next permitted step (GO only):** the release commit of `docs/v3/release-plan.md` step 2, only on explicit authorization.
