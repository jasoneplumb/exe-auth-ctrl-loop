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

On `mainline` (v3 Step 04, unreleased) also in scope: any route from the
execution agent, the proposal generator, or the pipeline to a registered
handler other than `ExecutionGateway.execute`, and any way to obtain a token
for a decision that was not committed to the ledger. Out of scope, and stated
as a limit: code running in the same Python process can inspect the
interpreter (`gc.get_objects()`, frames) and reach any object, including the
handler table. Only running the gateway in a separate process closes that;
v3 does not (docs/v3/spec.md O-1).

On `mainline` (v3 Step 05, unreleased) also in scope: any way to obtain a token
without holding a live grant whose scope admits the operation, to delegate a
grant broader or longer-lived than its parent, to use an approval or a release
not signed by an active human principal, to use one approval for two tokens,
or to change a principal, grant, or approval without a ledger event. Out of
scope: anyone holding the host's signing keys, which are HMAC secrets the host
holds (stronger keys are a production requirement).

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
process or in several sharing the database (on `mainline`, v3 Step 03; in the
released 0.3.1, within a single process).

## Known limitations of the prototype gateway

- On `mainline` (v3 Step 03, unreleased), check-and-consume is one SQLite
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
  outstanding token. See `docs/v2/gateway-redemption.md` for the exact rule. On
  `mainline` (v3 Step 06) an increment that takes the bound below policy
  revokes the partition's evidence-based tokens in the label's own
  transaction, and redemption recomputes the gate; the released 0.3.1 sees it
  only at the next decision.
- In the released 0.3.1, `issue()` does not check that a decision came from the
  controller: it accepts any decision object that names the proposal and an
  executing route. On `mainline` (v3 Step 04) it requires the decision's
  committed ledger record.
- In the released 0.3.1, a policy-version change clears a suspension without a
  human release: the successor under the new version starts without the
  lineage flag, because only `on_churn` carries a suspension forward. On
  `mainline` (v3 Step 06) every successor, including a policy-version one,
  inherits its predecessor's suspension through a recorded lineage edge.
