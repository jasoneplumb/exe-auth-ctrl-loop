# v3 specification: durable authority, closed boundary, synchronous revocation

Status: draft contract, written at Step 00 alongside `plan.md`; Step 01 finalizes it. No behaviour in `src/` implements it yet. MUST / MUST NOT / SHOULD / MAY are normative for Steps 02–09. Everything in `docs/v2/spec.md` stays in force unless a section here replaces it; replaced sections are named.

## 1. Scope and trust boundary (replaces v2 §1)

- Models produce proposals and requests only. They never grant authority. An object supplied to the agent side MUST NOT contain or reach a handler, the handler table, the token table, or a signing key.
- The controller, gateway, lifecycle, evidence intake, and store are one trust domain, the **host**, running in one process over one SQLite database on one host.
- v3 guarantees are **durable**: they hold across threads, across OS processes sharing the database file, and across restarts. They do not hold across hosts, and they do not hold against code running inside the host process (§4).
- Separating the host from the agent into different processes or hosts is a production requirement, stated, not provided (O-1).

## 2. Durable state

1. All authority state (tokens, decisions, ledger, evidence, trials, labels, lifecycle records, lineage, principals, grants, nonces, α consumption, risk charges) MUST be in the database. No authority decision MAY depend on state held only in memory.
2. Every operation that reads authority state and then writes it MUST do both inside one `BEGIN IMMEDIATE` transaction.
3. The ledger, trials, labels, lineage, `ledger_anchor`, and `alpha_ledger` tables are append-only. Update and delete MUST be refused by the database (triggers), not only by the absence of a method.
4. A restart MUST NOT clear a suspension, a lineage flag, a revocation, a consumed token, a pending trial, or a budget charge.

## 3. Capabilities (replaces the token parts of v2 §7 and `docs/v2/gateway-redemption.md` "Linearization point")

Token states: `issued`, `revoked`, `consumed`, `handler_started`, `handler_completed`, `handler_failed`. Legal transitions: `issued → revoked`, `issued → consumed → handler_started → handler_completed | handler_failed`. Nothing leaves `revoked`, `handler_completed`, or `handler_failed`.

Each token row records, besides the v0.3.1 token fields: `state`, `decision_id` (unique), `grant_id`, `requesting_principal`, `redemption_channel ∈ {local, mcp}`, and `mcp_audience` (null iff the channel is `local`). All are fixed at issue.

1. `issue()` MUST refuse a decision that is not recorded in `decisions` with the same `decision_id`, `proposal_digest`, and route. Commit-before-issue is enforced by the gateway.
2. Redemption is one transaction: every check in `docs/v2/gateway-redemption.md` (checks 1–8), the gate recomputation in §5, and `UPDATE tokens SET state='consumed' WHERE token_id=? AND state='issued'`. The redemption succeeds iff that update affects exactly one row and the transaction commits. The commit is the linearization point.
3. The handler runs after the commit. `handler_started` is written before the call and `handler_completed` or `handler_failed` after it, each in its own transaction. A token left in `handler_started` after a restart means the effect is unknown; it MUST NOT be re-run, and the store MUST expose such tokens to an operator query (`effect_unknown()`), so they are triaged rather than silently left.
4. Each decision MUST produce at most one token. `issue()` MUST refuse a second request for a `decision_id` that already has a token row, whatever that row's state; the unique constraint on `decision_id` enforces it at the database. Closes C2's "several tokens from one decision".
5. `ExecutionGateway.execute()` MUST refuse a token whose `redemption_channel` is `mcp`. Only §6 redemption consumes it.

## 4. Execution boundary

1. Handlers are registered in a `HandlerTable` held only by the gateway. The gateway selects the handler from `token.allowed_tool`; no caller supplies a callable.
2. The agent side receives a `ToolCatalog`: names, schemas, validation, risk classification. It has no `execute`.
3. Stated residual: in one Python process, reflection (`gc.get_objects()`, frame inspection) can reach any object. v3 removes every path the code supplies; it does not defend against code that inspects the interpreter. This is the reason O-1 remains a requirement.

## 5. Revoking events (F4)

Each event below MUST, in the same transaction that records it, set `state='revoked'` on every `issued` token in its scope with the event as `revocation_reason`, and append one ledger event naming the count revoked and listing any in-scope tokens already in `handler_started` (effect unknown; not revocable, reported). Redemption checks remain as a second line. One occurrence produces exactly one revoking event: where two rows could describe the same change, the table says which one fires.

| Event | Recorded by | Scope revoked |
|---|---|---|
| `SEVERE_FAILURE` (any provenance) | lifecycle | partition, and every partition with a lineage edge from it |
| `EVIDENCE_INVALIDATED` | evidence store | partition |
| `GATE_LOST` (an admitted label takes $L_t < r$) | evidence intake, same transaction as the label | evidence-based tokens of the partition only. Tokens issued on an `Approval` are deliberately kept: an approval does not rest on the statistical gate (as at v0.3.1, `docs/v2/gateway-redemption.md`). Contrast `SEVERE_FAILURE`, which revokes every token |
| `PARTITION_RETIRED` (key churn of a field other than `policy_version`) | lifecycle | partition |
| `LINEAGE_SUSPENDED` | lifecycle | successor partition |
| `POLICY_CHANGED` (new `policy_version`) | policy provider | every token under the old version. In the same transaction, every partition on the old version is marked retired and gets a lineage edge to its successor key; this is one event, not one `PARTITION_RETIRED` per partition |
| `EFFECT_PROHIBITED` (prohibited set grows without a version change) | policy provider | every token whose effect set intersects the newly prohibited set |
| `BUDGET_TIGHTENED` or `ENVELOPE_CHANGED` | risk policy | tokens whose committed proposal the new limits would refuse |
| `APPROVAL_RESCINDED` | principals | the token issued on that approval |
| `GRANT_REVOKED` | principals | tokens issued under the grant and under every descendant grant |
| `PRINCIPAL_DEACTIVATED` | principals | grants held by the principal (then as `GRANT_REVOKED`) and approvals it gave |

Not revoking, deliberately: an admitted label that leaves $L_t \ge r$; a new principal or grant; a looser policy that keeps the version. Not an event: evidence staleness, which is a time condition and is checked at redemption from `collected_until`.

`LifecycleManager.redemption_blockers` MUST compute the gate from current counts, not from the state recorded at the last `evaluate()` (replaces the v2 constraint at `lifecycle.py:266`).

## 6. MCP (replaces the redemption-free model of `docs/mcp-extension.md` 0.1)

1. Extension version `0.2`. Signed keys add `audience` (the receiving server's principal id) and `redemption` (`server` or `nonce`). The binding of 0.1 (proposal digest, call digest, decision, token, policy version, expiry, MAC) is kept unchanged.
2. A tool is **consequential** iff its registered effect set is non-empty. A consequential call MUST be redeemed by the server against the host's token table under §3.2, with the call digest and audience checked inside the transaction. The host MUST NOT consume a token it bound to MCP.
3. Pending O-5: a non-consequential call MAY be accepted on verification plus an insert into `nonces(token_id, audience)` under a uniqueness constraint; a failed insert denies. If O-5 is decided the other way, rule 2 applies to every call.
4. Inside the redemption transaction the server MUST check that the token's `redemption_channel` is `mcp` and that the block's `audience` equals the stored `mcp_audience`.
5. A server MUST refuse a block whose `audience` is not itself, a block whose version is not exactly `0.2` (allowlist: past and unknown future versions alike), and a block whose token is not `issued`.

## 7. Evidence admission records (extends v2 §3–§5)

| Record | Fields (all immutable once written) |
|---|---|
| Trial | `trial_id` (= `decision_id`), `partition`, `epoch`, `provenance` (derived from the decision's route and approval), `frozen_digest`, `frozen_at` (host clock), `oracle_id`, `oracle_version` (bound at epoch open), `ledger_sequence` |
| Label | `trial_id`, `label`, `oracle_principal`, `oracle_version`, `claimed_at` (labeller's), `received_at` (host clock), `mac`, `admitted` (bool), `quarantine_reason` |
| Human review | `trial_id`, `reviewer_principal`, `action`, `rewritten_digest` |

Rules (in addition to v2 §3 rules 1–6):

7. Provenance MUST be derived by the host from the committed decision. A caller cannot assert it.
8. A label MUST be quarantined if its MAC fails, its oracle is not the epoch's bound oracle (`WRONG_ORACLE`), its principal is not an active `ORACLE`, or its principal recorded a human review of the same trial (`REVIEWER_IS_ORACLE`).
9. The label deadline is measured on `received_at`. `claimed_at` is recorded and never used for admission.
10. Pending O-6 (decided at Step 01; not yet normative): every `HUMAN_APPROVAL`-routed proposal on an `UNESTABLISHED` or `QUALIFYING` partition is frozen as `SHADOW` before the approval request is shown. Alternative: a host-drawn shadow rate committed with the decision.
11. Opening an epoch (human release, or an oracle change under O-2) MUST reset the partition's counts to zero: no trial or label from an earlier epoch enters a later epoch's bound, so evidence never pools across oracles or across a suspension. The epoch open is recorded in `alpha_ledger` with the α it starts with, so a reset of counts is never a silent reset of the error budget (see O-8).

## 8. Principals and delegation (new; F5)

- `Principal(principal_id, kind, key_id, active)`, kind ∈ `HUMAN`, `SERVICE`, `MODEL`, `ORACLE`. Every ledger `actor` MUST be a registered principal id.
- `Grant(grant_id, parent_grant_id, grantor, grantee, scope, expires_at)`. `scope` = permitted tools, effects, partitions, budget ceilings. A grant MUST be refused unless its scope is a subset of its parent's and its expiry is not later than its parent's. Root grants are created only by a `HUMAN` principal.
- Every token records `grant_id` and the requesting principal; `lineage(token_id)` returns the grant chain to its root.
- Every change to who may do what is exactly one of: `principal.registered`, `principal.deactivated`, `grant.created`, `grant.revoked`, `approval.recorded`, `approval.rescinded`. Permission changes without an event MUST NOT be possible through the API.
- `Approval` (a `HUMAN` principal's MAC over decision id and proposal digest) replaces `human_approved: bool`.

## 9. Statistics (pointer)

Unchanged until Step 09 reports. Candidate policy fields `pending_treatment ∈ {deadline, pessimistic}` and `alpha_spending ∈ {per_partition, online}` are specified in `plan.md` Step 09 and adopted only on its evidence.

## 10. Invariants for tests

1. No handler runs without a token row moving `issued → consumed` in a committed transaction.
2. At most one `consumed` transition per token, across threads, processes, and restarts.
3. No object reachable from the agent, proposer, or pipeline references a handler or the `HandlerTable`.
4. After a revoking event commits, no token in its scope can be redeemed, with no intervening `evaluate()`.
5. A consequential MCP call runs at most once per token, on its audience only.
6. Evidence counts are a function of the append-only tables alone; replaying them reproduces counts and quarantines.
7. No grant is broader than its parent; revoking a grant leaves no redeemable token beneath it.

## 11. Decisions to confirm

O-1, O-2, O-5, O-6, O-7 are closed. The rules that still read "pending" for O-2, O-5, and O-6 (§6 rule 3, §7 rules 10–11) become normative in Step 01 (gate criterion 3b). O-3, O-4, O-8 wait on Step 09.

| ID | Statement |
|---|---|
| O-1 | **Closed 2026-11-01 (owner): durable, same process.** Not: full service separation; in-process without durability; staged tiers. |
| O-2 | **Closed 2026-11-01 (owner): new epoch, counts reset.** Oracle change: **bind each epoch to one oracle and open a new epoch on change, with counts reset (§7 rule 11)** (recommended; no partition-key change, same no-pooling result) vs. add `oracle_id`, `oracle_version` to `PartitionKey` (a new partition; changes every digest). |
| O-3 | Pending trials: **count as failures until resolved (pessimistic)** (recommended, if Step 09 confirms) vs. outside `n` until the deadline (v0.3.1). |
| O-4 | Multiplicity: **online α spending by partition registration order, recorded in `alpha_ledger`** (recommended) vs. fixed Bonferroni with a declared K in the policy. |
| O-5 | **Closed 2026-11-01 (owner): nonce-only allowed.** Non-consequential MCP calls: **nonce-only acceptance allowed** (recommended) vs. server-side redemption for every call. |
| O-6 | **Closed 2026-11-01 (owner): freeze every one.** Shadow selection: **freeze every human-routed proposal on a non-autonomous partition** (recommended; no selection, A3 by construction) vs. a host-drawn shadow rate committed with the decision. |
| O-7 | **Closed 2026-11-01 (owner): remove in 0.4.0.** Compatibility: **remove in-memory classes in 0.4.0** (recommended; 0.x, one code path) vs. keep them as deprecated shims for one release. |
| O-8 | Repeated epochs (L11): **lineage-level α spending across releases** (recommended, if Step 09 confirms) vs. per-epoch α with lifetime rate reported only. |
