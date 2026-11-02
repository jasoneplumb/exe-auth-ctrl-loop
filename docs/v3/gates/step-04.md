# Gate report — Step 04

**Status:** completed 2026-11-02. Criteria were pre-registered at Step 00. Criterion 3 is reported under the owner's interpretation recorded below (decision before work began, 2026-11-02); no other criterion was changed.

**Step objective:** Execution boundary (serves F1; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-04-boundary` from `mainline` @ `de841a7`; Step 04 `c6ec990`, review fixes follow. Issue #30, PR #31.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-02, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1

**Owner decisions taken at the start of this step (2026-11-02):**
- **Boundary.** Criterion 3 cannot hold literally: whatever can request an effect must reference the gateway, and the gateway references the handler table. `ExecutionGateway` and `AuthorityController` are therefore the host's boundary objects. The reachability walk treats them as opaque and must find no handler, `HandlerTable`, or signing key by any other route. Spec §1 reworded to match. Rejected: a narrow port object; dropping the criterion.
- **Commit.** `EventLedger.commit_decision` writes the `decisions` row in the same transaction as its ledger event, so "committed" means "on the tamper-evident record". An agent with no commit hook can no longer execute. Rejected: the controller commits; a row with no ledger link.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. `ToolCatalog` / `HandlerTable` split; agent receives the catalog only | PASS | `tools.py`: `ToolCatalog` (no `execute`), `ToolDefinition` (no `handler` field), `HandlerTable(catalog)`. `ClaudeExecutionAgent(..., catalog, ...)` and `OpenAIProposalGenerator(..., catalog, ...)`; `ExecutionGateway(..., handlers=)`. `test_boundary.py::CatalogAndHandlerTableTests` |
| 2. `ExecutionGateway.execute(token_id, proposal)` has no adapter parameter; handler chosen by `token.allowed_tool` | PASS | `test_execute_takes_no_handler` (signature is exactly `self, token_id, proposal`); `test_the_handler_is_the_one_the_token_names` (two tools registered, the token's tool runs); `test_another_tools_proposal_cannot_spend_the_token`. Arguments are validated and the handler resolved *inside* the consume transaction, so a schema failure or missing handler leaves the token unspent (`test_arguments_the_schema_refuses_do_not_spend_the_token`, `test_a_tool_with_no_handler_does_not_spend_the_token`) |
| 3. Reachability walk from agent, proposer, and pipeline finds no handler callable and no `HandlerTable`; the test fails if `executor.py` is given the table | PASS (under the owner's interpretation above) | `test_nothing_handed_to_the_agent_side_reaches_a_handler` over the shipped composition (catalog, handler table, controller, gateway, executor, proposer, pipeline with shadow log). The walk follows instance attributes, containers, bound methods, closures, and defaults; not module globals, classes, or the two boundary types. Positive controls: `test_an_agent_given_the_table_is_caught`, `test_a_handler_hidden_in_a_closure_is_caught`. **Source mutation** (`self._handlers = gateway.handlers` added to `ClaudeExecutionAgent.__init__`): the test fails for both the agent and the pipeline (recorded below) |
| 4. `issue()` refuses a decision absent from `decisions` or differing in digest or route (closes L7 first half, enforces C20) | PASS | Schema v3 `decisions` (append-only). `test_boundary.py::CommittedDecisionTests`: uncommitted, hand-built, and route-altered-after-commit decisions refused; a committed one issues; a second commit of one decision fails on the primary key and appends nothing. `test_audit_commitment.py::test_agent_without_a_commit_hook_cannot_execute` |
| 5. Same-process reflection residual stated in README and SECURITY.md | PASS | README invariant 1; SECURITY.md "Scope notes" (in scope: any route to a handler except `ExecutionGateway.execute`, any token for an uncommitted decision; out of scope and stated: `gc.get_objects()` / frame inspection in the same process) |
| 6. Global gate passes; offline examples updated and exit 0 | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 13 files; 286 passed, 1 skipped (Step 06 forward check), 58 subtests; `example.py`, `denials.py`, `mcp_demo.py` exit 0; `live_example.py` parses (API keys needed, not run). Full experiment reproduction: canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a`, identical to `results/manifest.json`, although the harness now commits every decision to the ledger before issuing; reproduced before and after the `executable_route()` refactor (at `c6ec990`). The PR #31 review fixes narrow one `except` and change tests and comments only |

**Recorded mutation output (criterion 3):**

```text
### mutation: executor holds gateway.handlers
E               AssertionError: Lists differ: [<exe_auth_ctrl_loop.tools.HandlerTable ob[134 chars]c10>] != []
tests/test_boundary.py:219: AssertionError
E               AssertionError: Lists differ: [<exe_auth_ctrl_loop.tools.HandlerTable ob[134 chars]c10>] != []
tests/test_boundary.py:219: AssertionError
```

**Design and statistical assumptions:**
- **Validation and resolution before the consume commits.** `HandlerTable.resolve()` validates the arguments and finds the handler inside the consume transaction and returns the call, which runs after commit. A refusal there rolls the consume back. At v0.3.1 validation ran inside the adapter, after the token was spent.
- **One statement of which routes issue.** `executable_route()` in `authority.py` is used by both `issue()` and the executor's error reporting.
- **Executor reporting.** When the gateway refuses an executable route (e.g. an uncommitted decision), the step's route is `DENY` and its error is the gateway's reason, not "authority route: autonomous".
- **The harness commits decisions.** `experiments/simulate.py` calls `ledger.commit_decision` before `issue()`, as the shipped loop does, closing one of the four L6 differences ("never commits decisions to the ledger"). Results are byte-identical: extra ledger events shift sequence numbers, but every evidence comparison (epoch floors, freeze order) is relative.

**Limitations / deviations:**
- **Criterion 3 interpretation** (owner decision above). The walk proves there is no route to a handler except the gateway; it does not prove the gateway is unreachable, which is impossible for an object that must request effects. Same-process reflection remains (spec §4.3, O-1).
- **One changed assertion.** `test_agent_without_a_commit_hook_records_nothing` became `test_agent_without_a_commit_hook_cannot_execute` and asserts DENIED with no handler call, no token, and an empty ledger: the enforcement the owner chose.
- **Test mechanics.** Gateway-mechanism tests (`test_authority`, `test_gateway_revocation`, `test_consume`, `test_lifecycle`, `test_mcp`, the risk-envelope gateway) supply their handler through `tests/_support.py`: `AdapterHandlers` is a stand-in table, `run()` replaces `gateway.execute(token, proposal, adapter)`, and `issue()` commits the decision before issuing. Their assertions are unchanged. Executor-path tests (`test_providers`, `test_audit_commitment`, the risk-envelope executor test) use a real `ToolCatalog` + `HandlerTable` and a ledger commit hook.
- **`denials.py` output.** Its handler recorded the proposal id; a handler receives only the tool arguments, so it now records those. The "first use" line and the README block that quotes it changed accordingly.
- **`mcp_demo.py`** gives its gateway an empty handler table: the effect runs on the MCP server. Step 07 makes MCP-bound tokens explicit.
- Breaking API: `ToolRegistry` → `ToolCatalog`; `ToolDefinition` has no `handler`; `ExecutionGateway(..., handlers=)` required; `execute(token_id, proposal)`; `ClaudeExecutionAgent` / `OpenAIProposalGenerator` take `catalog` (keyword renamed from `registry`); `issue()` needs a committed decision.
- Files touched: 6 source files (`authority.py`, `executor.py`, `ledger.py`, `providers.py`, `store.py`, `tools.py`, plus `__init__.py`), 11 test files including the new `test_boundary.py`, four examples, `experiments/simulate.py`, README, SECURITY.md, spec §1, this report. Over the 5-file guideline for the reason of Steps 02–03.
- Python 3.10/3.13 not run locally; CI covers them.

**Unexpected behavior / open risks:**
- `AdapterHandlers` raises if a test calls `gateway.execute` without `run()`, so a test cannot silently reach a stale handler.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 05, principals, grants, lineage, only on explicit request.
