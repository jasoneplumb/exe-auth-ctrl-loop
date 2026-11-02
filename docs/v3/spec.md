# v3 specification: durable authority, closed boundary, synchronous revocation

Status: Step 01 contract (final, 2026-11-01). Documentation and fixtures only; no behaviour in `src/` implements it yet. Every normative requirement names, in parentheses, the step that implements it, and `tests/test_v3_spec_fixture.py` checks that none is missing. Everything in `docs/v2/spec.md` stays in force unless a section here replaces it; replaced sections are named. The machine-readable counterpart of §5 is `docs/v3/revocation_fixture.json`.

## 1. Scope and trust boundary (replaces v2 §1)

- Models produce proposals and requests only. They never grant authority. An object supplied to the agent side (the execution agent, the proposal generator, the pipeline) MUST NOT contain or reach a handler, the handler table, or a signing key by any route except through `ExecutionGateway` or `AuthorityController`, which are the host's boundary objects and the intended door; and MUST NOT read or change token rows except through the gateway (Step 04). Owner decision 2026-11-02: these two objects are treated as opaque by the reachability test, since any object that can request an effect necessarily references, through them, what performs it.
- The controller, gateway, lifecycle, evidence intake, and store are one trust domain, the **host**, running in one process over one SQLite database on one host.
- v3 guarantees are **durable**: they hold across threads, across OS processes sharing the database file, and across restarts. They do not hold across hosts, and they do not hold against code running inside the host process (§4).
- Separating the host from the agent into different processes or hosts is a production requirement, stated, not provided (O-1).

## 2. Durable state

1. All authority state (tokens, decisions, ledger, evidence, trials, labels, lifecycle records, lineage, principals, grants, nonces, α consumption, risk charges) MUST be in the database, and no authority decision MAY depend on state held only in memory (Step 02).
2. Every operation that reads authority state and then writes it MUST do both inside one `BEGIN IMMEDIATE` transaction (Step 02).
3. The ledger, trials, labels, lineage, `ledger_anchor`, and `alpha_ledger` tables are append-only. Update and delete MUST be refused by the database (triggers), not only by the absence of a method (Step 02).
4. A restart MUST NOT clear a suspension, a lineage flag, a revocation, a consumed token, a pending trial, or a budget charge (Step 02).
5. 0.4.0 MUST NOT ship an in-memory implementation of authority state; tests use a temporary database file (O-7) (Step 02).

## 3. Capabilities (replaces the token parts of v2 §7 and `docs/v2/gateway-redemption.md` "Linearization point")

Token states: `issued`, `revoked`, `consumed`, `handler_started`, `handler_completed`, `handler_failed`. Legal transitions: `issued → revoked`, `issued → consumed → handler_started → handler_completed | handler_failed`. Nothing leaves `revoked`, `handler_completed`, or `handler_failed`.

Each token row records, besides the v0.3.1 token fields: `state`, `decision_id` (unique), `grant_id`, `requesting_principal`, `redemption_channel ∈ {local, mcp}`, and `mcp_audience` (null iff the channel is `local`). All are fixed at issue.

1. `issue()` MUST refuse a decision that is not recorded in `decisions` with the same `decision_id`, `proposal_digest`, and route. Commit-before-issue is enforced by the gateway (Step 04).
2. Redemption MUST be one transaction containing every check in `docs/v2/gateway-redemption.md` (checks 1–8), the gate recomputation in §5, and `UPDATE tokens SET state='consumed' WHERE token_id=? AND state='issued'`. It succeeds iff that update affects exactly one row and the transaction commits; the commit is the linearization point (Step 03).
3. The handler runs after the commit. `handler_started` is written before the call and `handler_completed` or `handler_failed` after it, each in its own transaction. A token left in `handler_started` after a restart means the effect is unknown; it MUST NOT be re-run, and the store MUST expose such tokens to an operator query, `effect_unknown()` (Step 03).
4. Each decision MUST produce at most one token. `issue()` MUST refuse a second request for a `decision_id` that already has a token row, whatever that row's state; a unique constraint on `decision_id` enforces it at the database. Closes C2's "several tokens from one decision" (Step 03).
5. `ExecutionGateway.execute()` MUST refuse a token whose `redemption_channel` is `mcp`; only §6 redemption consumes it (Step 07).

## 4. Execution boundary

1. Handlers MUST be registered only in a `HandlerTable` held by the gateway, and the gateway MUST select the handler from `token.allowed_tool`; no caller supplies a callable (Step 04).
2. The agent side MUST receive only a `ToolCatalog`: names, schemas, validation, risk classification, and no `execute` (Step 04).
3. Stated residual: in one Python process, reflection (`gc.get_objects()`, frame inspection) can reach any object. v3 removes every path the code supplies; it does not defend against code that inspects the interpreter. This is the reason O-1 remains a requirement.

## 5. Revoking events (F4)

Each event below MUST, in the same transaction that records it, set `state='revoked'` on every `issued` token in its scope with the event as `revocation_reason`, and append one ledger event naming the count revoked and listing any in-scope tokens already in `handler_started` (effect unknown; not revocable, reported) (Step 06). Redemption checks remain as a second line. One occurrence produces exactly one revoking event: where two rows could describe the same change, the table says which one fires, and a cascade is recorded inside the event that caused it, not as further events.

"Evidence-based" tokens are those not issued on an `Approval`. Rows scoped to them deliberately keep approval-based tokens: an approval does not rest on the statistical gate (as at v0.3.1, `docs/v2/gateway-redemption.md`).

| ID | Event | Recorded by | Scope revoked |
|---|---|---|---|
| RV-01 | `SEVERE_FAILURE` (any provenance) | `lifecycle.py` | every token of the partition and of every partition with a lineage edge from it |
| RV-02 | `EVIDENCE_INVALIDATED` | `authority.py` (`EvidenceStore`) | every token of the partition |
| RV-03 | `GATE_LOST` (an admitted label takes $L_t < r$) | `shadow.py`, same transaction as the label | evidence-based tokens of the partition |
| RV-04 | `ORACLE_CHANGED` (epoch opened for a new oracle; counts reset under §7 rule 11) | `lifecycle.py` | evidence-based tokens of the partition |
| RV-05 | `PARTITION_RETIRED` (key churn of a field other than `policy_version`) | `lifecycle.py` | every token of the partition |
| RV-06 | `LINEAGE_SUSPENDED` | `lifecycle.py` | every token of the successor partition |
| RV-07 | `POLICY_CHANGED` (new `policy_version`) | `revocation.py` | every token under the old version. In the same transaction every partition on the old version is retired and gets a lineage edge to its successor key; one event, not one `PARTITION_RETIRED` per partition |
| RV-08 | `EFFECT_PROHIBITED` (prohibited set grows without a version change) | `revocation.py` | every token whose effect set intersects the newly prohibited set |
| RV-09 | `BUDGET_TIGHTENED` | `risk.py` | every token whose committed proposal the new budget would refuse |
| RV-10 | `ENVELOPE_CHANGED` | `risk.py` | every token whose committed proposal the new envelope would refuse |
| RV-11 | `APPROVAL_RESCINDED` | `principals.py` | the token issued on that approval |
| RV-12 | `GRANT_REVOKED` | `principals.py` | every token issued under the grant or any descendant grant |
| RV-13 | `PRINCIPAL_DEACTIVATED` | `principals.py` | every token issued under a grant the principal holds or any descendant of one, and every token issued on an approval it gave; the revoked grants are listed in this event |

Not revoking, deliberately: an admitted label that leaves $L_t \ge r$; a new principal or grant; a looser policy that keeps the version. Not an event: evidence staleness, which is a time condition and is checked at redemption from `collected_until`.

`LifecycleManager.redemption_blockers` MUST compute the gate from current counts, not from the state recorded at the last `evaluate()`; this replaces the v2 constraint at `lifecycle.py:266` (Step 06).

Every successor partition, including one created by RV-07, MUST get a lineage edge from its predecessor, so suspension passes to it (closes L12) (Step 06).

## 6. MCP (replaces the redemption-free model of `docs/mcp-extension.md` 0.1)

1. Extension version `0.2`. Signed keys add `audience` (the receiving server's principal id) and `redemption` (`server` or `nonce`). The binding of 0.1 (proposal digest, call digest, decision, token, policy version, expiry, MAC) is kept unchanged.
2. A tool is **consequential** iff its registered effect set is non-empty. A consequential call MUST be redeemed by the server against the host's token table under §3.2, with the call digest and audience checked inside the transaction, and the host MUST NOT consume a token it bound to MCP (Step 07).
3. A non-consequential call MAY instead be accepted on verification plus an insert into `nonces(token_id, audience)` under a uniqueness constraint; a failed insert MUST deny, and the check that the token is still `issued` and the nonce insert MUST be one `BEGIN IMMEDIATE` transaction (§2 rule 2, as for redemption in §3 rule 2), so a revocation cannot commit between them (O-5) (Step 07).
4. Inside the redemption transaction the server MUST check that the token's `redemption_channel` is `mcp` and that the block's `audience` equals the stored `mcp_audience` (Step 07).
5. A server MUST refuse a block whose `audience` is not itself, a block whose version is not exactly `0.2` (allowlist: past and unknown future versions alike), and a block whose token is not `issued` (Step 07).

## 7. Evidence admission records (extends v2 §3–§5)

| Record | Fields (all immutable once written) |
|---|---|
| Trial | `trial_id` (= `decision_id`), `partition`, `epoch`, `provenance` (derived from the decision's route and approval), `frozen_digest`, `frozen_at` (host clock), `oracle_id`, `oracle_version` (bound at epoch open), `ledger_sequence` |
| Label | `trial_id`, `label`, `oracle_principal`, `oracle_version`, `claimed_at` (labeller's), `received_at` (host clock), `mac`, `admitted` (bool), `quarantine_reason` |
| Human review | `trial_id`, `reviewer_principal`, `action`, `rewritten_digest` |

Rules (in addition to v2 §3 rules 1–6):

7. Provenance MUST be derived by the host from the committed decision; no API accepts it as an argument (Step 08).
8. A label MUST be quarantined if its MAC fails, its oracle is not the epoch's bound oracle (`WRONG_ORACLE`), its principal is not an active `ORACLE`, or its principal recorded a human review of the same trial (`REVIEWER_IS_ORACLE`) (Step 08).
9. The label deadline MUST be measured on `received_at`; `claimed_at` is recorded and never used for admission (Step 08).
10. Every `HUMAN_APPROVAL`-routed proposal on an `UNESTABLISHED` or `QUALIFYING` partition MUST be frozen as `SHADOW` before the approval request is shown; no proposal in that class is skipped (O-6) (Step 08).
11. Each epoch MUST be bound at its opening to one `(oracle_id, oracle_version)`, and a change of oracle MUST open a new epoch (RV-04) (O-2) (Step 08).
12. Opening an epoch (human release, or an oracle change) MUST reset the partition's counts to zero, so no trial or label from an earlier epoch enters a later epoch's bound, and MUST record the opening in `alpha_ledger` with the α it starts with, so a reset of counts is never a silent reset of the error budget (see O-8) (Step 08).

## 8. Principals and delegation (new; F5)

- `Principal(principal_id, kind, key_id, active)`, kind ∈ `HUMAN`, `SERVICE`, `MODEL`, `ORACLE`. Every ledger `actor` MUST be a registered principal id (Step 05).
- `Grant(grant_id, parent_grant_id, grantor, grantee, scope, expires_at)`. `scope` = permitted tools, effects, partitions, budget ceilings. A grant MUST be refused unless its scope is a subset of its parent's and its expiry is not later than its parent's; root grants MUST be created only by a `HUMAN` principal (Step 05).
- Every token records `grant_id` and the requesting principal; `lineage(token_id)` returns the grant chain to its root.
- Every change to who may do what is exactly one of: `principal.registered`, `principal.deactivated`, `grant.created`, `grant.revoked`, `approval.recorded`, `approval.rescinded`. Permission changes without an event MUST NOT be possible through the API (Step 05).
- `Approval` (a `HUMAN` principal's MAC over decision id and proposal digest) replaces `human_approved: bool`, and `release()` MUST require a verified `HUMAN` principal (Step 05).

## 9. Statistics (pointer)

Unchanged until Step 09 reports. Candidate policy fields `pending_treatment ∈ {deadline, pessimistic}` and `alpha_spending ∈ {per_partition, online}` are specified in `plan.md` Step 09 and adopted only on its evidence.

## 10. Invariants for tests

1. No handler runs without a token row moving `issued → consumed` in a committed transaction (Step 03).
2. At most one `consumed` transition per token, across threads, processes, and restarts (Step 03).
3. No object reachable from the agent, proposer, or pipeline references a handler or the `HandlerTable` (Step 04).
4. After a revoking event commits, no token in its scope can be redeemed, with no intervening `evaluate()` (Step 06).
5. A consequential MCP call runs at most once per token, on its audience only (Step 07).
6. Evidence counts are a function of the append-only tables alone; replaying them reproduces counts and quarantines (Step 08).
7. No grant is broader than its parent; revoking a grant leaves no redeemable token beneath it (Steps 05, 06).

## 11. Decisions

O-1, O-2, O-5, O-6, O-7, O-9 are closed and written into the rules above (§2 rule 5, §5 RV-04, §6 rule 3, §7 rules 10–12). O-3, O-4, O-8 wait on Step 09.

| ID | Statement |
|---|---|
| O-1 | **Closed 2026-11-01 (owner): durable, same process.** Not: full service separation; in-process without durability; staged tiers. |
| O-2 | **Closed 2026-11-01 (owner): new epoch, counts reset.** Each epoch is bound to one oracle; a change opens a new epoch (§7 rules 11–12). Extended at Step 01 by O-9 (RV-04), which the original decision did not include. Not: oracle fields in `PartitionKey`; pooling with a per-oracle report. |
| O-3 | Open until Step 09. Pending trials: **count as failures until resolved (pessimistic)** (recommended, if Step 09 confirms) vs. outside `n` until the deadline (v0.3.1). |
| O-4 | Open until Step 09. Multiplicity: **online α spending by partition registration order, recorded in `alpha_ledger`** (recommended) vs. fixed Bonferroni with a declared K in the policy. |
| O-5 | **Closed 2026-11-01 (owner): nonce-only allowed** for non-consequential MCP calls (§6 rule 3). Not: redemption for every call; verify-only with replay until expiry. |
| O-6 | **Closed 2026-11-01 (owner): freeze every one** (§7 rule 10). Not: a host-drawn shadow rate; per-partition operator opt-in. |
| O-7 | **Closed 2026-11-01 (owner): remove in-memory classes in 0.4.0** (§2 rule 5). Not: deprecated shims for one release; a permanent in-memory backend. |
| O-8 | Open until Step 09. Repeated epochs (L11): **lineage-level α spending across releases** (recommended, if Step 09 confirms) vs. per-epoch α with lifetime rate reported only. |
| O-9 | **Closed 2026-11-01 (owner), raised at Step 01 review: approve RV-04 as written.** An oracle change (O-2) synchronously revokes the partition's evidence-based tokens; approval-based tokens are kept, as for `GATE_LOST`. Not: revoke every token; no event, leaving it to redemption-time gate recomputation. |
