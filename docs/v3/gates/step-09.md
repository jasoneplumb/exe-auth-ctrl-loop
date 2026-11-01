# Gate report — Step 09

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** Statistical evaluation (serves F6; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Harness drives shipped components; the four L6 differences are gone (each checked by a test) | NOT RUN | |
| 2. All five scenario families in `plan.md` Step 09 run at ≥1000 seeded sequences per cell; results committed with configs | NOT RUN | |
| 3. Monotonicity of the Beta-mixture lower bound in successes at fixed n proved in `statistical-method.md` (or the pessimistic-pending proposition withdrawn) | NOT RUN | |
| 4. For each candidate estimator change: false-authorization rate with and without, and median trials to qualify at p = 0.97; owner decides O-3, O-4, O-8 from this table | NOT RUN | |
| 5. Every scenario that violates A1–A5 is labelled so in its summary | NOT RUN | |
| 6. Three independent full runs give one canonical hash | NOT RUN | |
| 7. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
