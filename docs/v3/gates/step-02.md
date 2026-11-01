# Gate report — Step 02

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** Durable substrate (serves F1, F2, F3, F5; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `store.py` with WAL, `BEGIN IMMEDIATE` writes, per-thread connections; Python 3.10 compatible | NOT RUN | |
| 2. Append-only tables refuse UPDATE and DELETE at the database (test issues raw SQL and expects an error) | NOT RUN | |
| 3. `EvidenceStore`, `EventLedger`, lifecycle records, and risk charges on the store with unchanged semantics: every v0.3.1 test passes with construction changes only (diff of test assertions is empty) | NOT RUN | |
| 4. Restart test: suspension, lineage flag, budget charge, and pending trial survive closing and reopening the database (closes L4) | NOT RUN | |
| 5. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
