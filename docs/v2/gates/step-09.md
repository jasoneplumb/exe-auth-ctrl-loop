# Gate report — Step 09

**Branch / SHA:** `research/v2`, on top of `18dae89` (Step 08 manifest); Step 09 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-07, macOS, Python 3.14.4

**Step objective:** Claims-to-evidence map, manuscript plan and worked example from `n=0`, reproducibility appendix, release plan, lineage-preserving repository metadata. No tag, push, publication, or DOI.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Every main result and statistical/security claim has a path to code, config, raw data, and commit | PASS | `docs/paper-v2/claims-matrix.md` (33 claims + 5 limitations, each with code path, tests, data/config, commit); `tests/test_claims_matrix.py` verifies every referenced path and test exists and that the matrix cites the manifest's canonical hash |
| 2. Worked example starts from empty evidence; admission, qualification, invalidation, requalification documented; no 198/2 history | PASS | `docs/paper-v2/worked-example.md` from `results/{stable,suspension_cycle,turnover_slow}/trace.jsonl` seed 1: `n=0` at t=0, `QUALIFYING` at t=2, `AUTONOMOUS` at t=54 on 53/53, suspension at t=87 with one stale-token refusal, release at t=117, requalification at t=201, lineage suspension after churn at t=200 |
| 3. Manuscript states trusted components, shadow-oracle scope, dependence assumptions, unprotected strategic attacks | PASS | `manuscript-plan.md` §2, §4, §5, §10 and the introduction's non-claims; claims L1–L5; reviewer crosswalk table |
| 4. README/CITATION/changelog preserve `v0.2.0` lineage and DOIs while identifying the proposed version separately | PASS | README "Versions and lineage"; `CHANGELOG.md` `[Unreleased] - proposed 0.3.0` above an untouched `[0.2.0]`; `CITATION.cff` values unchanged (comment only); `release-plan.md` identifier table |
| 5. Clean-room reproduction instructions work; Step 08 hashes match before any release request | PASS | `reproducibility.md`; third independent full run (`python -m experiments.run --all`) reproduced canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a` with identical per-file hashes |
| 6. Release checklist complete; no unauthorized remote writes, tags, publication, or DOI claims | PASS | `release-plan.md` checklist with status; `git branch -a` shows no new remote refs; no tags created; `CITATION.cff` carries no new identifier |
| Required: `ruff check . && mypy src && pytest -q` | PASS | clean; clean; 192 passed (189 + 3) |
| Required: three offline examples | PASS | exit 0 |
| Required: Step 08 reproduction command re-run with hash comparison | PASS | identical (above) |

**Design and statistical assumptions:** None new. The documents restate A1–A5, R1–R3, and L1–L5 and add nothing.

**Limitations / deviations:**
- The manuscript is a plan with section-level sources, a change log, and a crosswalk, not prose. Related work is an outline; it must be written against sources, and the three citations in `docs/v2/statistical-method.md` are still unverified.
- Paper v1 is described in README as "manuscript on the v0.2.0 design, August 2026, not published" without naming a venue or outcome; the owner should decide whether to say more.
- "Nothing from `exe-auth-ctrl-loop-ip` is in the public repo" is asserted from the fact that no `-ip` material was read during Steps 00–09; the owner should confirm before pushing.
- Version `0.3.0` is the owner's choice and appears only in documents; `pyproject.toml`, `__version__`, and `CITATION.cff` remain `0.2.0` for the release process to bump.
- 11 files touched (new: `docs/paper-v2/` × 5, `tests/test_claims_matrix.py`, this report; modified: `README.md`, `SECURITY.md`, `CHANGELOG.md`, `CITATION.cff`). Documentation step; over the 5-file guideline.
- Python 3.10/3.13 not run locally at any step; CI must run before merge.

**Unexpected behavior / open risks:**
- `test_claims_matrix.py` caught a glob (`results/*/summary.json`) I had written as a path in the matrix; corrected to the concrete file. The test does not verify commit SHAs (shallow CI checkouts).
- Merging `research/v2` to `mainline` will trigger CI on 3.10 and 3.13 for the first time on this code; the harness tests add ~20 s.

**Decision: GO** (recommended; reviewer to confirm). This is the last step of the plan. Everything after it (PR, merge, tag, release, Zenodo, paper) is in `release-plan.md` and requires separate explicit authorization.

**Next permitted step (GO only):** None in the plan. Integration per `docs/paper-v2/release-plan.md` on authorization.
