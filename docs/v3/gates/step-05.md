# Gate report — Step 05

**Status:** completed 2026-11-02. Criteria were pre-registered at Step 00 and are reported unchanged.

**Step objective:** Principals, grants, lineage (serves F5, closes L9; see `docs/v3/plan.md` §4).

**Branch / SHA:** `feat/v3-step-05-principals` from `mainline` @ `6d2721f`; Step 05 commit follows this report. Issue #32.

**Reviewer:** (pending, one person decides)

**Date / environment:** 2026-11-02, macOS (Darwin 25.6.0), Python 3.14.8, SQLite 3.54.1

**Owner decision taken at the start of this step (2026-11-02):** an `Approval` is a HUMAN principal's MAC over the **proposal digest** (with partition, approval id, and expiry) and is **single use**: one token per approval, enforced by a unique `approval_id` on tokens. Spec §8 said "decision id"; a human approves before the run that makes the decision, so that id does not yet exist. Spec §8 amended. Rejected: decision-id binding with pause-and-resume; deferring the choice.

| Acceptance criterion | PASS / FAIL / NOT RUN / UNKNOWN | Evidence |
|---|---|---|
| 1. Ledger refuses an unregistered `actor` | PASS | `EventLedger.append` checks `principals` in the append transaction. `test_principals.py::LedgerActorTests`: unregistered refused; a deactivated actor refused after deactivation; the host's SERVICE identities exist by schema |
| 2. `Approval` replaces `human_approved`; a forged or wrong-principal MAC is refused at `issue()`; `release()` requires a verified `HUMAN` principal (closes L9) | PASS | `issue(..., approval=)`; `human_approved` is now derived (`approval is not None`). `ApprovalTests`: valid approval issues once and records `approval.recorded`; forged MAC, another human's name on alice's MAC, a model, an oracle, and an unregistered name all refused; approvals do not transfer to another proposal or partition; expired, rescinded, and reused approvals refused; a rescission after issue refuses redemption. `ReleaseTests`: a signed human release works; unsigned, wrongly signed, model, oracle, and unknown releases refused; a signed release cannot be replayed at a later epoch; a manager without `principals` cannot release |
| 3. A grant broader than its parent, or outliving it, is refused at creation | PASS | `GrantTests`: narrower-and-sooner delegation accepted; broader by unrestricted tools, an extra tool, an extra effect, a dropped ceiling, or a higher ceiling refused; outliving the parent refused; root grants need a signing human (a model, or a signature over different terms, refused) |
| 4. `lineage(token_id)` returns the chain to the root grant for every token | PASS | `LineageTests`: tokens issued at depths 1, 2, and 3 return `[root]`, `[mid, root]`, `[leaf, mid, root]`; the last has no parent; the requesting principal is the leaf grant's grantee; every token in the table records a grant |
| 5. Every permission change emits exactly one of the six event types; a test enumerates the public API and asserts no mutating path skips the ledger | PASS | `PermissionEventTests`: the public methods of `Principals` (found by `inspect`) must equal the classified set of 8 queries and 7 mutations, so a new method fails the test until classified; each mutation writes exactly one event of its type, and the 7 cover all 6 types; a refused change writes nothing; raw SQL that changes a principal's kind or re-activates it, or edits or deletes a grant, is refused by the database |
| 6. Global gate passes | PASS | below |
| Global quality gate: `ruff check .`, `mypy src`, `pytest -q`, three offline examples | PASS (local, Python 3.14.8 only) | ruff clean; mypy 0 issues in 14 files; 310 passed, 1 skipped (Step 06 forward check), 85 subtests; three offline examples exit 0 and `denials.py` still matches the README; `live_example.py` parses. Full experiment reproduction: canonical hash `2e9dc58974affce7aca80d2e93322d18defb94956d98682d5f62212881ccbf1a`, identical to `results/manifest.json`, with the harness registering principals and a root grant, issuing under it, and signing its releases |
| Mutation check (v3 plan §5 rule) | PASS | Each fails `test_principals.py`: ledger actor check removed (2 failures), delegation narrowing skipped (5), approval signature check skipped (6), release signature check skipped (8) |

**Design and statistical assumptions:**
- **Keys in a host `Keyring`, not the database.** The agent side reaches the Store through `ToolCatalog → RiskPolicy`; spec §1 forbids it reaching a signing key. Keys are HMAC secrets supplied at start-up (like the MCP secret); `Principals` holds the keyring, and the gateway and lifecycle reach it only through `Principals`. Stated in spec §8.
- **Root of trust.** The host's own SERVICE identities (`controller`, `execution-gateway`, `claude-execution-loop`) are created by schema v4, because registering a principal is itself a ledger event whose actor must already exist. Every other principal is registered with `principal.registered`.
- **Grant scope.** Tools, effects, risk classes, task categories, and per-operation argument ceilings (spec's "budget ceilings", stated in §8 as ceilings on named numeric arguments). Cumulative budgets stay with `RiskPolicy`; a grant-level running total would duplicate its accounting.
- **Who may do what.** Register and deactivate: an active SERVICE or HUMAN principal. Root grant: an active HUMAN, with a signature over the exact terms. Delegate: the holder of a live parent, attenuation only. Revoke: any grantor at or above the grant. Rescind: the approving human, signed.
- **Redemption re-checks authority.** A token whose grant chain has since been revoked, expired, or lost an active party, or whose approval was rescinded, is refused at redemption and left `issued`. Revoking such tokens at the moment of the change (RV-11, RV-12, RV-13) is Step 06.
- **Release payload includes the epoch**, so a signed release cannot be replayed at a later suspension.

**Limitations / deviations:**
- **Spec §8 amended** by the owner decision above (proposal digest + single use, not decision id).
- **One harness assertion basis changed.** Registering test principals writes ledger events during `test_audit_commitment.Harness` setup, so two assertions about what a run records compare from a post-setup baseline (`h.run_events()`); their expected values are unchanged.
- **Test mechanics.** `tests/_support.py` gains `principals_for(store)` (idempotent registration of the test humans, oracles, models, and a root grant `g-root` from alice to agent), `gateway_store(store)` (store and principals guaranteed to share one Store), `approve(proposal)`, `release(manager, ...)`, and `AGENT`. The ChainLimit tests append as `controller` instead of the unregistered `"x"`.
- Breaking API: `ExecutionGateway(..., principals=)` required; `issue(..., *, principal_id, grant_id, approval=None)` replaces `human_approved=`; `ClaudeExecutionAgent(..., principal_id=, grant_id=)` required; `run(approvals=)` and `CrossModelAuthorityLoop.execute(approvals=)` replace `approved_proposal_ids`; `LifecycleManager.release(key, actor, reason, mac)`; `LifecycleManager(..., principals=)`.
- Files touched: 9 source files (new `principals.py`), 15 test files (new `test_principals.py`), four examples, `experiments/simulate.py`, README, SECURITY.md, spec §8, this report. Over the 5-file guideline for the reason of Steps 02–04.
- Python 3.10/3.13 not run locally; CI covers them.

**Unexpected behavior / open risks:**
- pytest collected an imported helper named `test_principals` as a test; renamed `principals_for` before commit.

**Decision: GO** (recommended; reviewer to confirm)

**Next permitted step (GO only):** Step 06, synchronous invalidation, only on explicit request.
