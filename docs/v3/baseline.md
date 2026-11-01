# v3 baseline (Step 00)

Read-only snapshot taken before any v3 work. No code, tag, release, or DOI was changed.

## Anchors

| Item | Value |
|---|---|
| Default branch | `mainline` |
| Baseline HEAD (v3 starts here) | `c58ce7db2ae48920cb1f180f36ceeef660d1955f` |
| Package version | `0.3.1` (`pyproject.toml`, `__init__.__version__`, `CITATION.cff`) |
| Tag `v0.3.0` | commit `78bdf9d982134a115fbeaaffd53213d553f4ddf3` |
| Tag `v0.3.1` | commit `3032a114f303f58476f5bc62eba23aa2206a6bae` |
| Software DOI (all versions) | 10.5281/zenodo.21983061 |
| Software DOI v0.3.0 / v0.3.1 | 10.5281/zenodo.23226913 / 10.5281/zenodo.23705921 |
| Disclosure DOI | 10.5281/zenodo.21894658 |
| Integration branch | `docs/v3-plan` (local; nothing pushed) |

`mainline` HEAD is ahead of `v0.3.1` by one documentation commit (`c58ce7d`, CITATION.cff DOI).

## Source of the v3 work

Six review findings on v0.3.1, received 2026-11-01, numbered here F1–F6 in the order received. Their text is reproduced in `plan.md` §1. Owner's posture decision for v3 (2026-11-01): **durable, same process**: in-memory state is replaced by transactional storage in one process; separating the controller and gateway into their own process remains a stated production requirement (`spec.md` O-1).

## Component map at v0.3.1

| Path | LOC | Role relevant to F1–F6 |
|---|---|---|
| `src/exe_auth_ctrl_loop/authority.py` | 700 | `EvidenceStore` (dict), `AuthorityController.evaluate`, `ExecutionGateway` (`tokens` dict under `threading.Lock`; `execute(token_id, proposal, adapter)` runs whatever `adapter` the caller passes) |
| `src/exe_auth_ctrl_loop/executor.py` | 396 | `ClaudeExecutionAgent` holds the `ToolRegistry` and passes `lambda p: self.registry.execute(...)` to the gateway (`executor.py:313`) |
| `src/exe_auth_ctrl_loop/tools.py` | 193 | `ToolRegistry.execute` is public; the registry is both catalog and handler table |
| `src/exe_auth_ctrl_loop/mcp.py` | 297 | Stateless HMAC binding (`build_call_meta`, `verify_call_meta`); no redemption, nonce, or audience |
| `src/exe_auth_ctrl_loop/shadow.py` | 437 | Frozen trials and oracle labels; `provenance` and `labeled_at` are caller-supplied; `Oracle` is an unauthenticated dataclass |
| `src/exe_auth_ctrl_loop/lifecycle.py` | 427 | Four-state lifecycle; `redemption_blockers` reads state as of the last `evaluate()` and does not refresh (`lifecycle.py:266`) |
| `src/exe_auth_ctrl_loop/ledger.py` | 160 | In-memory, unkeyed SHA-256 chain; free-text `actor`; `anchor()` is demonstration only |
| `src/exe_auth_ctrl_loop/pipeline.py` | 126 | Freezes `AUDIT` trials only (`pipeline.py:56`); never creates `SHADOW` trials |
| `src/exe_auth_ctrl_loop/risk.py` | 304 | Envelopes and sequence budgets, in memory |
| `src/exe_auth_ctrl_loop/sequential.py` | 119 | Beta-mixture confidence sequence |
| `experiments/simulate.py` | 544 | Harness; differs from the shipped loop in four ways (claims matrix L6) |

## Where each finding lands

| Finding | Code at baseline | Existing claim rows (`docs/paper-v2/claims-matrix.md`) |
|---|---|---|
| F1 Close the execution boundary | `ExecutionGateway.execute` takes any adapter; `ToolRegistry.execute` public and held by the agent; `issue()` accepts any decision object; consume is a thread lock on a dict | C1, C15 (concurrency tests pass with the lock replaced by a no-op), L3, L7; TM-8 |
| F2 Unify authorization across MCP | `verify_call_meta` is stateless; a block re-verifies until expiry, after the token is consumed or revoked | L3 (last sentence); `mcp.py` module `Future:` |
| F3 Trustworthy evidence admission | `freeze(provenance=...)` trusts the caller; `adjudicate(labeled_at=...)` trusts the labeller; oracle unauthenticated and not bound to the partition; ledger re-hash undetected; no shipped `SHADOW` creation | C4, C5, C6, C22, L1, L6, L8 |
| F4 Immediate lifecycle invalidation | Loss of autonomy reaches the gateway only after a later `evaluate()`; the gateway never reads counts | C13, C14 |
| F5 Delegation observability | No principal type; `human_approved` is a boolean; `release(actor=...)` accepts any string; no grant lineage or permission-change events | L9 (no row for delegation; new) |
| F6 Statistical evaluation | Label-latency, censoring, correlation, and repeated-epoch behaviour are outside the proposition; α resets per epoch and per successor; K is not recorded | C12, C29, L2, L11, L13 |

## Reproduction

Run 2026-11-01, Python 3.14.8 (macOS, existing `.venv`). Python 3.10 and 3.13 not run locally; CI covers them.

```sh
ruff check .            # All checks passed!
mypy src                # Success: no issues found in 12 source files
pytest -q               # 212 passed, 15 subtests passed
python examples/example.py && python examples/denials.py && python examples/mcp_demo.py   # all exit 0
```

## Observed discrepancies (not fixed here)

- README "Results" table says "203 tests pass offline"; the suite has 212 at baseline. To be corrected in Step 10 with the final count.
