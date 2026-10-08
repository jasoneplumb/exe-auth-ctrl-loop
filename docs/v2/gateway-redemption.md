# Gateway redemption contract (Step 05)

Implements spec §7's "live invalidation at redemption" in `ExecutionGateway` (`authority.py`), with `LifecycleManager` as a `RedemptionGuard`. Closes baseline gap 2: an issued token no longer outlives the justification it was issued on.

## Wiring

```python
gateway = ExecutionGateway(
    clock,
    evidence=store,                       # live EvidenceStore
    current_policy=lambda: manager.policy,  # whatever policy is current now
    guards=(manager,),                    # any RedemptionGuard, e.g. LifecycleManager
)
```

All three are optional. A gateway constructed with none of them behaves as v0.2.0 did (expiry, scope, and explicit revocation only), which keeps the existing examples and the MCP demo valid. `AuthorizationToken` now carries `partition` so the live checks know what to look up.

## Linearization point

`execute()` takes a single `threading.Lock`, performs every check, and sets `token.used = True` while still holding it. That assignment is the linearization point: a redemption is either before it (and sees `used=False`, then wins the lock and consumes) or after it (and is refused). `revoke()` takes the same lock, so a revoke and a redeem are ordered, never interleaved. The handler is invoked after the lock is released, so a slow or blocking handler does not serialize unrelated tokens, and a second redeemer of the same token is refused while the first handler is still running (`test_token_is_consumed_before_the_handler_runs`). A handler that raises does not refund the token.

**Scope of the claim.** Correct for threads within one Python process. It says nothing across processes, hosts, or a restart, where tokens must be consumed transactionally in a durable store before the handler is reached. SECURITY.md states the same.

## Checks at redemption, in order

| # | Check | Denial |
|---|---|---|
| 1 | Token exists, not used, not revoked | `missing, consumed, or revoked token` |
| 2 | `clock() < expires_at` (denied *at* `expires_at`) | `expired token` |
| 3 | Proposal digest, tool name, effect subset | unchanged from v1 |
| 4 | `current_policy().version == token.policy_version` | `POLICY_CHANGED` |
| 5 | `requested_effects ∩ current_policy().prohibited_effects = ∅` | `PROHIBITED_EFFECT` |
| 6 | Partition snapshot not suspended and valid, unless the approval was informed (below) | `PARTITION_SUSPENDED` / `EVIDENCE_INVALID` |
| 7 | For evidence-based (non-human) tokens: snapshot exists and `evidence_id` matches the token's | `EVIDENCE_WITHDRAWN` |
| 8 | Each guard's `redemption_blockers(token)` is empty | guard reasons, e.g. `LIFECYCLE_QUALIFYING`, `PARTITION_RETIRED`, `LINEAGE_SUSPENDED` |

Checks 4–8 are collected, not short-circuited, so the `PermissionError` names every reason (`authorization withdrawn: POLICY_CHANGED, EVIDENCE_INVALID`).

## What does and does not invalidate a token

**Invalidates:** partition suspension (by `EvidenceStore.adjudicate` with a severe outcome, or `LifecycleManager.record_severe`); `EvidenceStore.invalidate`; the evidence record being replaced with a different `evidence_id` or removed; a policy version change; an effect becoming prohibited; lifecycle leaving `AUTONOMOUS` for an evidence-based token; partition retirement or lineage suspension for any token; explicit `revoke`.

**Does not invalidate (stated deliberately):** an ordinary evidence increment, meaning a new label folded into the same evidence record (same `evidence_id`, higher `version`) while the partition stays valid and unsuspended. The token was issued for one already-decided operation; the next operation gets a fresh decision on the new counts. If the increment drops the bound below policy, the lifecycle notices at the next `evaluate()` (`GATE_LOST_OUTCOME`) and from then on the guard refuses evidence-based tokens; `test_gate_lost_before_redemption_denies` and `test_ordinary_increment_without_reevaluation_keeps_token_good` pin both sides of this boundary. There is no separate epoch counter; the snapshot's flags, `evidence_id`, and the lifecycle record are the authority epoch.

## Human-approved tokens

A human signature overrides a *route* (`HUMAN_APPROVAL`, `AUDIT`), never a *prohibition*:

- `issue()` refuses a human-approved token for any prohibited effect, whatever the decision's route says, and refuses a policy object that is not the current one when a provider is attached.
- At redemption a human-approved token is subject to checks 4, 5, and 8 like any other, and to check 6 unless the approval was **informed**: the decision it was issued from saw the same snapshot version that now carries the suspension or invalidation. A human who approves an operation on a partition they can see is suspended is exercising the `SUSPENDED → HUMAN_APPROVAL` route the spec allows; a human who approved before the suspension did not consent to the new state, and that token is refused.
- Check 7 does not apply (a human approval needs no evidence record). Stated consequence: a human-approved token survives the replacement of the evidence record it was approved against by a *healthy* successor record, because the approval is consent to one operation, not reliance on the record. Degradation of that record (suspension, invalidation) still refuses it unless the approval was informed. This is the one deviation from README invariant 3 and is listed there. The lifecycle guard lets a human token through `UNESTABLISHED` and `QUALIFYING` but not retirement, lineage suspension, or suspension.
- `revoke(token_id, reason)` records the reason on the token.

## Limits

- In-process lock only; no durable or distributed atomicity (see above).
- Denials are raised, not ledgered, by the gateway itself; the pipeline records them. Step 07 revisits what the ledger must hold.
- Guards are called inside the lock and must be read-only and fast. `LifecycleManager.redemption_blockers` does not call `refresh()`: lifecycle state at redemption is exactly as fresh as the last `evaluate()`. A bound that fell or evidence that went stale between issuance and redemption, with no `evaluate()` in between, is not caught by the guard; the gateway's own checks still catch suspension, declared invalidation, and evidence replacement, and the TTL bounds the rest.
- `commit()` on a guard must not raise; it runs after the token is consumed. Anything that could refuse the operation belongs in `redemption_blockers()`.
- When a snapshot is both suspended and invalid, the denial names both `PARTITION_SUSPENDED` and `EVIDENCE_INVALID`.
- A handler that raises after the token is consumed surfaces as an `ExecutionStep` with `failed=True` and run status `FAILED`; the proposal is marked executed for the run so it cannot be retried on a spent token.
- Risk envelopes and sequence budgets (amount, recipient, ordering) are Step 06; this step covers only identity, scope, and justification.
