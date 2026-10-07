# Gate report — Step 07

**Branch / SHA:** `research/v2`, on top of `a5181c9` (Step 06); Step 07 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-07, macOS, Python 3.14.4

**Step objective:** Make the draw-commit-act order real and fail-closed; separate selection randomness, event ordering, and rewrite resistance; align claims with what the chain proves.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Ledger append and audit-selection record verifiably precede adapter calls/outcomes | PASS | `EventLedger.commit_decision` called by the executor between `evaluate()` and `issue()`; `test_decision_and_draw_are_committed_before_the_handler_runs` (handler observes the committed record, and `execution.step.recorded` not yet present; sequence strictly ordered); `test_approved_audit_executes_only_after_the_commit` |
| 2. Simulated append failure causes denial; unlogged execution impossible in the tested path | PASS | `test_commit_failure_refuses_the_step_and_issues_no_token` (`RefusingLedger`; step `DENY`, no handler call, `gateway.tokens == {}`); `CrossModelAuthorityLoop.__post_init__` wires the hook, `test_loop_wires_the_commit_hook` |
| 3. In-memory chain detects internal tampering; rewrite/truncation limits explicit and test-covered | PASS | `test_interior_edit_is_detected`, `test_tampering_with_the_committed_draw_breaks_the_chain`; limits: `test_truncation_is_not_detected_by_verify_alone`, `test_wholesale_rewrite_is_not_detected_by_verify_alone` (both assert `verify()` returns True); `docs/v2/audit-commitment.md` attack table |
| 4. Independence claim qualified by trusted RNG/controller; no unsupported proof language | PASS | `authority.py` comment "provably independent" replaced; README claims 9 and 12 rewritten; `ledger.py` module docstring; doc §"What independent means here" (R1–R3) |
| 5. Global quality gate | PASS | `ruff` clean, `mypy src` clean (12 files), 172 passed (157 + 15), three offline examples exit 0 |
| Required: `pytest -q tests/test_audit_commitment.py` | PASS | 15 passed |
| RNG seeded/replay behaviour | PASS | `test_seeded_rng_replays_the_same_committed_draw`, `test_different_seeds_change_the_draw_not_the_contract` |
| Anchoring kept outside the guarantee | PASS | `anchor()`/`verify_anchor()` documented as demonstration; `ChainLimitTests`; README production bullet |

**Design and statistical assumptions:** R1 trusted RNG (`SystemRandom` by default; seeded RNG labelled deterministic), R2 trusted controller commits before acting, R3 models cannot observe RNG state (TM-10). The claim is an ordering guarantee under R1–R3, not a proof against a malicious host.

**Limitations / deviations:**
- An executor constructed without the loop and without a `commit` hook records nothing (`test_agent_without_a_commit_hook_records_nothing`). Allowed for unit tests and offline demos; the loop is the enforcement point. `examples/*.py` call the gateway directly and never had a ledger; unchanged.
- The gateway itself does not append to the ledger; denials raised by the gateway are recorded by the loop after the step. The *decision* is what is committed before action; outcome records remain post hoc by nature.
- Anchoring is in-process only and proves nothing by itself; stated in three places.
- **O-2 remains open.** `AUDIT` still means a pre-execution human review, as in v1. Switching to execute-then-label (spec §3) changes what runs unattended; left for the owner to decide, not folded into an ordering change.
- 8 files touched (modified: `authority.py`, `executor.py`, `ledger.py`, `pipeline.py`, `README.md`; new: test, doc, this report).
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- The pre-existing `ledger.py` docstring claimed the draw "lands here before any outcome exists"; before this step the pipeline appended decisions only after the whole run. The claim is now true in the wired path and the docstring says so.
- Two first-draft test mistakes (seed 7 at audit rate 0.5 draws an audit; bound-method identity vs equality). Test-only; no production change.

**Decision: GO** (recommended; reviewer to confirm, and to decide O-2)

**Next permitted step (GO only):** Step 08 — `steps/08_experiments.md`, only on explicit request.
