# Gate report — Step 02

**Status:** completed 2026-11-01. Criteria were pre-registered at Step 00 and are reported unchanged.

**Step objective:** Durable substrate (serves F1, F2, F3, F5; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-02-store` from `mainline` @ `2034673`; phase 1 `efc342a`, phase 2 commit follows this report. Issue #26.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-01, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `store.py` with WAL, `BEGIN IMMEDIATE` writes, per-thread connections; Python 3.10 compatible | PASS (3.10 by CI only) | `Store`: WAL, `synchronous=FULL`, `isolation_level=None` with explicit `BEGIN IMMEDIATE`, re-entrant per thread, rollback on any failure including a failed `COMMIT`. `tests/test_store.py` (19), including two OS processes doing 200 read-modify-writes each and ending at exactly 400. No SQL feature newer than 3.24 (`ON CONFLICT ... DO UPDATE`); no `RETURNING`, no `STRICT` |
| 2. Append-only tables refuse UPDATE and DELETE at the database (test issues raw SQL and expects an error) | PASS | `test_every_history_table_refuses_update_and_delete_at_the_database` over all 9 tables in `APPEND_ONLY_TABLES`, plus a check that the seed covers every one |
| 3. `EvidenceStore`, `EventLedger`, lifecycle records, and risk charges on the store with unchanged semantics: every v0.3.1 test passes with construction changes only (diff of test assertions is empty) | PASS, with one stated deviation | `git diff` of the ten v0.3.1 test files shows no added or removed line containing an assertion. Changes are construction (a scratch `Store` per test) plus the deviation below. The shadow log moved too, since its trials and labels are the pending-trial state of criterion 4. **Stronger check:** the full experiment run (`python -m experiments.run --all`) reproduces canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a`, identical to the committed `results/manifest.json`: all 15 scenarios × 10 seeds produce byte-identical artifacts on the durable store. `examples/denials.py` output is identical before and after |
| 4. Restart test: suspension, lineage flag, budget charge, and pending trial survive closing and reopening the database (closes L4) | PASS | `tests/test_durability.py::RestartTests`: new components over the same file, later clock. Suspension still denies (`PARTITION_SUSPENDED`), successor still `LINEAGE_SUSPENDED`, the 40.0 charge still blocks a 61.0 refund against a 100 budget, the pending trial is still pending and expires at its original deadline, ledger length and chain intact. `SecondProcessTests`: a suspension written by one process is seen by another |
| 5. Global gate passes | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 13 files; 251 passed, 1 skipped (the Step 06 forward check), 40 subtests; `example.py`, `denials.py`, `mcp_demo.py` exit 0; `live_example.py` parses (needs API keys, not run) |
| Mutation check (v3 plan §5 rule) | PASS | Phase 1: each of six mutations of `store.py` fails the suite (deferred BEGIN → the cross-process test fails with "database is locked"; triggers removed; no rollback on failed commit; no nested join; `synchronous=NORMAL`; in-memory path allowed). The restart tests cannot run against v0.3.1 at all (there is no store to reopen); they fail by construction if any of the four kinds of state is held in memory |

**Design and statistical assumptions:**
- One database per host process group. `LifecycleManager` now refuses a shadow log and evidence store on different `Store`s, so a transition and its ledger record always commit together.
- Lifecycle records are the one mutable table (current state of a state machine); every change to them is a ledger event, as at v0.3.1.
- Budget charges are keyed by budget and scope, not policy version, so they survive `RiskPolicy.update()` exactly as before. Totals are summed in Python in commit order, not with SQL `SUM`, so a total is the same float v0.3.1 produced.
- Timestamps are stored as exact ISO text and ordered by an integer-microsecond column, because ISO strings with different UTC offsets do not sort by instant.
- `on_churn` now also records a lineage edge in the `lineage` table. The flag on the successor is unchanged; the edge is what Step 06 will use for policy-version successors (L12).

**Limitations / deviations:**
- **Tamper tests rewritten.** Seven tests (five C22 chain-limit tests, the audit-draw tamper test, and the replay tamper test) edited `ledger.events` as a Python list. On an append-only table that is impossible, so they now tamper through `tests/_support.py::rewrite_events`, which drops the ledger's triggers, rewrites the rows, and restores the triggers: the "anyone who can write the file" of C22. Their assertions are unchanged.
- `ShadowEvidenceLog.replay` takes a second, empty `Store` to rebuild into (the source database already holds its own trials).
- Tokens remain in memory under the thread lock: that is Step 03. SECURITY.md's gateway section is still accurate and unchanged.
- README: three statements that this step made false (evidence and ledger "in memory", "not persisted") corrected, and `store.py` added to the module table. The rest of the README reconciliation stays in Step 10.
- The reproduction hash was taken before the final `/simplify` refactor (moving one microsecond conversion into `store.delta_us`; integer arithmetic only, unchanged). The suite was re-run after it; the hash was not.
- Test suite time rose from ~20 s to ~35 s, nearly all in `tests/test_experiments.py` (fsync per transaction); the full experiment run takes ~3 minutes.
- Files touched: phase 1 three (`store.py`, `__init__.py`, `tests/test_store.py`); phase 2 five source files (`authority.py`, `ledger.py`, `shadow.py`, `lifecycle.py`, `risk.py`), `store.py`, `experiments/simulate.py`, four examples, ten existing test files, three new test files (`_support.py`, `conftest.py`, `test_durability.py`), README, and this report. Far over the 5-file guideline: every test, example, and the harness constructs these classes, and O-7 removes the constructor without a store, so no smaller set leaves the suite green.
- Python 3.10/3.13 not run locally; CI covers them.

**Unexpected behavior / open risks:**
- `tests/conftest.py` closes scratch stores after each test; without it, three file descriptors per WAL connection would exhaust macOS's default limit of 256 within the suite.
- A connection opened by a thread that exits stays open until `Store.close()`. Acceptable at prototype thread counts.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 03, transactional capability consumption, only on explicit request.
