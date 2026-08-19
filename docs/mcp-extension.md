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

All keys carry the prefix above. All ten non-MAC keys are covered by the MAC; every one is
required, and a missing key is a denial rather than a default.

| Key | Type | Meaning |
| --- | --- | --- |
| `version` | string | Extension version. A mismatch denies rather than degrades. |
| `proposalDigest` | string | Digest of the authorized proposal, as the host recorded it. |
| `evidenceSnapshot` | string \| null | Digest of the evidence identity and version that justified the decision. `null` on the human-approval path, where no evidence backed it. |
| `auditCommitted` | boolean | Whether this operation was drawn for audit. The draw is committed into the authorization record before the outcome exists. |
| `humanApproved` | boolean | Whether a human approved this execution. |
| `callDigest` | string | Digest of `{tool, arguments}` — the one field the server can independently recompute. |
| `decisionId` | string | Authority decision identifier, for correlation with the host ledger. |
| `tokenId` | string | Capability token identifier. Single-use enforcement lives in the gateway. |
| `policyVersion` | string | Policy version under which authority was granted. |
| `expiresAt` | string | RFC 3339 expiry, timezone-aware. A naive timestamp is rejected. |
| `mac` | string | HMAC-SHA256 over the canonical JSON of the ten keys above. |

### On `humanApproved`

It is here because the selection-bias firewall depends on it. If a downstream outcome
reporter cannot tell an approved execution from an autonomous one, human-approved runs get
counted as autonomous successes — and the human filter has removed exactly the failures the
autonomous path would have committed. The evidence base is then censored where the system is
least trustworthy. Carrying the flag keeps the estimator on-policy across a process boundary.

### On `auditCommitted`

The flag reflects a draw already committed into the hash-chained authorization record before
any outcome exists. A server must treat it as an observation, not an instruction: it is not
a request to behave differently on audited calls, and behaving differently would defeat the
property the commitment establishes.

## MAC construction

```text
canonical = JSON(body, sorted keys, no whitespace)   # the ten signed keys, MAC excluded
mac       = HMAC-SHA256(secret, canonical)
```

Adding a key to the signed set changes the wire format and requires a `version` bump.

## Verification order

A server must perform these in order and deny on the first failure:

1. `_meta` is present and is a mapping.
2. All ten signed keys are present.
3. `mac` is present, and recomputing it over the signed keys matches under constant-time
   comparison.
4. `version` is supported.
5. `callDigest` equals the digest recomputed from the tool name and arguments **the server
   actually received** — not from anything the request asserts about itself.
6. `expiresAt` has not passed.

Step 5 is what makes the binding meaningful. Edited arguments and a substituted tool both
fail there; a forged, stripped, or tampered field fails at step 3.

## Coexistence with MCP's own `_meta`

The base protocol requires `io.modelcontextprotocol/protocolVersion` and
`io.modelcontextprotocol/clientCapabilities` on every request, and reserves `progressToken`,
`traceparent`, and others. `attach_meta` preserves foreign keys and refuses a collision
rather than overwriting one.

## Known gap: cross-server replay

A server cannot detect replay on its own. The gateway enforces single use, but a captured
block replayed to a *different* server inside the token TTL verifies successfully — the MAC
is valid and the arguments still match. Two closes are available, and both change the wire
format:

- a per-server audience field inside the MAC, so a block is valid at one server only; or
- a server-side nonce cache keyed on `tokenId` for the TTL window.

Until then, deployments should keep the token TTL short and enforce idempotency at the
external service, as the disclosure's production trust boundary already requires.

## Status

Version `0.1`, tracking the MCP `_meta` rules as of the 2026-07-28 specification. This is a
research prototype, not a registered extension; it ships under a vendor prefix precisely
because that requires no permission from the MCP project.
