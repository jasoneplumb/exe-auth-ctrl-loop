# MCP extension: execution-authority metadata on `tools/call`

A third-party [Model Context Protocol](https://modelcontextprotocol.io) extension that
carries an authorization with the tool call it authorizes, so a server can verify the call
rather than trust an assertion about it.

Reference implementation: [`src/exe_auth_ctrl_loop/mcp.py`](../src/exe_auth_ctrl_loop/mcp.py).
Runnable demo: [`examples/mcp_demo.py`](../examples/mcp_demo.py).

## Why the metadata is signed

`_meta` travels on a request that a model asked for. In this architecture model output is
never authority, so an unsigned block would be a claim the server has no way to check —
and a server that acted on it would reintroduce precisely the hole the control loop closes.
Every field below is covered by a MAC, and verification fails closed.

This is not an optional hardening step. `build_call_meta` requires a secret and raises
without one; there is no unsigned mode to reach for by accident.

## Prefix

```text
com.jasoneplumb.exe-auth/
```

Legal as a third-party vendor prefix. The MCP specification reserves *"any prefix where the
second label is `modelcontextprotocol` or `mcp`"* — here the second label is `jasoneplumb`,
a domain the extension's author controls. `validate_meta_prefix` enforces the rule, so
`io.modelcontextprotocol/`, `dev.mcp/`, and `com.mcp.tools/` are rejected while
`com.example.mcp/` is accepted, matching the specification's own worked example.

## Keys

All keys carry the prefix above. All twelve non-MAC keys are covered by the MAC; every one
is required, and a missing key is a denial rather than a default.

| Key | Type | Meaning |
| --- | --- | --- |
| `version` | string | Extension version, exactly `0.2`. Anything else, older or newer, is refused. |
| `proposalDigest` | string | Digest of the authorized proposal, as the host recorded it. |
| `evidenceSnapshot` | string \| null | Digest of the evidence identity, version, **and partition** that justified the decision. `null` on the human-approval path, where no evidence backed it. |
| `auditCommitted` | boolean | Whether this operation was drawn for audit. The draw is committed into the authorization record before the outcome exists. |
| `humanApproved` | boolean | Whether a human approved this execution. |
| `callDigest` | string | Digest of `{tool, arguments}` — the one field the server can independently recompute. |
| `decisionId` | string | Authority decision identifier, for correlation with the host ledger. |
| `tokenId` | string | Capability token identifier. The server spends it at the host (below). |
| `policyVersion` | string | Policy version under which authority was granted. |
| `expiresAt` | string | RFC 3339 expiry, timezone-aware. A naive timestamp is rejected. |
| `audience` | string | *(0.2)* Principal id of the one MCP server this block is for. Any other server refuses it. |
| `redemption` | string | *(0.2)* `server` when the tool has effects (consequential): the server must redeem the token against the host's token table. `nonce` when it has none: a single-use nonce suffices. |
| `mac` | string | HMAC-SHA256 over the canonical JSON of the twelve keys above. |

### On `humanApproved`

It is here because the selection-bias firewall depends on it. If a downstream outcome
reporter cannot tell an approved execution from an autonomous one, human-approved runs get
counted as autonomous successes — and the human filter has removed exactly the failures the
autonomous path would have committed. The evidence base is then censored where the system is
least trustworthy. Carrying the flag keeps the estimator on-policy across a process boundary.

### On `evidenceSnapshot`

The partition is part of the hash input, not just the evidence id and version. Evidence ids
are unique *per partition* — `EvidenceStore` keys on `PartitionKey` — so hashing the id alone
would let the same id in two partitions produce one hash and make downstream correlation
ambiguous. The value stays opaque to the server, which never recomputes it.

### On `auditCommitted`

The flag reflects a draw already committed into the hash-chained authorization record before
any outcome exists. A server must treat it as an observation, not an instruction: it is not
a request to behave differently on audited calls, and behaving differently would defeat the
property the commitment establishes.

## MAC construction

```text
canonical = JSON(body, sorted keys, no whitespace)   # the twelve signed keys, MAC excluded
mac       = HMAC-SHA256(secret, canonical)
```

Adding a key to the signed set changes the wire format and requires a `version` bump.

## Verification order

A server must perform these in order and deny on the first failure (`verify_call_meta`):

1. `_meta` is present and is a mapping.
2. `version` is exactly `0.2`. This comes first so a block from another version is refused
   for that reason, not for a missing key; the MAC still covers the field.
3. All twelve signed keys are present.
4. `mac` is present, and recomputing it over the signed keys matches under constant-time
   comparison.
5. `callDigest` equals the digest recomputed from the tool name and arguments **the server
   actually received** — not from anything the request asserts about itself.
6. `expiresAt` has not passed.
7. `audience` is this server's own principal id.
8. `redemption` is `server` or `nonce`.

Step 5 is what makes the binding meaningful. Edited arguments and a substituted tool both
fail there; a forged, stripped, or tampered field fails at step 4.

## Spending the block (0.2)

Verification is stateless, so a verified block would verify again inside its TTL. A server
therefore spends it at the host before running anything (`McpServer.call`):

- **`redemption: server`** (the tool has effects): `ExecutionGateway.redeem_remote()`. One
  transaction checks that the token is bound to MCP and to this server, that the call digest
  matches the proposal stored with the token, every redemption check the host itself would
  run (expiry, live policy and evidence, lifecycle, grants, budgets), and then consumes the
  token with the same conditional update as a local redemption. The server's handler runs
  after the commit, and the token records `handler_started` and `handler_completed` or
  `handler_failed` as any other.
- **`redemption: nonce`** (no effects): `ExecutionGateway.accept_nonce()`. The same checks,
  without consuming, then an insert into `nonces` whose primary key refuses a second
  presentation, from any thread or process.

A replay to the same server finds the token spent or the nonce taken; a replay to another
server fails at step 7. A token revoked by any host event (`docs/v3/spec.md` §5) is refused
at the server with that event as the reason. The host never spends an MCP-bound token itself:
`ExecutionGateway.execute()` refuses it.

The server shares the host's token table: one database on one host, as v3 is scoped
(`docs/v3/spec.md` O-1). Across hosts the same two calls become a network service.

## Coexistence with MCP's own `_meta`

The base protocol requires `io.modelcontextprotocol/protocolVersion` and
`io.modelcontextprotocol/clientCapabilities` on every request, and reserves `progressToken`,
`traceparent`, and others. `attach_meta` preserves foreign keys and refuses a collision
rather than overwriting one.

## Migrating from 0.1

0.1 had no `audience` and no `redemption`, and a server verified a block without spending it,
so a captured block replayed inside its TTL, to the same server or another, verified again.
0.2 closes that, and changes the wire format:

- **Host:** issue MCP-bound tokens with `ExecutionGateway.issue(..., audience=<server>)`,
  where the server is a registered SERVICE principal. `build_call_meta` refuses a token
  without an audience.
- **Server:** pass its own principal id to `verify_call_meta(..., audience=)`, and spend the
  block with `redeem_remote` or `accept_nonce` (or use `McpServer`, which does both).
- **Mixed versions:** none. A 0.1 block is refused by a 0.2 server, and a 0.2 block by a 0.1
  server (its version check). Upgrade hosts and servers together.

## Remaining limit

One shared secret signs blocks for every server, so any server holding it could mint a block
for another. Per-server keys (or asymmetric signatures) are a production requirement.

## Status

Version `0.2` (v3 Step 07), tracking the MCP `_meta` rules as of the 2026-07-28
specification. Version `0.1` is the v0.2.0–v0.3.1 format. This is a research prototype, not
a registered extension; it ships under a vendor prefix precisely because that requires no
permission from the MCP project.
