# Gate report — Step 00

**Branch / SHA:** `research/v2` from `mainline` @ `86fcf83b5e387c9095c184eacdcb5b6b677d91f1`

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-06, macOS (Darwin 25.6.0), Python 3.14.4, fresh venv

**Step objective:** Read-only baseline and v2 workflow preparation.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Original commit SHA and all pre-existing tags recorded, none moved | PASS | `docs/v2/baseline.md` Anchors (v0.1.0 `f070e01`, v0.2.0 `b266eb7`); no tag commands run |
| 2. `ruff`, `mypy src`, `pytest -q`, offline examples run | PASS (local, Python 3.14.4 only) | ruff clean; mypy 0 issues; 41 passed; `example.py`, `denials.py`, `mcp_demo.py` exit 0. Python 3.10/3.13 NOT RUN locally (CI matrix) |
| 3. `baseline.md` maps authority, executor, ledger, tests, README, CI, CITATION | PASS | `docs/v2/baseline.md` Component map |
| 4. Both gaps documented (empty-store qualification; tokens survive suspension) | PASS | `docs/v2/baseline.md` Documented gaps, with source citations |
| 5. No behavior, tag, or DOI changed | PASS | Diff is `docs/v2/` additions only |
| Global quality gate | PASS (see criterion 2) | |

**Design and statistical assumptions:** None introduced.

**Limitations / deviations:** Python 3.10 and 3.13 not run locally. The `v0.2.0` tag precedes `mainline` HEAD by doc-only commits; v2 is based on HEAD.

**Unexpected behavior / open risks:** `ci.yml` omits `examples/denials.py`.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 01 — `steps/01_spec.md`, only on explicit request.
