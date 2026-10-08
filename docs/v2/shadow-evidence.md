# Shadow evidence (Step 02)

Implements spec §3-§5 in `src/exe_auth_ctrl_loop/shadow.py`. Existing `EvidenceStore`, `PartitionKey`, `Proposal`, controller, and gateway are unchanged.

## Contract

- `ShadowEvidenceLog.freeze(trial_id, proposal, provenance)` appends a `shadow.frozen` ledger event holding the proposal digest, partition, and time. That event is the pre-review commitment; `ShadowTrial.frozen_sequence` is its ledger position.
- `adjudicate(...)` takes one oracle label. The caller names the partition and the digest the oracle actually labeled, plus an `Oracle` (id, version, kind, validity scope).
- `record_human_review(...)` appends the reviewer's action and any rewritten digest. It never changes a trial, never creates one, and never counts. A rewrite is not evidence and cannot take a label.
- `expire_unlabeled()` censors trials past `label_deadline`. `publish()` calls it first, so withholding a label cannot keep a failing trial out of the bound.
- `counts(key, provenance)` returns `frozen / positive / negative / inconclusive / pending`, with `coverage` (decisive over frozen) and `censored_fraction`.
- `snapshot()` / `publish(store, key, collected_from)` turn counting provenance (`SHADOW`, `AUTONOMOUS`, `AUDIT`) into an `EvidenceSnapshot`: `successes` are positives, `failures` are negatives plus inconclusive. `HUMAN_APPROVED` is recorded and never counted. A partition with no counting trial yields no snapshot, so it stays unknown.
- `ShadowEvidenceLog.replay(ledger)` rebuilds the log from a verified ledger alone.

## Rejection reason codes

Quarantined requests change no evidence and are logged as `shadow.quarantined`.

| Code | Cause |
|---|---|
| `DUPLICATE_TRIAL` | `trial_id` already frozen |
| `UNKNOWN_TRIAL` | label or review for a trial that was never frozen |
| `WRONG_PARTITION` | label names a different 12-field key |
| `DIGEST_MISMATCH` | label is for something other than the frozen digest (e.g. a rewrite) |
| `ALREADY_ADJUDICATED` | second label, or label after censoring |
| `LABEL_BEFORE_FREEZE` | label timestamp precedes the freeze |
| `LATE_LABEL` | label after the deadline; the trial is censored instead |
| `LABEL_DEADLINE` | (censor reason) no label by the deadline |

## What the counts mean

The labels estimate oracle-adjudicated acceptability of the frozen proposal under the oracle's validity scope. They are a proxy and do **not** estimate live side-effect reliability; equivalence is assumption A5 and must be stated wherever results are reported. The bound applied to these counts is still the v1 fixed-`z` Wilson bound, which does not meet the time-uniform claim in spec §6; Step 04 replaces it. Do not describe a partition qualified by this log as having a validated autonomy guarantee.

## Limits

- In-memory and single-process; the ledger can detect edits but not truncation or replacement.
- `publish()` replaces the partition snapshot with log-derived counts. Do not mix it with a seeded or v1-adjudicated snapshot for the same key.
- Severe-failure suspension, epochs, and the state machine are Step 03; this module only supplies admissible counts.
- The oracle itself is a caller-supplied label. No oracle implementation or validation procedure exists yet (spec O-3).
