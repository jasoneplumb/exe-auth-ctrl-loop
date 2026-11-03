# Gate report — Step 07

**Status:** completed 2026-11-02. Criteria were pre-registered at Step 00 (letter-suffixed ones added from the PR #23 review, before any work) and are reported unchanged.

**Step objective:** MCP server-side redemption (serves F2, closes the rest of L3; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-07-mcp` from `mainline` @ `611dd95`; Step 07 `e6347b7`, PR #37 review fixes follow. Issue #36.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-02, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Extension 0.2 with `audience` and `redemption` signed; 0.1 blocks refused | PASS | `mcp.py`: `EXTENSION_VERSION = "0.2"`, `KEY_AUDIENCE` and `KEY_REDEMPTION` in the signed set. `test_mcp_redemption.py::WireFormatTests`: the block carries both; changing either breaks the MAC; a 0.1-shaped block (no audience, no redemption, version `0.1`, validly signed) is refused as an unsupported version and runs nothing |
| 2. Consequential call: replayed block to the same server denied after the first redemption; block to a different server denied by audience | PASS | `ConsequentialCallTests::test_the_call_runs_once_and_a_replay_is_refused` (one effect, token `handler_completed`, replay refused as consumed); `test_another_server_is_refused_by_audience` (refused at verification, token still `issued`, no effect) |
| 3. Block for a token revoked by any spec §5 event denied at the server | PASS | `test_a_revoked_token_is_refused_at_the_server` (RV-02, denial reads `revoked by EVIDENCE_INVALIDATED`); the server path goes through the same `_consume` as local redemption, so every §5 event applies; `test_a_revoked_token_is_refused_on_the_nonce_path` for the nonce path |
| 4. Non-consequential nonce path: second presentation denied by the uniqueness constraint, including across two processes | PASS | `NonceTests::test_a_second_presentation_is_refused` (`already presented`; the token is not consumed on this path, by design); `test_two_processes_present_the_same_block_and_one_is_accepted` (two OS processes, exactly one accepted, the other refused by the `nonces` primary key) |
| 5. Host does not consume MCP-bound tokens locally (test fails if it does) | PASS | `ChannelTests::test_the_host_does_not_consume_an_mcp_token` (`execute()` refused, token still `issued`, then the server spends it). Mutation (channel check made one-way) fails it |
| 5b. `ExecutionGateway.execute()` refuses a token with `redemption_channel='mcp'`; server redemption refuses a `local` token and an audience differing from the stored `mcp_audience` | PASS | Same test; `test_server_redemption_refuses_a_local_token_and_a_wrong_audience`; `test_server_redemption_checks_the_call_against_the_stored_proposal` |
| 5c. Blocks of any version other than `0.2` (including `0.1` and an unknown `0.3`) are refused | PASS | `test_only_version_0_2_is_accepted`: `0.1`, `0.3`, `1.0`, and a missing version, each validly signed, all refused as unsupported |
| 6. `docs/mcp-extension.md` updated with migration note; `mcp_demo.py` exits 0 | PASS | The doc gains the two keys, the 0.2 verification order, "Spending the block", "Migrating from 0.1", and the remaining shared-secret limit. `mcp_demo.py` drives `McpServer` and now also shows a replayed block and a block for another server refused |
| 7. Global gate passes | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 15 files; 353 passed, 95 subtests; three offline examples exit 0 |
| Mutation check (v3 plan §5 rule) | PASS | Each fails `test_mcp_redemption.py`: host may spend MCP tokens (1 failure); verification ignores audience (1); nonce insert ignores duplicates (2); version check as a denylist of `0.1` only (3) |

**Experiment reproduction:** canonical hash `4cc7a0e38ed7d60dd064fbf8b62fa6f3b04864c9b8c6aaa1fd131d6ea6785ea6`, identical to Step 06's (the harness uses no MCP, and the `execute()` refactor keeps its checks and their order). Not re-run after the review fixes: the only change on the harness's path wraps `_consume` in a transaction that joins the one its caller already holds. `results/` remains the v0.3.x artifacts until Step 09 (owner decision at Step 06).

**Design and statistical assumptions:**
- **One consume path.** `execute()`, `redeem_remote()`, and `accept_nonce()` share `_consume` / `_check`: the channel and audience are checked *before* any write (a token presented on the wrong channel is not spent), then the conditional update, then the same expiry, scope, and live-state checks. The server path therefore inherits every spec §5 revocation and every redemption guard.
- **The call is checked against the stored proposal.** The server recomputes the call digest from what it received; the host recomputes it from the proposal stored with the token (schema v5) and compares, inside the consume transaction.
- **Nonce path does not consume.** Spec §6 rule 3: the nonce row is the single use. The token stays `issued` and remains subject to revocation.
- **Audience is a principal.** `issue(..., audience=)` requires an active SERVICE principal (Step 05), recorded as `mcp_audience` with `redemption_channel = mcp` (schema v6).
- **The receipt carries the authority** (PR #37 review). `McpServer.call()` returns a `CallReceipt` (handler result and the `VerifiedAuthority` it ran on), so no caller re-verifies a block after spending it; the demo's second verification, which could fail after the effect, is gone. `_consume` owns its transaction (joining the caller's) rather than reaching for the store's connection. The nonce path's lack of handler states is documented as deliberate.
- **Version first.** `verify_call_meta` refuses any version other than exactly `0.2` before reading other keys, so an old block is refused as old, not as malformed. The MAC still covers the field.

**Limitations / deviations:**
- **Shared secret.** One HMAC secret signs blocks for every server; a server holding it could mint a block addressed to another. Per-server keys are a production requirement (stated in the doc, README, SECURITY.md).
- **Same database.** The server spends tokens in the host's database (one host, O-1). Across hosts `redeem_remote` and `accept_nonce` become a network service.
- **Test changes.** `verify_call_meta` takes a required keyword `audience`; the twelve calls in `test_mcp.py` pass it and its tokens are issued with `audience=` (construction only; no assertion changed). `tests/_support.py` registers an `mcp-server` SERVICE principal and `issue()` takes `audience=`. One raw-SQL copy in `test_consume.py` lists the two new columns.
- Files touched: `authority.py`, `mcp.py`, `store.py`, `__init__.py`, `mcp_demo.py`, `docs/mcp-extension.md`, README, SECURITY.md, four test files (new `test_mcp_redemption.py`), this report.
- Python 3.10/3.13 not run locally; CI covers them.

**Unexpected behavior / open risks:**
- The cross-process nonce test first failed because its workers used the wall clock while the token was issued against the test's fixed clock (expired); the workers now take the test's clock. Not a code defect.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 08, evidence admission, only on explicit request.
