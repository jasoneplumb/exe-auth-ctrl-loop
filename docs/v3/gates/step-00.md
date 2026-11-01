# Gate report — Step 00

**Branch / SHA:** `docs/v3-plan` from `mainline` @ `c58ce7db2ae48920cb1f180f36ceeef660d1955f`

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-01, macOS (Darwin 25.6.0), Python 3.14.8, existing `.venv`

**Step objective:** Read-only v3 baseline; map the six review findings to code and existing claims; write the v3 plan, draft contract, threat-model delta, and pre-registered gates. No behaviour change.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Baseline SHA, tags, version, and DOIs recorded; none moved | PASS | `docs/v3/baseline.md` Anchors; no tag or release commands run |
| 2. `ruff`, `mypy src`, `pytest -q`, offline examples run | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 12 files; 212 passed, 15 subtests; `example.py`, `denials.py`, `mcp_demo.py` exit 0 |
| 3. Each finding F1–F6 mapped to code locations and existing claim rows | PASS | `baseline.md` "Where each finding lands" |
| 4. Plan orders steps by dependency, with a gate per step and criteria fixed before work | PASS | `plan.md` §4; `gates/step-01.md` … `step-10.md` (all NOT RUN) |
| 5. What v3 does not close is stated | PASS | `plan.md` §2 (F1 process separation, F2 cross-host, F3 external anchor, F5 key strength); `spec.md` §4.3; `threat-model.md` |
| 6. No behaviour, tag, or DOI changed | PASS | Diff is `docs/v3/` additions only |

**Design and statistical assumptions:** One owner decision recorded (O-1, durable same-process). O-2 to O-8 are recommendations awaiting confirmation. The pessimistic-pending proposition in `plan.md` Step 09 is a conjecture with a proof sketch; it depends on a monotonicity property not yet proved.

**Limitations / deviations:**
- 15 files added, all under `docs/v3/`. Documentation step; over the 5-file guideline, as v2 Step 09 was.
- `spec.md` is a draft; Step 01 finalizes it and adds a machine-readable fixture for spec §5.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- README states 203 tests; the suite has 212 (`baseline.md`, discrepancies). Left for Step 10.
- Several steps (02, 05, 08, 09) are estimated well above v2 step sizes; each may need splitting at its own planning.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 01, only on explicit request.
