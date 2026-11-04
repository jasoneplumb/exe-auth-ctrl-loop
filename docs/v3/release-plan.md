# Release plan (proposed 0.4.0) and checklist status

Written at v3 Step 10 (issue #46). Nothing here has been done: every step that tags, pushes a release, publishes, or deposits needs the owner's separate authorization, and this plan is not one.

## Identifiers

| Item | Value | Status |
|---|---|---|
| Software version | `0.4.0` (plan §6; breaking release) | proposed. `pyproject.toml`, `src/exe_auth_ctrl_loop/__init__.py` `__version__`, and `CITATION.cff` `version` still say `0.3.1`; they are bumped in the release commit |
| Software tag / GitHub release | `v0.4.0` | not created |
| Zenodo version DOI for 0.4.0 | — | minted only on an authorized release; added to `CITATION.cff` `identifiers` afterwards with the actual value |
| Concept DOI | 10.5281/zenodo.21983061 | unchanged; resolves to the latest version |
| Release-candidate commit | the squash merge of the Step 10 PR on `mainline` | recorded in `docs/v3/gates/step-10.md` once merged |
| Artifacts | `results-v3/` (scenarios, statistics, agreement), hashes in `docs/v3/gates/step-10.md` | three independent runs at Step 10 |
| `results/` | the v0.3.0 artifacts cited by `docs/paper-v2/claims-matrix.md` | unchanged (owner decision 2026-11-04) |
| Paper v2 | draft on v0.3.0 artifacts | unchanged; it cites 0.3.0, and `docs/v3/claims-delta.md` restates each claim for 0.4.0. A paper on 0.4.0 is a separate decision |

## Order of operations, each gated on authorization

1. **Review and merge** the Step 10 PR (claims delta, docs, `results-v3/` at the release-candidate commit). One person makes the GO decision per `docs/v3/gates/step-10.md`.
2. **Release commit** on `mainline`. *Requires authorization to push.*
   - Bump `pyproject.toml`, `__init__.__version__`, and `CITATION.cff` (`version`, `date-released`) to `0.4.0`.
   - Rename `[Unreleased] - proposed 0.4.0` in `CHANGELOG.md` to `[0.4.0] - <date>`.
   - Rewrite the README's "On `mainline` (v3 Step NN, unreleased)" and "In the released 0.3.1" pairs as "Since 0.4.0" and "In 0.3.1", then rerun `tests/test_claims_matrix.py` and `tests/test_claims_delta.py`.

   The version string is metadata excluded from the canonical hashes, so `results-v3/` need not be regenerated for it.
3. **Verify**: CI green on Python 3.10 and 3.13; the global gate locally; one full `results-v3/` run at the release commit reproduces the Step 10 hashes.
4. **Tag and release** `v0.4.0` with the notes below. *Requires authorization to tag and to publish the release.* Record the actual release SHA.
5. **Archive** to Zenodo, which is automatic on a GitHub release, as for 0.2.0 and 0.3.x. Obtain the actual version DOI and add it to `CITATION.cff` `identifiers` in a follow-up commit. *Requires authorization.*
6. **Engineering vault**: refresh the generated project view after the release (`/project-state refresh`). It is pushed directly, with no PR.

## Release notes (draft for the GitHub release)

> **0.4.0 is a breaking release.** It answers the six review findings on 0.3.1 (`docs/v3/plan.md`).
>
> - **Durable state.** Authority state is durable in one SQLite file, and a token is spent at most once across threads, processes on one host, and restarts.
> - **Closed execution boundary.** Handlers are reachable only through the gateway, which runs the handler its token names, and only for a decision committed to the ledger.
> - **Synchronous revocation.** Thirteen kinds of event revoke the tokens in their scope in their own transaction, and redemption recomputes the gate.
> - **MCP server-side redemption.** An MCP block names its one server and is spent once, at the host.
> - **Trustworthy evidence admission.** Trial provenance is derived from the committed decision, and labels are signed by the oracle bound to the epoch. The deadline runs on the host's clock, a reviewer cannot also label, and the ledger is HMAC-keyed.
> - **Observable delegation.** Approvals, releases, and delegation are signed by registered principals, and every token is traceable to a human's root grant.
> - **Statistically safer defaults.** The default `Policy` counts pending trials as failures and spends one α across every partition and epoch. Delayed or withheld labels can no longer raise the bound, and no lifetime or family-wise budget resets. Both were measured at 1000 sequences per cell.
>
> **Upgrading.** Nearly every constructor changed; see `CHANGELOG.md` "Breaking" and `docs/v3/plan.md` §6. 0.3.1 state was in memory and is not migrated. A `Policy` built as in 0.3.1 now behaves differently: qualification needs more evidence (63 perfect trials for the first partition, against 53), later partitions and epochs need more, and slow labels delay qualification. The 0.3.1 rules remain selectable with `pending="deadline"`, `partition_alpha="per_partition"`, `epoch_alpha="per_epoch"`.
>
> **Still a prototype.**
> - **One process, one host.** The gateway shares a Python process with its caller, so interpreter reflection can reach any object. State does not cross hosts.
> - **Keys.** The ledger key and the principals' HMAC keys are held by the host. One shared MCP secret signs for every server.
> - **Undetected rewrites.** Ledger tail truncation is not detected without an external anchor.
> - **Statistical scope.** The guarantee needs labels i.i.d. in freeze order, and correlated failures are outside it.
> - **Simulation.** The evaluation uses an oracle that knows the truth.
>
> See `SECURITY.md`, `docs/v3/threat-model.md`, `docs/v3/claims-delta.md`, and `experiments/README.md`.

## Checklist

- [x] Historical tags and releases preserved (`v0.1.0`, `v0.2.0`, `v0.3.0`, `v0.3.1` untouched).
- [x] Paper versioning independent of software versioning: paper v2 stays on 0.3.0, and `results/` stays its artifacts.
- [x] Tests, negative security tests, mutation checks, and experiment reproductions pass (gate reports Steps 02–10, issues #41 and #43).
- [x] Every claim of the paper-v2 matrix restated for 0.4.0, with its test and data (`docs/v3/claims-delta.md`, checked by `tests/test_claims_delta.py`).
- [x] Breaking changes verified against the final API (`docs/v3/plan.md` §6).
- [x] No synthetic experiment represented as field validation (`experiments/README.md`, README, claims L5).
- [x] Release notes state prototype against production limits (above).
- [ ] No confidential or non-public material from `exe-auth-ctrl-loop-ip` is in the public repository. Nothing from it was read or copied during v3; the owner should confirm before the release.
- [ ] Release commit, tag, and GitHub release pushed only after **explicit authorization**; record the actual release SHA.
- [ ] Zenodo version created only after the authorized release; use the **actual** DOI.
- [ ] README rewritten from "On `mainline` (unreleased)" to "Since 0.4.0" in the release commit.
