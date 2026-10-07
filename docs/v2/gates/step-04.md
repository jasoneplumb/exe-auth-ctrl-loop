# Gate report — Step 04

**Branch / SHA:** `research/v2`, on top of `74093d8` (Step 03); Step 04 commit follows this report

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-10-06, macOS, Python 3.14.4

**Step objective:** Replace the fixed-sample Wilson bound, for new autonomous authorization, with an anytime-valid Beta-mixture confidence sequence under stated assumptions; keep Wilson as an opt-in legacy comparator.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Method note states a named sequential validity proposition and its actual assumptions, incl. limits under adaptive behaviour | PASS | `docs/v2/statistical-method.md` §3: proposition, martingale + Ville argument, sources (Ville 1939; Robbins 1970; Kaufmann & Koolen 2021), A1-A5, explicit "does not say" list covering delayed defection |
| 2. Multiple looks and multiple partitions have a defined error-budget policy | PASS | §4 (one α covers every look in an epoch), §5 (per-partition, per-epoch α; joint claims only by Bonferroni with K recorded; risk classes are separate partitions) |
| 3. Empty sample cannot qualify; reference vectors reproduce independently | PASS | `test_empty_sample_is_zero`, `test_no_successes_is_zero`; 15 vectors in §9 verified by exact-rational product (no `lgamma`) in `test_vectors_sit_on_the_boundary_by_independent_computation`; closed form S=1 in `test_single_success_closed_form` |
| 4. `tests/test_sequential_gate.py` passes deterministic and stopping-time checks; coverage simulation labelled illustrative with non-flaky criteria | PASS | 18 tests; `IllustrativeSimulationTests` docstring and §10; margins ≥5 SE (observed 0.4-1.1% vs 8% ceiling; Wilson 11-14% vs 5% floor) |
| 5. No README/manuscript text represents Wilson or the new method as protecting against arbitrary delayed defection | PASS | README "Conservative bound and policy" rewritten; `wilson_lower_bound` docstring marked legacy; §3 of the method note |
| 6. Global quality gate | PASS | `ruff` clean; `mypy src` clean (11 files); 110 passed (92 before + 18); `example.py`, `denials.py`, `mcp_demo.py` exit 0 |
| Required: `pytest -q tests/test_sequential_gate.py tests/test_lifecycle.py` | PASS | 37 passed |
| Lifecycle guard uses the new bound; estimator and α are policy identity | PASS | `Policy.lower_bound`, `test_default_policy_uses_the_sequential_bound`, `test_legacy_wilson_is_opt_in_only`, `test_alpha_is_validated_and_part_of_policy`; method note §7 |

**Design and statistical assumptions:** Jeffreys prior Beta(1/2, 1/2); α = 0.05 default, per partition and epoch; validity only for i.i.d. Bernoulli labels within an epoch (A1), plus A2-A5 from the spec. `n_min` is a separate policy floor and does not affect validity.

**Limitations / deviations:**
- The new bound is more conservative than Wilson at every n (60 perfect trials to clear 0.90 instead of 40; 297/3 to clear 0.95 instead of 198/2). Fixtures in `test_lifecycle.py`, `test_shadow_evidence.py`, and the three offline examples were re-seeded accordingly. The seeded examples remain demonstrations of the decision logic, not a bootstrap path.
- This phase touched 13 files (new: `sequential.py`, `test_sequential_gate.py`, `statistical-method.md`, this report; modified: `authority.py`, `lifecycle.py`, `__init__.py`, `test_lifecycle.py`, `test_shadow_evidence.py`, `README.md`, three examples). Well over the 5-file phase guideline; the overrun is the fixture re-seeding forced by the stricter bound.
- Citations in the method note were written from memory and could not be checked against the papers offline; the reviewer should confirm volume/issue numbers before any manuscript use.
- Exact-rational verification covers vectors up to 2000 trials; the 101,000-trial vector is checked by an independent log-sum product instead (still `lgamma`-free).
- `crosses()` is a simulation helper; the controller never uses it.
- Python 3.10/3.13 not run locally.

**Unexpected behavior / open risks:**
- First draft of the Wilson illustration used p = 0.88 and did not exceed 5% (observed 3.9%, which is still above the 2.5% nominal). Re-pointed at the boundary p = r = 0.90, where seeded runs give 11-14% and the rate grows with the horizon. The measured table is in §10.
- Default-estimator change alters v0.2.0 behaviour for any caller constructing `Policy` without `estimator`. Intended: it is a policy-identity change and the spec requires a new policy version for it. Release notes (Step 09) must say so.

**Decision: GO** (recommended; reviewer to confirm, including the citation check)

**Next permitted step (GO only):** Step 05 — `steps/05_revocation.md`, only on explicit request.
