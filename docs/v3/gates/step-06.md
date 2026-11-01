# Gate report — Step 06

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** Synchronous invalidation (serves F4; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. For every row of `revocation_fixture.json`: issue a token, record the event, then redeem with no intervening `evaluate()`; redemption denied with the event as reason | NOT RUN | |
| 2. Revocation and the event commit in one transaction (fault-injection test: a failure between them leaves neither) | NOT RUN | |
| 3. An admitted label that drops $L_t$ below $r$ revokes outstanding evidence-based tokens in the same transaction; one that keeps $L_t \ge r$ revokes nothing | NOT RUN | |
| 4. `redemption_blockers` recomputes the gate from current counts | NOT RUN | |
| 5. Policy-version successors inherit lineage suspension (closes L12) | NOT RUN | |
| 6. Grant revocation and principal deactivation cascade to descendant tokens | NOT RUN | |
| 7. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
