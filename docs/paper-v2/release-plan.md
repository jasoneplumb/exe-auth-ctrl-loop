# Release plan (proposed 0.3.0) and checklist status

Written at Step 09 on `research/v2`; status updated at release. `research/v2` was merged to `mainline` as #10 (squash `1924c9b`, 2026-10-08) after three review cycles, and `0.3.0` was tagged and released the same day by the owner's `/release`. Zenodo deposit of the paper and its submission remain separate authorizations.

## Identifiers

| Item | Value | Status |
|---|---|---|
| Software version | `0.3.0` (owner's choice, 2026-10-07) | released 2026-10-08; `pyproject.toml`, `__init__.__version__`, and `CITATION.cff` bumped in the release commit |
| Software tag / GitHub release | `v0.3.0` | created 2026-10-08 |
| Zenodo version DOI for 0.3.0 | 10.5281/zenodo.23226913 | minted on release 2026-10-08; in `CITATION.cff` `identifiers` |
| Concept DOI | 10.5281/zenodo.21983061 | unchanged; resolves to the latest version |
| Paper v2 | `manuscript.md` (draft, 2026-10-08), per `manuscript-plan.md` | drafted; not submitted; venue undecided |
| Paper v2 preprint DOI | — | none; added only when issued |
| Paper v1 | August 2026 manuscript on v0.2.0, not published | retained as is; not rewritten |

Paper versioning is independent of software semantic versioning: paper v2 cites software `0.3.0` at a specific commit; a later software release does not make a paper v3.

## Order of operations, each gated on authorization

1. **Review** `research/v2` (12 commits + Step 09 docs). One person makes the GO decision per `docs/v2/gates/step-09.md`.
2. **Integrate**: open a PR from `research/v2` to `mainline` using `templates/PR_TEMPLATE.md` fields (purpose, scope, contract, gate evidence, reproduction, limitations). Let CI run on Python 3.10 and 3.13. Address review. Merge. *Requires authorization to push.*
3. **Release 0.3.0** on `mainline`: bump `pyproject.toml`, `src/exe_auth_ctrl_loop/__init__.py`, `CITATION.cff` `version`; move the `[Unreleased]` changelog section to `[0.3.0] - <date>`; tag `v0.3.0`; GitHub release with the release notes below. *Requires authorization to tag and push.* Record the actual release SHA.
4. **Archive** to Zenodo (automatic on release if the integration is on, as for v0.2.0). Obtain the actual version DOI. Add it to `CITATION.cff` `identifiers` in a follow-up commit, as was done for v0.2.0. *Requires authorization.*
5. **Regenerate `results/`** at the release SHA so `manifest.json` names it; verify the canonical hash is unchanged (it must be, since `src/` and `experiments/` do not change between `18dae89` and the release except for the version string, which is excluded metadata). Commit.
6. **Paper**: write prose from `manuscript-plan.md`; verify every citation against the source; cite software `0.3.0`, the release SHA, the version DOI, and the canonical hash. Submit under the venue's rules. *Separate authorization.*

## Release notes (draft for the GitHub release)

> **0.3.0** closes the three gaps reviewers found in the v0.2.0 design: a partition can now earn autonomy from an empty evidence store through shadow trials frozen before human review and labelled by an independent oracle; the evidence gate is an anytime-valid Beta-mixture confidence sequence instead of a fixed-sample Wilson bound read at every decision; and a token is refused at redemption when its partition has been suspended, its evidence invalidated, its policy changed, or its autonomy lost. Also new: an explicit four-state lifecycle with recorded transitions, host-owned risk envelopes and sequence budgets, decisions committed to the ledger before any token is issued, audited operations that execute unattended and are labelled afterwards, and a seeded, bit-reproducible simulation of fifteen scenarios including the ones where the design fails.
>
> **Behaviour changes.** `Policy.estimator` defaults to `"beta_mixture"` (`"wilson_legacy"` restores the 0.2.0 bound); a perfect record needs 60 trials, not 40, to clear 0.90. `Route.AUDIT` executes without a human. `AuthorizationToken` has a required `partition` field. `ToolRegistry` refuses a tool with its own `risk_class` when a `RiskPolicy` is attached. `ExecutionStatus.AWAITING_AUDIT` is removed. A `Policy` constructed without `estimator` therefore behaves differently from 0.2.0; treat the upgrade as a policy-version change.
>
> **Still a prototype.** Single process; lock-based token consume, in-memory evidence, ledger, budgets, and lifecycle; the ledger proves internal consistency only; the statistical guarantee holds for i.i.d. labels within an epoch and is not a defence against a strategic agent; the evaluation is simulated with an oracle that knows the truth. See `SECURITY.md`, `docs/v2/`, and `experiments/README.md`.

## Checklist (from `05_RELEASE_CHECKLIST.md`)

- [x] Preserve historical original code SHA and original software `v0.2.0` tag/release. (`v0.2.0` = `b266eb7` untouched; `docs/v2/baseline.md`.)
- [x] Confirm paper version naming independent of software semantic versioning. (Above.)
- [x] Tests, experiment reproductions, negative security tests, and hashes all pass. (Step 09 gate report.)
- [x] Reviewer crosswalk has a specific answer and limitations for each criticism. (`manuscript-plan.md`.)
- [x] Figure/table provenance references current tested code SHA and config IDs. (`claims-matrix.md`, `reproducibility.md`.)
- [x] No synthetic experiment is represented as field validation. (`experiments/README.md`, README, claims L5.)
- [ ] Relevant code licensed, acknowledgments and related work reviewed. (License unchanged, Apache-2.0; related work is an outline, to be written against sources.)
- [x] Public README explains differences from paper v1 and links source/artifacts. ("Versions and lineage" section.)
- [ ] No confidential/non-public material from `exe-auth-ctrl-loop-ip` is in public repo. (Nothing from `-ip` was read or copied during Steps 00–09; the owner should confirm before pushing.)
- [x] Release notes explicitly state prototype vs production limitations. (Above.)
- [ ] Tag/release pushed only after **explicit authorization**; obtain actual release SHA/version. (Not done.)
- [ ] Zenodo new version/archive created only after authorized release; use **actual** DOI. (Not done.)
- [ ] Paper deposited/submitted separately under target venue's rules. (Not done.)
- [x] Original paper and review record retained without revisionist rewriting. (Nothing about v1 was edited; the changelog and README describe v1 as what it was.)
