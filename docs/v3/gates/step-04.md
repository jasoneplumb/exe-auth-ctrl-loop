# Gate report — Step 04

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** Execution boundary (serves F1; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `ToolCatalog` / `HandlerTable` split; agent receives the catalog only | NOT RUN | |
| 2. `ExecutionGateway.execute(token_id, proposal)` has no adapter parameter; handler chosen by `token.allowed_tool` | NOT RUN | |
| 3. Reachability walk from agent, proposer, and pipeline finds no handler callable and no `HandlerTable`; the test fails if `executor.py` is given the table | NOT RUN | |
| 4. `issue()` refuses a decision absent from `decisions` or differing in digest or route (closes L7 first half, enforces C20) | NOT RUN | |
| 5. Same-process reflection residual stated in README and SECURITY.md | NOT RUN | |
| 6. Global gate passes; offline examples updated and exit 0 | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
