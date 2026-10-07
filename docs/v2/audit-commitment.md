# Audit commitment and ledger claims (Step 07)

Three things are easy to conflate and are kept apart here:

1. **Selection randomness.** Which operations get audited is decided by a draw from the controller's RNG, compared against the policy's audit rate.
2. **Event ordering.** The draw is realized and committed to the ledger before any token is issued or handler reached.
3. **Resistance to record rewriting.** What the hash chain can and cannot prove about the record afterwards.

The pre-outcome audit-selection claim rests on (1) and (2) together with a trust assumption. It does not rest on (3), and (3) is weaker than it looks.

## Event-order contract

For every operation the executor handles:

```
evaluate()            controller computes the decision; if the route is eligible, the
                      audit draw is realized now and stored on the Decision
commit(decision)      ledger.commit_decision appends "authority.decision.committed"
                      with route, reason codes, evidence id/version, audit_probability,
                      audit_draw, and the proposal digest
issue()               gateway mints the token (or refuses)
execute()             gateway consumes the token and calls the handler
```

Guarantees, in the wired path (`CrossModelAuthorityLoop`):

- The committed record's ledger sequence is strictly less than that of any event describing the outcome, and the handler observes the committed record already present (`test_decision_and_draw_are_committed_before_the_handler_runs`).
- If `commit` raises, the executor returns a `DENY` step with `decision not committed to ledger: ...`, no token is issued, and no handler runs (`test_commit_failure_refuses_the_step_and_issues_no_token`). An effect off the record is impossible in this path.
- `CrossModelAuthorityLoop.__post_init__` wires the executor's `commit` to its ledger. An executor used without the loop and without a `commit` hook records nothing; that is permitted for offline demos and unit tests and is exactly what the loop exists to prevent in a real run.

The loop still appends `execution.step.recorded` after each step (receipt, error, executed flag). That is the outcome record; the decision record precedes it.

## What "audit selection is independent of results" means here

Given:

- **R1** the RNG is trusted: `random.SystemRandom` by default, or a seeded `random.Random` in tests and experiments (deterministic, reproducible, and labelled so);
- **R2** the controller is trusted to commit the draw it realized, before acting;
- **R3** the models cannot observe RNG state or influence the proposal stream in response to it (threat model TM-10),

the audit draw is a function of nothing the outcome could influence, because it exists on the record before the outcome does. That is an *ordering* argument. It is not a proof that a malicious controller could not draw, look at the outcome, and record a different draw: such a controller is outside R2, and the hash chain does not catch it (next section). Earlier wording in the code ("provably independent") was replaced for that reason.

Seeded RNGs replay the same draw sequence (`test_seeded_rng_replays_the_same_committed_draw`), which is what makes experiments reproducible; it also means a seed must never be derivable by a model.

## What the hash chain proves

`EventLedger.verify()` recomputes each event's hash over its body and the previous hash.

| Attack | Detected by `verify()` | Detected with an external anchor |
|---|---|---|
| Edit a retained event (payload, actor, time, order) | yes | yes |
| Delete an interior event | yes (the next event's `previous_hash` no longer matches) | yes |
| Truncate the tail | **no** | yes, if the anchor is at or past the cut |
| Replace the whole chain with a freshly built one | **no** | yes |
| Append fabricated events at the tail | no (they chain correctly) | only after the next anchor |

The chain is in memory, owned by the same process as the controller. Anyone who can call `append` can rebuild the list. So `verify()` is a check against accidental corruption and against a party who can edit entries but not rebuild from genesis; it is not a defence against the host.

## Anchoring: a demonstration, not a guarantee

`anchor()` returns `(sequence, event_hash)` of the current head. `verify_anchor(sequence, event_hash)` checks the chain still verifies and still contains that head. If the anchor is held somewhere the controller cannot write (another service, a signed log, a notary, a human's notebook), truncation below it and rewriting at or before it become detectable (`ChainLimitTests`).

Inside this process the anchor proves nothing, because the same party holds both. The methods exist so the contract is concrete and tested, and so the production requirement in README ("anchor the ledger head somewhere the controller cannot write") has an interface to attach to. They are not part of the prototype's security claim.

What external anchoring would require in production: a write-once store outside the controller's identity; an anchoring cadence (every event, or every N, which bounds how much tail can be lost undetected); a verifier that holds the anchors and is run by someone other than the controller's operator; and a policy for what happens when verification fails.

## Open item carried forward (O-2)

v1 routes `AUDIT` to a pre-execution human review (`AWAITING_AUDIT`). The spec (§3) defines `AUDIT` evidence as the independent label of a frozen proposal, which would let an audited operation execute autonomously and be labelled afterwards. This step keeps v1 behaviour: an audit still requires a human before execution, and its decision is committed first like any other. Changing `AUDIT` to execute-then-label alters what runs unattended and is a decision for the owner, not something to slip into an ordering fix. See the gate report.
