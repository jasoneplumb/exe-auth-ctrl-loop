# Gate report — Step 08

**Status:** pre-registered. Criteria fixed at Step 00 (2026-11-01) before any work; to be filled in when the step ends. A criterion may be tightened at Step 01 but not loosened without the owner's recorded decision.

**Step objective:** Evidence admission (serves F3; see `docs/v3/plan.md` §4).

**Branch / SHA:** —

**Reviewer:** (pending, one person decides)

**Date / environment:** —

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Provenance derived from the committed decision; no API accepts it as an argument | NOT RUN | |
| 2. Shipped loop freezes every human-routed proposal on a non-autonomous partition as `SHADOW` before approval is requested; a partition reaches `AUTONOMOUS` from `n=0` through `CrossModelAuthorityLoop` with fake providers, no harness code (closes C5 caveat, L6 first difference) | NOT RUN | |
| 3. Labels: bad MAC, unregistered or inactive oracle, `WRONG_ORACLE`, `REVIEWER_IS_ORACLE` each quarantined and ledgered | NOT RUN | |
| 4. Deadline on `received_at`; a backdated `claimed_at` cannot admit a late label | NOT RUN | |
| 5. Ledger: HMAC chain, contiguous sequence check; the C22 re-hash attack without the key is detected | NOT RUN | |
| 6. Replay of the append-only tables reproduces counts and quarantines exactly | NOT RUN | |
| 7. Global gate passes | NOT RUN | |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | NOT RUN | |

**Design and statistical assumptions:** —

**Limitations / deviations:** —

**Unexpected behavior / open risks:** —

**Decision:** —

**Next permitted step (GO only):** —
