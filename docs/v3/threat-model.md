# v3 threat model (delta)

Changes to `docs/v2/threat-model.md` that v3 targets. Rows not listed are unchanged. "Target" columns state what v3 is meant to establish once the named step passes its gate; until then the v2 document is the operative one.

## Trust classification changes

| Component | v2 | v3 target |
|---|---|---|
| Host | Trusted; one process, in memory | Trusted; one process, state in one SQLite database. Other OS processes on the same host that open the database through the store API are inside the trust domain |
| Agent side (proposer, executor, pipeline objects) | Potentially adversarial; held the tool registry | Potentially adversarial; holds a `ToolCatalog` only (Step 04) |
| Oracle | Fallible; identity and timestamps asserted by the caller | Fallible but authenticated: registered `ORACLE` principal, MAC-signed labels, host-stamped receipt time (Step 08) |
| Human reviewer / approver | Fallible; approval is a boolean, release takes any name | Authenticated `HUMAN` principal; approval and release are MAC-signed records (Step 05) |
| MCP server | Holds the shared secret; verifies statelessly | Registered `SERVICE` principal and audience; redeems consequential calls against the host token table (Step 07) |
| Ledger | In-memory, unkeyed hash | Append-only table, HMAC chain under a host key, contiguous sequence, append-only anchor table (Step 08). The key is passed to `Store(path, ledger_key=...)` and never written to the file (owner decision, Step 08): it defends against someone who can write the database file, not against the host or same-process code holding the `Store` |

## Threats: changed rows

| ID | Threat | v2 residual | v3 containment | v3 residual |
|---|---|---|---|---|
| TM-6 | Suspension laundering via config churn | Only `on_churn` carries lineage; policy-version successors start clean (L12); lineage in memory | Lineage edge for every successor including policy change; durable (Steps 02, 06) | None within the trust boundary |
| TM-7 | Stale token after suspension or policy change | Depends on what the gateway was given; loss of autonomy seen only after a later `evaluate()` | Revoking events revoke in their own transaction (spec §5); redemption recomputes the gate (Step 06) | Evidence staleness is checked at redemption, not revoked at the instant it occurs; a token can expire first, which is the same outcome |
| TM-8 | Concurrent double redemption | Thread lock; nothing across processes or restart; tests pass without the lock | Conditional update in one `BEGIN IMMEDIATE` transaction; cross-process and restart tests with a mutation check (Step 03) | Across hosts: not claimed |
| TM-10 | Agent biases which proposals are shadowed or audited | Shadow creation not shipped | Every human-routed proposal on a non-autonomous partition is shadowed (spec §7 rule 10, Step 08) | Agent influence over the proposal stream itself (A3) |
| TM-11 | Label non-response steers the sample | Deadline only; withholding helps inside the window (L8) | Pessimistic pending if adopted (O-3, Step 09). Proved to keep false authorization ≤ α whatever is withheld (`statistical-method.md` §11) | Measured in Step 09 (`results-v3/statistics/`): withholding negatives under the shipped deadline-only rule gives false authorization 1.0 at $p = r$; pessimistic pending 0.004. Until O-3 is decided and adopted, the shipped rule's residual is the measured one |

## Threats: new rows

| ID | Threat | v3 containment (step) | Residual |
|---|---|---|---|
| TM-13 | Replay of a valid MCP block, to the same or another server | Audience in the MAC; server-side redemption or insert-once nonce (Step 07) | A server holding the shared secret can mint blocks; per-server keys are a production requirement |
| TM-14 | Oracle impersonation, backdated labels, reviewer acting as oracle, an oracle capturing an epoch by labelling first | Oracle principals and signed labels; `received_at` governs the deadline; `REVIEWER_IS_ORACLE` quarantine (Step 08). Every epoch is bound explicitly at its opening: the host binds epoch 0, a release binds the oracle the releasing human signed, and an unbound epoch admits no label (`ORACLE_NOT_BOUND`, issue #41) | Collusion between distinct principals; a host that binds the wrong oracle |
| TM-15 | Delegation escalation: a delegate grants itself or a child more than it holds | Attenuation-only grants checked at creation; grant chain on every token; revocation cascades (Steps 05, 06) | None within the trust boundary |
| TM-16 | Agent invokes a handler directly | Handlers only in the gateway's table; tool chosen from the token (Step 04) | Interpreter reflection in the same process (spec §4.3); closed only by process separation |
| TM-17 | Unrecorded permission change | Every permission change is one of six ledger event types; no API path without one (Step 05) | Direct database writes by the host |
| TM-18 | Issuance on a fabricated decision | `issue()` requires a matching committed `decisions` row (Step 04) | None within the trust boundary |
| TM-19 | Ledger rewrite by someone who can write the database file (C22) | HMAC chain under a key the file does not hold; contiguous sequence check; a re-hashed edit, insertion, or deletion fails `verify()` and `replay()` refuses it (Step 08) | Tail truncation (needs an anchor held outside the host); any rewrite by a key holder (the host, same-process code holding the `Store`); direct edits to the derived tables (trials, labels), which are not chained and are detected only by comparing them with a replay of the verified ledger |
| TM-20 | Provenance asserted by the caller | Provenance derived from the committed decision's route and approval record; no API takes it (Step 08) | A host that commits a decision with a false route |

## Unsupported adversaries (unchanged, restated)

A compromised host, operator, or anyone holding the host keys (the ledger key included); anyone with write access to the database file, except for the ledger chain itself (TM-19); collusion between distinct principals; side channels; denial of service; real-world validity of oracle labels beyond their declared scope; adversaries on other hosts.

## Prototype versus production (replaces the v2 table)

| Property | v0.3.1 | v3 target | Production requirement still open |
|---|---|---|---|
| Token single use | Thread lock on a dict | Durable conditional update, cross-process and restart | Cross-host store |
| Execution boundary | Convention | Handlers unreachable through any supplied object | Separate process or host for the gateway |
| Evidence store | In memory | Durable, append-only | Replication |
| Ledger | In-memory hash chain | Keyed chain (key held by the `Store`, not the file), contiguous sequence, append-only anchor table | Anchor held outside the host; key held outside the authority process |
| Identity | None | Principals with HMAC keys | Asymmetric keys, external identity provider |
| Audit randomness | `SystemRandom` | Unchanged | Auditable CSPRNG with committed seed |
| Suspension lineage | In memory, `on_churn` only | Durable, every successor | |
