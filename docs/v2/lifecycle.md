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

All of: `n >= n_min`; Wilson lower bound at least the policy's bound for the risk class (v1 bound, replaced in Step 04); evidence not older than `max_evidence_age`, measured from the last label or censor time; policy version matches; not lineage-suspended; not retired. An `AUTONOMOUS` partition falls back to `QUALIFYING` on stale evidence (`EVIDENCE_EXPIRED`) or a bound below policy (`GATE_LOST_OUTCOME`), with the reason codes in the ledger.

## Transition table

`lifecycle.TRANSITIONS` is the only source of legal moves; `_move` raises on anything else. `tests/test_lifecycle.py` checks it against `docs/v2/lifecycle_fixture.json`. The only entry to `AUTONOMOUS` is `(QUALIFYING, GATE_MET)`. `SUSPENDED` leaves only by `HUMAN_RELEASE`, only to `QUALIFYING`.

## Ledger records

Each state change, retirement, and lineage event is a `lifecycle.transition` ledger event with `from`, `to`, `event`, `reasons`, `epoch`, `retired`, `lineage_suspended`, `policy_version`, `evidence_id`, and `evidence_version`. The evidence is published before the move, so the record names the version the gate relied on.

## Changes to `shadow.py` in this step

- `open_epoch(key)` and epoch-aware counting (`shadow.epoch_opened` ledger event, replayed).
- `collected_until` now comes from the latest label or censor time of the counted trials. In Step 02 it was the publish time, which let re-publishing make old evidence look fresh. Fixed here and covered by `test_republishing_does_not_freshen_old_evidence`.

## Limits

- An already-issued token is not invalidated by a suspension or retirement; that is Step 05 (baseline gap 2 remains open).
- The statistical gate is still the fixed-`z` Wilson bound; do not read `AUTONOMOUS` here as the time-uniform guarantee of spec §6 (Step 04).
- Severity is supplied by the caller through `record_severe`; nothing detects it. State is in memory, and `refresh` is pull-based (it runs at `evaluate`), so expiry is noticed on the next decision, not by a timer.
- Churn is reported by the caller; the manager detects only policy-version changes by itself.
