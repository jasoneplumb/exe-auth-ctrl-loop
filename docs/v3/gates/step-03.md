# Gate report — Step 03

**Status:** completed 2026-11-01. Criteria were pre-registered at Step 00 (letter-suffixed ones added from the PR #23 review, before any work) and are reported unchanged.

**Step objective:** Transactional capability consumption (serves F1; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-03-consume` from `mainline` @ `6dd25e0`; Step 03 commit follows this report. Issue #28.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-01, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Redemption is one transaction ending in a conditional update with rowcount 1; the thread lock is removed | PASS, with an ordering note (deviations) | `ExecutionGateway.execute`: one `BEGIN IMMEDIATE` transaction containing `UPDATE tokens SET state='consumed' WHERE token_id=? AND state='issued'` (rowcount must be 1), every v2 redemption check, and guard settlement; the commit is the linearization point. `threading.Lock` and its import are gone from `authority.py` |
| 2. Two OS processes redeeming one token through separate connections: exactly one handler call, over at least 200 trials | PASS | `test_consume.py::CrossProcessRedemptionTests`: 200 tokens, two worker processes started on one signal, each attempting every token in the same order; handlers write to an `effects` table with no uniqueness. Result: 200 effects, exactly one per token, both workers exit 0, every token `handler_completed`. Both workers win tokens in every run (splits measured over five runs: 9/191, 15/185, 2/198, 31/169, 10/190), so the redemptions interleave and the trailing worker presents each token the leader consumed |
| 3. Mutation check: with the `state='issued'` condition removed, the cross-process test FAILS (recorded output in this report). Closes C15 | PASS | Recorded output below. **A** (condition removed): `1 failed` — the trailing worker's update hits the transition trigger, `sqlite3.IntegrityError: illegal token transition`, worker exit code 1. **B** (condition and transition trigger both removed): `AssertionError: 400 != 200` — every handler ran twice. **C** (trigger removed, condition kept): `1 passed` — the condition alone suffices. Closes C15: unlike the v2 concurrency tests, this one fails when the single-use guard is removed |
| 4. Restart: consumed stays consumed; an issued unexpired token redeems exactly once after reopening; a `handler_started` token is never re-run | PASS | `test_consume.py::RestartTests`: `test_consumed_stays_consumed`, `test_an_issued_token_redeems_exactly_once_after_reopening`, `test_a_handler_interrupted_by_a_crash_is_reported_and_never_rerun` (a subprocess calls `os._exit(3)` inside the handler; after reopening, the token is `handler_started`, redemption is refused, and the handler is not called) |
| 4b. `effect_unknown()` lists every token left in `handler_started` after a restart | PASS | Same crash test (`effect_unknown() == [token_id]`); `test_effect_unknown_is_empty_when_every_handler_reported` |
| 5. One decision issues at most one token | PASS | `tokens.decision_id UNIQUE`; `issue()` checks before insert |
| 5b. A second `issue()` for a `decision_id` is refused whatever the first token's state (issued, revoked, consumed), at the API and by the database unique constraint | PASS | `test_a_second_issue_is_refused_whatever_the_first_tokens_state` (three subtests), `test_the_database_refuses_a_second_row_for_a_decision` (raw INSERT → `IntegrityError` naming `decision_id`) |
| 6. Global gate passes | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 13 files; 266 passed, 1 skipped (Step 06 forward check), 53 subtests; three offline examples exit 0; `denials.py` output byte-identical to Step 02. Full experiment reproduction: canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a`, identical to `results/manifest.json` |

**Recorded mutation output (criterion 3):**

```text
### A: WHERE state='issued' removed (trigger kept)
E           AssertionError: 1 != 0 : Traceback (most recent call last):
E           sqlite3.IntegrityError: illegal token transition
tests/test_consume.py:111: AssertionError
1 failed in 0.72s
### B: WHERE removed AND transition trigger removed
E       AssertionError: 400 != 200
tests/test_consume.py:115: AssertionError
1 failed in 0.94s
### C: transition trigger removed only
1 passed in 0.71s
```

**Design and statistical assumptions:**
- **Consume first, check after, in one transaction.** The conditional update runs before the expiry, scope, and live-state checks, and any refusal rolls it back. That makes the update the only single-use guard (no Python-side state check stands in for it, which is what lets criterion 3 bite), and keeps v0.3.1's denial order: a replayed token is still reported as "missing, consumed, or revoked".
- **Transition and fixed-field triggers.** The database refuses every state change spec §3 does not list, including a same-state update (so a lost `WHERE` fails loudly), any change to a field fixed at issue, and any delete. Tokens are current state, not history, so they are not in `APPEND_ONLY_TABLES`.
- **Guard settlement is in the consume transaction.** When a guard (e.g. `RiskPolicy`) shares the gateway's Store, a budget charge and a consume commit together. A guard whose `commit()` raises now rolls the consume back (token unspent), where v0.3.1 left the token spent with nothing settled.
- **Revoking a token that is no longer `issued` changes nothing.** v0.3.1 set `revoked=True` even on a consumed token; v3 has no transition out of a consumed state.
- **Handler states** `handler_started` → `handler_completed` / `handler_failed` are each their own transaction after the consume commits.

**Limitations / deviations:**
- **Criterion 1 wording.** It says the transaction *ends* in the conditional update; here the update comes first and the checks follow it inside the same transaction. The property the criterion protects (one transaction, a conditional update with rowcount 1 as the single-use guard, no lock) holds; the order is the design choice above.
- **`issue()` returns an issue-time snapshot.** Current state is read from the database (`gateway.token(id)`, `gateway.tokens`). Four lines in `test_gateway_revocation.py` change from `token.used` / `.revoked` / `.revocation_reason` to the stored token. Three of them check the same fact; the fourth (`test_revoke_and_redeem_cannot_both_win`) now asserts `revoked == (redeem lost the race)` instead of `revoked is True`, because a consumed token can no longer become revoked (design note above). That is a changed assertion, stated here.
- `test_effect_check_is_independent_of_digest` forged a token by mutating the in-memory object; it now forges the stored row through `tests/_support.py::forge_token`, which drops the `tokens_fixed` trigger as a writer of the file could. Assertion unchanged.
- `ExecutionGateway` gains a required keyword `store`; all 22 construction sites updated, using the evidence store's Store where one is given so tokens, evidence, and charges share a database.
- README limitations row and SECURITY.md "Known limitations" updated for the transactional consume (marked as `mainline`, unreleased). `docs/v2/gateway-redemption.md` is left as the v2 record; Step 10 reconciles.
- Files touched: `authority.py`, `store.py`, `risk.py`, `lifecycle.py`, `executor.py` (docstrings and comments only in the last three), nine test files including the new `test_consume.py` and `_support.py`, four examples, `experiments/simulate.py`, README, SECURITY.md, this report. Over the 5-file guideline for the same reason as Step 02: every construction site changes.
- Python 3.10/3.13 not run locally; CI covers them.

**Unexpected behavior / open risks:**
- The trailing worker in the cross-process test usually loses most tokens (it starts slightly later), so contention is concentrated at the start of the run. Both workers win tokens in every measured run, and the mutation results show the test detects a broken guard; a stricter barrier per token was not needed to meet the criterion.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 04, execution boundary, only on explicit request.
