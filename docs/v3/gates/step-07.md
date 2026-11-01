# Gate report — Step 07

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** MCP server-side redemption (serves F2; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Extension 0.2 with `audience` and `redemption` signed; 0.1 blocks refused | NOT RUN | |
| 2. Consequential call: replayed block to the same server denied after the first redemption; block to a different server denied by audience | NOT RUN | |
| 3. Block for a token revoked by any spec §5 event denied at the server | NOT RUN | |
| 4. Non-consequential nonce path: second presentation denied by the uniqueness constraint, including across two processes | NOT RUN | |
| 5. Host does not consume MCP-bound tokens locally (test fails if it does) | NOT RUN | |
| 6. `docs/mcp-extension.md` updated with migration note; `mcp_demo.py` exits 0 | NOT RUN | |
| 7. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
