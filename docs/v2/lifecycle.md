# Authority lifecycle (Step 03)

Implements spec §9 in `src/exe_auth_ctrl_loop/lifecycle.py` over the evidence from `shadow.py`. `AuthorityController` and `ExecutionGateway` are unchanged; `LifecycleManager` wraps the controller.

## Usage

`LifecycleManager(shadow, store, policy, controller, clock)`:

- `evaluate(proposal)` refreshes the partition, calls the controller, and then enforces the lifecycle. A decision that reaches `AUTONOMOUS` or `AUDIT` while the partition is not `AUTONOMOUS`, or is retired or lineage-suspended, is downgraded to `HUMAN_APPROVAL` with `LIFECYCLE_<STATE>` and blocker reason codes. Hand-seeding the store therefore cannot grant autonomy through the manager.
- `refresh(key)` publishes admissible shadow evidence to the store, walks the legal events, and records each transition.
- `record_severe(key, provenance, reason)` suspends immediately from any state and any provenance, including `HUMAN_APPROVED`.
- `release(key, actor, reason)` is the restoration process: a named human and a stated reason are required, the partition goes `SUSPENDED` to `QUALIFYING`, the epoch increments, and counting restarts from zero. Trials frozen before the release no longer count.
- `on_churn(old, new)` retires `old` and starts `new` at `UNESTABLISHED` with no carried counts. If `old` was suspended, `new` is `lineage_suspended`; only `release(new, ...)` clears that.

## Gate (`QUALIFYING` to `AUTONOMOUS`)

All of: `n >= n_min`; `Policy.lower_bound` at least the policy's bound for the risk class (the Beta-mixture confidence sequence by default since Step 04, see `statistical-method.md`; this step shipped with the v1 Wilson bound, now selected only by `estimator = "wilson_legacy"`); evidence not older than `max_evidence_age`, measured from the last label or censor time; policy version matches; not lineage-suspended; not retired. An `AUTONOMOUS` partition falls back to `QUALIFYING` on stale evidence (`EVIDENCE_EXPIRED`) or a bound below policy (`GATE_LOST_OUTCOME`), with the reason codes in the ledger.

## Transition table

`lifecycle.TRANSITIONS` is the only source of legal moves; `_move` raises on anything else. `tests/test_lifecycle.py` checks it against `docs/v2/lifecycle_fixture.json`. The only entry to `AUTONOMOUS` is `(QUALIFYING, GATE_MET)`. `SUSPENDED` leaves only by `HUMAN_RELEASE`, only to `QUALIFYING`.

## Ledger records

Each state change, retirement, and lineage event is a `lifecycle.transition` ledger event with `from`, `to`, `event`, `reasons`, `epoch`, `retired`, `lineage_suspended`, `policy_version`, `evidence_id`, and `evidence_version`. The evidence is published before the move, so the record names the version the gate relied on.

## Changes to `shadow.py` in this step

- `open_epoch(key)` and epoch-aware counting (`shadow.epoch_opened` ledger event, replayed).
- `collected_until` now comes from the latest label or censor time of the counted trials. In Step 02 it was the publish time, which let re-publishing make old evidence look fresh. Fixed here and covered by `test_republishing_does_not_freshen_old_evidence`.

## Limits

- Step 05 closed baseline gap 2 for gateways wired for it (`gateway-redemption.md`). An already-issued token is refused after a suspension or retirement when the gateway holds the evidence store, because the manager marks the snapshot suspended or invalid (check 6). Refusal on lifecycle state, such as leaving `AUTONOMOUS` or lineage suspension, needs the manager attached as a `RedemptionGuard` (check 8). A gateway with neither keeps the v1 behaviour, and the token survives.
- `AUTONOMOUS` carries the time-uniform guarantee of spec §6 only under the default `beta_mixture` estimator and its assumptions (`statistical-method.md` §3). Under `wilson_legacy` it does not.
- Severity is supplied by the caller through `record_severe`; nothing detects it. State is in memory, and `refresh` is pull-based (it runs at `evaluate`), so expiry is noticed on the next decision, not by a timer.
- Churn is reported by the caller; the manager detects only policy-version changes by itself.
