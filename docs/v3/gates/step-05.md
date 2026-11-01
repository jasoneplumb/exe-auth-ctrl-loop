# Gate report — Step 05

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** Principals, grants, lineage (serves F5; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Ledger refuses an unregistered `actor` | NOT RUN | |
| 2. `Approval` replaces `human_approved`; a forged or wrong-principal MAC is refused at `issue()`; `release()` requires a verified `HUMAN` principal (closes L9) | NOT RUN | |
| 3. A grant broader than its parent, or outliving it, is refused at creation | NOT RUN | |
| 4. `lineage(token_id)` returns the chain to the root grant for every token | NOT RUN | |
| 5. Every permission change emits exactly one of the six event types; a test enumerates the public API and asserts no mutating path skips the ledger | NOT RUN | |
| 6. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
