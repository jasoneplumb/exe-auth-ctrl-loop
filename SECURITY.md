# Security Policy

This project is a prototype of a cross-model execution-authority control loop.
The authority boundary it demonstrates is real, but the implementation is
in-process and illustrative. Read the "Production trust boundary" section of the
README before deploying any part of this code; the prototype does not provide
process isolation, signed capabilities, durable audit storage, or authenticated
human approvals.

## Supported versions

Only the latest commit on `mainline` is supported. There are no maintenance
branches.

## Reporting a vulnerability

Do not open a public issue for a security report. Instead, use GitHub's private
vulnerability reporting: **Security → Report a vulnerability** on this
repository. Include:

- the affected file(s) and commit hash;
- a description of the boundary being bypassed (e.g., handler reachable without
  a capability, token reuse, post-approval mutation, evidence-partition
  confusion); and
- a proof-of-concept or failing test if you have one.

You should receive an acknowledgment within 7 days.

## Scope notes

Reports that identify a way for either model integration (proposal or
execution) to cause a registered handler to run without a valid, unexpired,
single-use capability issued by the host-owned controller are always in scope.
Prompt-injection findings against the models themselves are in scope only when
they defeat a host-side invariant; the design assumes model outputs are
untrusted requests.

Also in scope: any way to redeem a token after its partition was suspended,
its evidence invalidated, its policy version replaced, or its lifecycle state
left `AUTONOMOUS`, when the gateway was constructed with that live state; and
any way for two redemptions of one token to reach a handler twice within a
single process.

## Known limitations of the prototype gateway

- Check-and-consume is serialized by a `threading.Lock`. This is correct for
  threads in one process and makes no claim across processes, machines, or a
  restart. Production needs a transactional consume in a durable store before
  the handler is reached.
- The handler runs after the token is consumed and outside the lock. A handler
  that fails does not refund the token; the operation needs a fresh decision.
- Live checks at redemption cover only the state the gateway was given. A
  gateway constructed without an evidence store, policy provider, or lifecycle
  guard behaves as v0.2.0 did: expiry and explicit revocation only.
- Ordinary evidence increments (a new label on the same evidence record, with
  the partition still valid, unsuspended, and autonomous) do not invalidate an
  outstanding token. See `docs/v2/gateway-redemption.md` for the exact rule.
