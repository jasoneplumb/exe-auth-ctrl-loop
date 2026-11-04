# Security Policy

This project is a prototype of a cross-model execution-authority control loop.
The authority boundary it demonstrates is real, but the implementation is
in-process and illustrative. Read the "Production trust boundary" section of the
README before deploying any part of this code; the prototype does not provide
process isolation, signed capabilities, durable audit storage, or authenticated
human approvals.

## Supported versions

Only the latest commit on `mainline` is supported. There are no maintenance
branches. `research/v2` is an integration branch for the next release and is
supported once merged.

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

Since 0.4.0 (v3 Step 04) also in scope: any route from the
execution agent, the proposal generator, or the pipeline to a registered
handler other than `ExecutionGateway.execute`, and any way to obtain a token
for a decision that was not committed to the ledger. Out of scope, and stated
as a limit: code running in the same Python process can inspect the
interpreter (`gc.get_objects()`, frames) and reach any object, including the
handler table. Only running the gateway in a separate process closes that;
v3 does not (docs/v3/spec.md O-1).

Since 0.4.0 (v3 Step 05) also in scope: any way to obtain a token
without holding a live grant whose scope admits the operation, to delegate a
grant broader or longer-lived than its parent, to use an approval or a release
not signed by an active human principal, to use one approval for two tokens,
or to change a principal, grant, or approval without a ledger event. Out of
scope: anyone holding the host's signing keys, which are HMAC secrets the host
holds (stronger keys are a production requirement).

Since 0.4.0 (v3 Step 07; MCP extension `0.2`) also in scope: any
way for an MCP server to run a call twice on one token, to accept a block
addressed to another server, to accept a block whose token was revoked, or for
the host to spend an MCP-bound token itself. Out of scope, and stated as a
limit: one shared secret signs blocks for every server, so a server holding it
could mint a block for another; per-server keys are a production requirement.

Since 0.4.0 (v3 Step 08) also in scope: any way to freeze a trial
with a provenance other than the one its committed decision gives, to skip
freezing a human-routed proposal on a non-autonomous partition, to count one
proposal twice in an epoch, to admit a label not signed by an active oracle
principal, from an oracle other than the one bound to the trial's epoch, from
the principal that reviewed the trial, or received after the deadline (whatever
time the labeller claims), and, for someone who can write the database file but
does not hold the ledger key, any way to edit, insert, or delete a ledger event
and have `verify()` still pass. Out of scope, and stated as limits: the ledger
key is held by the `Store`, so the host and any code in its process can append
through the ledger API or re-hash a rewritten chain; `verify()` does not detect
truncation of the tail without an anchor held outside the host; and the tables
counts are read from are not chained (a replay from a verified ledger rebuilds
them for comparison, and nothing compares them automatically).

Since 0.4.0 (issue #43) also in scope, under the default policy:

- any way for a delayed or withheld label to raise a partition's bound;
- any way for a partition or an epoch to be gated with more than the α its `alpha_ledger` row records;
- any way for a snapshot to loosen the α the gate applies.

Out of scope, and stated as limits: a labeller who delays or withholds positive labels can delay or block qualification (liveness, not safety), and later-registered partitions and later epochs need more evidence by design.

Also in scope: any way for a human review to create, alter, discard, or relabel
a frozen shadow trial; any path to `AUTONOMOUS` other than `QUALIFYING` on a
met gate; any way to clear a suspension without a recorded human release; any
way for a model-supplied risk label, confidence, or argument to obtain a
cheaper risk class than the host's envelopes derive; and any way to run a
handler before the decision is committed to the ledger.

Also in scope: any way to redeem a token after its partition was suspended,
its evidence invalidated, its policy version replaced, or its lifecycle state
left `AUTONOMOUS`, when the gateway was constructed with that live state; and
any way for two redemptions of one token to reach a handler twice, in one
process or in several sharing the database (since 0.4.0, v3 Step 03; in 0.3.1,
within a single process).

## Known limitations of the prototype gateway

- Since 0.4.0 (v3 Step 03), check-and-consume is one SQLite
  `BEGIN IMMEDIATE` transaction whose single-use guard is a conditional update,
  so it holds across threads, processes on one host, and restarts. It makes no
  claim across hosts. The released 0.3.1 serializes it with a `threading.Lock`
  and makes no claim beyond one process.
- The handler runs after the consume commits. A handler that fails does not
  refund the token; the operation needs a fresh decision. A process that stops
  inside a handler leaves the token in `handler_started`, which
  `ExecutionGateway.effect_unknown()` reports and nothing re-runs.
- Live checks at redemption cover only the state the gateway was given. A
  gateway constructed without an evidence store, policy provider, or lifecycle
  guard behaves as v0.2.0 did: expiry and explicit revocation only.
- Ordinary evidence increments (a new label on the same evidence record, with
  the partition still valid, unsuspended, and autonomous) do not invalidate an
  outstanding token. See `docs/v2/gateway-redemption.md` for the exact rule. Since
  0.4.0 (v3 Step 06) an increment that takes the bound below policy
  revokes the partition's evidence-based tokens in the label's own
  transaction, and redemption recomputes the gate; 0.3.1 sees it only at the
  next decision.
- In 0.3.1, `issue()` does not check that a decision came from the
  controller: it accepts any decision object that names the proposal and an
  executing route. Since 0.4.0 (v3 Step 04) it requires the decision's
  committed ledger record.
- In 0.3.1, a policy-version change clears a suspension without a
  human release: the successor under the new version starts without the
  lineage flag, because only `on_churn` carries a suspension forward. Since
  0.4.0 (v3 Step 06) every successor, including a policy-version one,
  inherits its predecessor's suspension through a recorded lineage edge.
