# v2 baseline (Step 00)

Read-only snapshot taken before any v2 work. No production code, tag, release, or DOI was changed.

## Anchors

| Item | Value |
|---|---|
| Default branch | `mainline` |
| Baseline HEAD (v2 starts here) | `86fcf83b5e387c9095c184eacdcb5b6b677d91f1` |
| Package version | `0.2.0` (`pyproject.toml`, `CITATION.cff`) |
| Tag `v0.1.0` | tag object `6ab4422...`, commit `f070e01953f4babfb117bb444e9e5e50cab02f8f` |
| Tag `v0.2.0` | tag object `389d17a4c43fbe9195f671673414e66e8ec9cd34`, commit `b266eb797656a84e7a21178cab4847b3aec9c3a2` (2026-08-20) |
| GitHub releases | `v0.2.0` (Latest, 2026-09-12), `v0.1.0` (2026-08-17) |
| Software DOI (all versions) | 10.5281/zenodo.21983061 |
| Software DOI v0.1.0 / v0.2.0 | 10.5281/zenodo.21983062 / 10.5281/zenodo.22730700 |
| Disclosure DOI | 10.5281/zenodo.21894658 |
| Defensive publication | https://www.tdcommons.org/dpubs_series/11356/ |

`mainline` HEAD is ahead of `v0.2.0` by documentation-only commits (README/CONTRIBUTING/CITATION edits, `examples/denials.py`, #6, #8). The released artifacts correspond to `b266eb7`, not HEAD.

## Component map

| Path | Role |
|---|---|
| `src/exe_auth_ctrl_loop/authority.py` (519 LOC) | `PartitionKey`, `Proposal`, `EvidenceSnapshot`, `EvidenceStore` (`put`, `get`, `adjudicate`), `wilson_lower_bound`, `Policy`, `Decision`, `AuthorizationToken`, `AuthorityController.evaluate`, `ExecutionGateway` (`issue`, `execute`, `revoke`) |
| `src/exe_auth_ctrl_loop/executor.py` (329) | `ClaudeExecutionAgent.run`: per-operation authority check immediately before each tool call |
| `src/exe_auth_ctrl_loop/ledger.py` (104) | In-memory hash-chained `EventLedger` (`append`, `verify`) |
| `src/exe_auth_ctrl_loop/providers.py`, `pipeline.py`, `tools.py`, `mcp.py` | Provider adapters, pipeline, tool registry, MCP extension |
| `tests/` | `test_authority.py` (108), `test_ledger.py` (16), `test_mcp.py` (277), `test_providers.py` (205) |
| `examples/` | `example.py`, `mcp_demo.py`, `denials.py` (offline); `live_example.py` (online, not run) |
| `.github/workflows/ci.yml` | Python 3.10 and 3.13; `ruff check .`, `mypy src`, `pytest -v`, `examples/example.py`, `examples/mcp_demo.py` |
| `CITATION.cff` | Software, release, and disclosure identifiers |

CI does not run `examples/denials.py`; the plan's global gate does.

## Reproduction

Run 2026-10-06, Python 3.14.4 (macOS, fresh venv). Python 3.10 and 3.13 were not run locally; CI covers them.

```sh
python -m pip install -e '.[dev]'
ruff check .            # All checks passed
mypy src                # no issues found in 8 source files
pytest -q               # 41 passed
python examples/example.py && python examples/denials.py && python examples/mcp_demo.py   # all exit 0
```

No failures.

## Documented gaps

1. **No earned qualification from an empty store.** `AuthorityController.evaluate` returns `NO_EXACT_EVIDENCE` when no snapshot exists. `EvidenceStore.adjudicate` raises `KeyError("partition not found")` for an absent partition, and it counts only autonomous outcomes. An autonomous outcome requires prior autonomy, which requires evidence. Nothing in the package creates a first snapshot or accrues one from `n=0`. `tests/test_authority.py` seeds snapshots directly via `store.put(EvidenceSnapshot(...))` (the `99/1` fixture), so no test shows a partition bootstrapping.
2. **Issued tokens do not fail on later suspension.** `ExecutionGateway` holds no reference to `EvidenceStore`. `execute()` checks only used/revoked, expiry, proposal digest, tool, and effects. Evidence id and version on the token are provenance, not live constraints. Nothing calls `revoke()` automatically; the token TTL bounds the window.

## Other known prototype limitations (observed in source)

- `execute()` sets `token.used = True` then calls the adapter; not atomic across concurrent redemptions (documented in a code comment).
- Tokens, evidence, and ledger are in-process and in-memory; `EventLedger.verify` detects in-chain tampering only, with no external anchor.
- `suspended` is never cleared by the package.
- The Wilson bound uses a fixed `z` at each decision; no sequential (optional-stopping) guarantee is claimed or tested.

## Integration branch

`research/v2` was created locally from `mainline` at `86fcf83`. Nothing was pushed.
