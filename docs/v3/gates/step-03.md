# Gate report — Step 03

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision. Criteria with a letter suffix were added 2026-11-01 from the PR #23 review, still before any work.

**Step objective:** Transactional capability consumption (serves F1; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Redemption is one transaction ending in a conditional update with rowcount 1; the thread lock is removed | NOT RUN | |
| 2. Two OS processes redeeming one token through separate connections: exactly one handler call, over at least 200 trials | NOT RUN | |
| 3. Mutation check: with the `state='issued'` condition removed, the cross-process test FAILS (recorded output in this report). Closes C15 | NOT RUN | |
| 4. Restart: consumed stays consumed; an issued unexpired token redeems exactly once after reopening; a `handler_started` token is never re-run | NOT RUN | |
| 4b. `effect_unknown()` lists every token left in `handler_started` after a restart | NOT RUN | |
| 5. One decision issues at most one token | NOT RUN | |
| 5b. A second `issue()` for a `decision_id` is refused whatever the first token's state (issued, revoked, consumed), at the API and by the database unique constraint | NOT RUN | |
| 6. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
