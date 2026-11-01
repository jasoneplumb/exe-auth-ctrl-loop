# Gate report — Step 01

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision. Criteria with a letter suffix were added 2026-11-01 from the PR #23 review, still before any work.

**Step objective:** v3 contract: spec, threat model, fixtures (serves all; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `spec.md` finalized: status line no longer "draft"; every MUST names the step that implements it | NOT RUN | |
| 2. Machine-readable fixture `docs/v3/revocation_fixture.json` for spec §5 (event, recorder, scope), checked by a new test that also asserts every row has a planned test name | NOT RUN | |
| 3. Owner has decided O-2, O-5, O-6, O-7; O-3, O-4, O-8 recorded as pending Step 09 | NOT RUN | |
| 3b. The spec rules that depend on O-2, O-5, and O-6 (§6 rule 3, §7 rules 10–11) are rewritten as normative or removed per the decisions; no rule marked "pending" remains for a decided option | NOT RUN | |
| 4. Module names in `plan.md` §3 fixed; any rename recorded | NOT RUN | |
| 5. No `src/` change; global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
