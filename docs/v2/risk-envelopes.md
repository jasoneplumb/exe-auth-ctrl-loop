# Risk envelopes and sequence budgets (Step 06)

Implements spec §8 in `src/exe_auth_ctrl_loop/risk.py`. Two gates now stand between a proposal and a handler, and both must open:

1. **Scope** (this step): is this operation, with these exact arguments and effects, inside a host-defined envelope, and does the sequence it belongs to stay inside its budgets? Answered by `RiskPolicy`. A no is `DENY`, which no human signature overrides.
2. **Evidence** (Steps 02–05): has this exact configuration, in the risk class the host derived, earned autonomy? Answered by the lifecycle and the sequential bound.

## Why envelopes rather than a bigger key

The partition key already carries `risk_class`, and evidence never pools across classes. What was missing was the host's own derivation of that class from the arguments. Putting every argument value into the key would fragment evidence into partitions that never reach `n_min`. An envelope instead names the *region* of argument space a class covers: all refunds up to $100 to an allow-listed payee are one class and share one track record; $100.01 is another class with its own record and its own threshold.

## Objects

- `RiskEnvelope(tool_name, risk_class, rank, allowed_effects, rules)`. Rules are `Bound(field, maximum, minimum)` for numbers (booleans are not numbers) and `OneOf(field, values)` for allow-lists such as recipients. For one tool, classification picks the lowest `rank` whose rules all admit the arguments. No admitting envelope means no class, and no class is denied, never defaulted.
- `SequenceBudget(budget_id, tool_names, scope, metric, limit, max_operations)`. `scope` is `global`, `partition`, or `parameter:<name>` (for example per payee). `metric` names a numeric argument to sum; `limit` caps the sum; `max_operations` caps the count. A budget with neither cap is rejected at construction.
- `RiskPolicy(version, envelopes, budgets, conflicts)`. `conflicts` is a set of `(earlier_tool, later_tool)` pairs that may not occur in that order within one partition. `update(...)` replaces the rules and keeps the charges.

## Where the checks run

`RiskPolicy` implements both guard protocols from `authority.py`:

- As a `ProposalGuard` on `AuthorityController`, `proposal_blockers()` runs at decision time. Any reason routes to `DENY` and is recorded in the decision's reason codes: `NO_RISK_ENVELOPE`, `RISK_CLASS_MISMATCH`, `EFFECT_OUTSIDE_ENVELOPE`, `BUDGET_EXCEEDED:<id>`, `OPERATION_LIMIT:<id>`, `CONFLICTING_ACTION:<tool>`.
- As a `RedemptionGuard` on `ExecutionGateway`, `redemption_blockers()` runs the same checks again under the consume lock, against the policy that is current *then* and the charges committed *since*. `commit()` charges the budgets after the token is marked used and no guard objected. A denied redemption charges nothing.

`ToolRegistry.classify_risk(name, parameters)` is the single place a class is derived. A registry constructed with `ToolRegistry(risk=policy)` delegates every tool to that policy's envelopes, and tools must register with `risk_class=None`; a registry without a policy uses each tool's own `risk_class`. Holding both is refused at `register()`, so there is never a second answer. `providers.py` builds the partition key from it, and `ClaudeExecutionAgent` re-derives and compares before each call, refusing a request whose proposal names a different class than the registry derives for the same arguments. Arguments no envelope admits raise `ToolValidationError`, which the proposer turns into `ProposalGenerationError` and the executor into a blocked step.

## What is derived from what

| Input | Used for classification | Used for budgets |
|---|---|---|
| Tool name (matched to the registry) | yes | yes |
| Argument values | yes | yes (the metric, the scope parameter) |
| Effects requested (must equal the registry's) | checked against the envelope | no |
| Model confidence, `confidence_bin` | **no** | **no** |
| The proposal's own `risk_class` label | **no**; compared to the derived class and denied on mismatch | no |
| Proposal text (`intent`, `assumptions`, ...) | no | no |

## Budget semantics and the in-process guarantee

- A decision *projects* a charge against what has been committed; it does not reserve. Several decisions may each look affordable.
- A redemption re-projects under the gateway lock and, if it fits, consumes the token and commits the charge in the same critical section.
- Therefore, for one `RiskPolicy` instance attached to one gateway in one process: the sum of committed charges in a scope never exceeds `limit`, and the committed count never exceeds `max_operations`. `test_budget_is_charged_at_redemption_not_decision` shows three affordable decisions of which only two redeem.
- Charges are in this object's memory. They do not survive a restart and are not shared across processes; the same accounting must live in a durable store in production. Budgets have no time window in this prototype; resetting them is a policy `update()` with fresh `SequenceBudget` objects under a new `budget_id`.

## Interaction with the Step 05 contract

`RiskPolicy.version` is not in the partition key and is not on the token. It does not need to be: the redemption guard re-derives everything from the current policy, so a rollback to a stricter envelope, a tightened budget, or a removed payee denies an outstanding token with the reason named (`test_rollback_to_stricter_envelope_denies_outstanding_token`, `test_tightened_budget_denies_outstanding_token`). Loosening a policy between issue and redemption does not widen an issued token, because the token's digest fixes the arguments and effects.

## Interaction with evidence policy

- Evidence earned in the `low` partition authorizes nothing in the `high` partition: the high partition starts `UNESTABLISHED` and must qualify on its own (`test_low_risk_autonomy_does_not_extend_to_the_high_envelope`).
- A proposal that labels a high-envelope operation `low` is denied as mislabeled before evidence is consulted; it does not become a human-approval request, because a human cannot change what the operation is.
- Within one class, the envelope is what makes the evidence meaningful: the track record for "refunds up to $100 to known payees" is a record of exactly that, and the envelope keeps the next operation inside the region the record describes.

## Limits

- No time windows on budgets; no cross-run persistence; no cross-process sharing.
- Envelope rules are numeric bounds and allow-lists. Richer predicates (for example, rate per recipient per hour, or relationships between arguments) are not expressed.
- `conflicts` is ordered-pair-within-partition only.
- The same `RiskPolicy` object must be the one attached to the registry, the controller, and the gateway. Nothing checks that three different instances were not supplied; a deployment constructs one and passes it to all three.
