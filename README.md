# Cross-Model Execution-Authority Control Loop

[![ci](https://github.com/jasoneplumb/exe-auth-ctrl-loop/actions/workflows/ci.yml/badge.svg)](https://github.com/jasoneplumb/exe-auth-ctrl-loop/actions/workflows/ci.yml)
[![Software DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21983061.svg)](https://doi.org/10.5281/zenodo.21983061)
[![Disclosure DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21894658.svg)](https://doi.org/10.5281/zenodo.21894658)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Release](https://img.shields.io/github/v/release/jasoneplumb/exe-auth-ctrl-loop)](https://github.com/jasoneplumb/exe-auth-ctrl-loop/releases)

A prototype that integrates a real OpenAI proposal stage with a real Claude
execution stage while preserving a deterministic, fail-closed authority
boundary.

OpenAI may propose. Claude may request execution. Neither model can grant
authority or directly invoke the registered side-effect handlers. The
host-owned authority controller selects versioned evidence, calculates a
conservative success bound, applies policy, and issues a short-lived,
single-use capability. The execution gateway is the only path to a handler.

> **Status:** research prototype, released under the
> [Apache-2.0 license](LICENSE). See [SECURITY.md](SECURITY.md) and the
> [production trust boundary](#production-trust-boundary) before reusing any
> of this in a real deployment.

The design is published as a timestamped defensive disclosure:
*Evidence-Partitioned Autonomy Authorization for LLM Agents: Single-Use
Capability Tokens, a Selection-Bias Firewall, and Pre-Outcome Committed Audit
Selection in a Cross-Model Execution-Authority Control Loop*
([Technical Disclosure Commons](https://www.tdcommons.org/dpubs_series/11356/) ·
[DOI 10.5281/zenodo.21894658](https://doi.org/10.5281/zenodo.21894658)).
See [CITATION.cff](CITATION.cff) for citation metadata.

## In one minute

**The problem.** When an LLM agent can call a tool that moves money, deletes a
record, or sends a message, the decision to allow that call usually lives in the
same conversation that requested it. A plan approved once becomes a bearer
credential: arguments drift, a second call reuses the first call's blessing, and
the model that asked is also, effectively, the model that answered.

**What this does.** Authority is moved out of the models entirely. A host-owned
controller evaluates each operation immediately before it runs, against
versioned evidence for that exact model/tool/policy partition, and mints a
capability bound to one proposal digest, one tool, one effect set, one short
expiry, one use. The gateway holding that capability is the only path to a
registered handler. Every check is additive and failure-closed: one unmet
condition withholds autonomy, and no weighing overrides it.

**Contribution.** Design, implementation, tests, and disclosure are mine, with
AI-assisted implementation under human review (see
[CONTRIBUTING.md](CONTRIBUTING.md)). The proposal and execution stages call the
OpenAI and Anthropic APIs; the authority controller, evidence partitioning,
capability gateway, ledger, and MCP binding are this repository's own code.

**See it refuse.** No API keys, no network:

```bash
python examples/denials.py
```

```text
sparse evidence              route=human_approval reasons=('EVIDENCE_IMMATURE', 'BOUND_BELOW_POLICY')
stale evidence               route=human_approval reasons=('EVIDENCE_STALE',)
no exact evidence            route=human_approval reasons=('NO_EXACT_EVIDENCE',)
unresolved question          route=clarification  reasons=('UNRESOLVED_QUESTIONS',)
authorized (before edit)     route=autonomous     reasons=('AUTHORITY_SUFFICIENT',)
arguments edited             gateway denied: proposal changed after authorization
first use                    handler ran: [{'order_id': 4815, 'usd': 24.0}]
replayed capability          gateway denied: missing, consumed, or revoked token
```

Six requests, one effect: the single request that was authorized and reached
the gateway unmodified. `python examples/example.py` shows the authorized path
on its own; `python examples/mcp_demo.py` shows the same authorization carried
across an MCP `tools/call`.

### Results

| | |
| --- | --- |
| **Contribution** | Sole author of the controller, gateway, evidence partition, ledger, and MCP binding; AI-assisted implementation with human review. Model providers supply the proposal and execution stages only. |
| **Status** | Research prototype. Not deployed, not hardened, no production users. |
| **Evidence** | Output above, reproduced from `examples/denials.py` on Python 3.14 (macOS). On `mainline`, 417 tests pass offline against fake provider clients (the v0.3.1 release had 212). Fifteen seeded simulation scenarios reproduce bit-for-bit on macOS with Python 3.14. `results/` holds the v0.3.0 artifacts the paper-v2 claims matrix cites, unchanged. `results-v3/` holds the proposed 0.4.0 artifacts: the scenarios through the shipped loop, the statistical families, and the agreement check, each reproduced in three independent runs (`docs/v3/gates/step-10.md`). CI checks only a reduced-size rerun. Design disclosed at [Technical Disclosure Commons](https://www.tdcommons.org/dpubs_series/11356/) and [Zenodo](https://doi.org/10.5281/zenodo.21894658). |
| **Reproduction** | `pip install -e ".[dev]"` then `pytest && python examples/denials.py`; `python -m experiments.run --all --output results` for the simulation. Offline; the live cross-model path needs API keys and is separate. See [docs/paper-v2/reproducibility.md](docs/paper-v2/reproducibility.md). |
| **Limitations** | In-process only: the gateway shares an address space with its caller. On `mainline` (v3 Steps 02–03, unreleased), evidence, the ledger, shadow trials, lifecycle records, budget charges, and tokens are durable in one SQLite file and survive a restart, and a token is consumed by one database transaction, so it redeems at most once across threads, processes on one host, and restarts. In the released 0.3.1 all of this is in memory and consumption is serialized by an in-process lock. On `mainline` (v3 Step 06, unreleased), each event that withdraws authority (suspension, evidence invalidation, gate loss, oracle change, retirement, lineage suspension, policy change, newly prohibited effect, risk-rule change, approval rescission, grant revocation, principal deactivation) revokes the outstanding tokens in its scope in the same transaction, and redemption recomputes the gate from current counts. In the released 0.3.1, only when the gateway is given the live evidence store, policy, and lifecycle is a token issued before a suspension, policy change, evidence invalidation, or loss of autonomy refused at redemption, and a loss of autonomy is seen only after a later decision has recorded it; without them the gateway checks only the token. Human approval and release are authenticated on `mainline` (v3 Step 05, unreleased: a human principal's HMAC signature, keys held by the host) and not in the released 0.3.1. On `mainline` (v3 Step 08, unreleased), the shipped loop freezes every human-routed proposal on a non-autonomous partition as a shadow trial, a trial's provenance is derived from its committed decision, oracle labels are signed by a registered oracle principal and timed by the host, and the ledger chain is keyed by the host; in the released 0.3.1 none of these hold. The simulation harness wires the components differently from the shipped loop; see [experiments/README.md](experiments/README.md), [docs/v2/gateway-redemption.md](docs/v2/gateway-redemption.md), the [claims matrix](docs/paper-v2/claims-matrix.md), and the [production trust boundary](#production-trust-boundary). |

## Architecture

```mermaid
flowchart TD
    A["User intent"] --> B["OpenAI Structured Output"]
    B --> C["Typed proposal bundle"]
    C --> D["Claude execution planner"]
    D --> E["Exact client-tool request"]
    E --> F["Authority controller"]
    F -->|authorized capability, audited or not| G["Execution gateway"]
    F -->|approval / revise / clarify| H["Human control path"]
    G -.->|audit-selected: frozen form labelled afterwards| K["Independent oracle"]
    G --> I["Registered handler"]
    I --> J["Receipt and outcome"]
    J --> F
```

The sequencing is intentional: Claude can reason about the complete bundle, but
every requested operation is evaluated independently immediately before
execution. An earlier plan-level approval is not a bearer credential.

## Implemented model integrations

### OpenAI proposal generation

`OpenAIProposalGenerator` uses `client.responses.parse(...)` with a Pydantic
`ProposalDraftModel`. OpenAI receives a public, read-only tool catalog and
returns one of three states:

- `draft` - incomplete proposal; controller requests revision.
- `needs_clarification` - missing information; controller requests clarification.
- `executable` - every action has exact arguments, effects, preconditions,
  success criteria, assumptions, and unresolved questions.

The host, not OpenAI, resolves tool versions, task categories, risk classes,
and effects from the host's `ToolCatalog` (`ToolRegistry` in 0.3.1). Model-declared
effects must exactly match the catalog. Invalid JSON, unknown tools, invalid parameters, or mismatched
effects fail before authority evaluation.

OpenAI Structured Outputs documentation:
<https://developers.openai.com/api/docs/guides/structured-outputs>

### Claude execution

`ClaudeExecutionAgent` uses Anthropic client tools with `strict: true`. Claude
sees only tools referenced by the proposal bundle. Each call must contain a
`proposal_id`; parallel tool use is disabled so that evidence and authority can
be re-evaluated between effects.

For every Claude tool request, the host verifies:

1. The proposal ID exists in the immutable bundle.
2. The proposal has not already executed.
3. The requested tool exactly matches the proposal.
4. Every argument exactly matches the proposed arguments.
5. Parameters still satisfy the host-owned JSON Schema.
6. Registered effects have not changed.
7. Current evidence and policy authorize this exact operation.
8. The resulting token is current, unrevoked, and unused.

Anthropic client tool documentation:
<https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>

### MCP protocol binding

`exe_auth_ctrl_loop.mcp` carries an authorization with the tool call it authorizes, as
vendor-namespaced metadata under `com.jasoneplumb.exe-auth/` on `tools/call` requests. The
block carries the proposal digest, the evidence-snapshot hash, and the committed audit flag,
and it is signed: `_meta` rides on a request a model asked for, so an unsigned block would be
a claim a server cannot check.

A receiving server recomputes the call digest from the tool name and arguments it actually
received. Edited arguments, a substituted tool, a forged or stripped signature, and an
expired authorization each deny.

On `mainline` (v3 Step 07, unreleased; extension `0.2`) the block also names the one server
it is for (`audience`, signed), and the server spends it at the host before running anything:
a call with effects is redeemed against the host's token table in one transaction, an
effect-free call takes a single-use nonce. A replay, to the same server or another, is
refused, and so is a block whose token a host event has revoked; the host never spends an
MCP-bound token itself. In the released 0.3.1 (extension `0.1`) verification is stateless,
so a block verifies again until it expires, even after its token has been consumed or
revoked. The audit flag also tells the server, before execution, which calls are audited.

See [docs/mcp-extension.md](docs/mcp-extension.md) for the key specification and the
verification order, and `python examples/mcp_demo.py` for a runnable round trip.

## Safety invariants

1. **No capability, no execution.** A registered handler runs only after the gateway redeems a token for that exact proposal. On `mainline` (v3 Step 04, unreleased) handlers live in a `HandlerTable` that only the gateway holds, the gateway runs the handler named by the token it consumed (no caller supplies one), it issues only for a decision committed to the ledger, and the agent and proposer hold a `ToolCatalog` that cannot run anything; a reachability test finds no route to a handler from the agent, proposer, or pipeline except through the gateway. Two limits remain: in one Python process, code that inspects the interpreter (`gc.get_objects()`, frame inspection) can still reach any object, which only running the gateway in its own process closes; and a server holding the shared MCP signing secret could mint a block for another server (per-server keys are a production requirement). In the released 0.3.1 the gateway runs whichever handler its caller passes and `ToolRegistry.execute` is public.
2. **Models do not grant authority.** OpenAI and Claude outputs are untrusted requests.
3. **Exact scope.** A token records the proposal digest, tool, effects, policy version, partition, evidence id and version, and expiry, and is consumed once. Redemption always checks the digest, tool, effects, and expiry. It checks the policy version only when the gateway has a policy provider and the evidence id only when it has an evidence store, and it compares the evidence version only to decide whether a human approval saw the current snapshot. On `mainline` (v3 Steps 03–04, unreleased) a decision gets at most one token, and `issue()` refuses a decision without its committed ledger record. In the released 0.3.1, single use is per token: one decision can be issued several tokens, and because `issue()` does not check that a decision came from the controller, any in-process caller holding one can mint more. One stated exception: a human-approved token binds the evidence snapshot it was approved against only for degradation (suspension, invalidation), not for replacement by a healthy successor record, because a human approval is consent to one operation rather than reliance on a track record (see [docs/v2/gateway-redemption.md](docs/v2/gateway-redemption.md)).
4. **No post-approval mutation.** Changed arguments or tools require a new proposal and decision.
5. **Conservative authority.** Autonomous execution requires the lower bound to clear the applicable risk threshold.
6. **Sparse or stale evidence fails closed.** Missing, immature, invalid, suspended, mismatched, or expired evidence requires approval.
7. **Early proposals cannot execute.** Draft and clarification-needed states never reach Claude.
8. **Severe failures contract immediately.** The exact cross-model partition is suspended before ordinary updating. Exceptions: a retired partition only logs the failure, and a partition on a superseded policy version is retired instead of suspended.
9. **Audit selection precedes outcomes.** The audit draw is realized in the decision, and the executor commits it to the ledger before asking for a token; if the commit fails, the step is refused. The order is the executor's: `issue()` does not itself require a commit. Independence from results holds given a trusted RNG and controller; it is an ordering guarantee, not a proof against a malicious host (see [docs/v2/audit-commitment.md](docs/v2/audit-commitment.md)).
10. **Human-approved outcomes remain distinguishable.** Approval changes the treatment and must not silently inflate autonomous evidence. On `mainline` (v3 Step 08, unreleased) a trial is a committed decision, and its provenance is derived by the host from the decision's route and approval record; no API accepts it as an argument. A label is admitted only if a registered, active oracle principal signed it, the oracle is the one bound to the trial's epoch, the labeller did not also review the trial, and the host received it before the deadline (the labeller's claimed time is recorded and never used). In the released 0.3.1 the provenance is whatever the caller passes when it freezes a trial, and the shadow log trusts it.
11. **Provider diversity is not independence.** End-to-end evidence is collected for the combined pipeline.
12. **Authorization provenance is hash-chained.** `EventLedger` is a hash chain, durable on `mainline` (v3 Step 02) in an append-only table whose triggers refuse UPDATE and DELETE. On `mainline` (v3 Step 08, unreleased) each hash is an HMAC-SHA256 under a key the host passes to `Store(path, ledger_key=...)` and never writes to the file, and `verify()` requires sequence numbers to run without gaps, so someone who can write the database file but does not hold the key cannot edit, insert, or delete an event and re-hash the rest. It still does not detect truncation of the tail, or any rewrite by a key holder: the host, and any code in its process holding the `Store`. The tables counts are read from (trials, labels) are not themselves chained; replaying a verified ledger rebuilds them for comparison. External anchoring would close the truncation gap and is demonstrated, not provided. In the released 0.3.1 the hash is unkeyed SHA-256 and sequence gaps are not checked, so a re-hashed suffix verifies.

## Evidence partition

Each exact partition identifies both model roles:

```text
proposal_provider
× proposal_model_version
× proposal_prompt_version
× execution_provider
× execution_model_version
× execution_prompt_version
× tool_version
× policy_version
× environment_version
× task_category
× confidence_bin
× risk_class
```

Changing the OpenAI model or proposal prompt invalidates proposal and
end-to-end evidence. Changing Claude or its execution prompt invalidates
execution and end-to-end evidence. Tool, policy, and environment changes affect
only matching partitions, unless policy explicitly widens invalidation.

Production deployments should maintain related evidence streams for:

- proposal correctness and material human edits;
- execution fidelity to the authorized proposal; and
- acceptable end-to-end outcomes.

The prototype gates on the exact end-to-end partition.

## Conservative bound and policy

The reference estimator is the lower endpoint of a Beta-mixture confidence
sequence (`Policy.estimator = "beta_mixture"`, `Policy.alpha`), which remains
valid when the evidence is re-read before every operation:

```text
lower_bound = BetaMixtureCS(successes, failures, alpha)
```

See [docs/v2/statistical-method.md](docs/v2/statistical-method.md) for the
proposition, its assumptions (labels i.i.d. within an epoch in the order they
enter the count, no selection of trials, conservative censoring, oracle
validity), and reference vectors. Two of those assumptions are easy to break in
use: latency that depends on the label (failures counted later than successes)
voids the guarantee, and a label still pending before its deadline is outside the
count, so withholding negative labels raises the bound until the deadline passes.
In v0.3.1 the error budget `alpha` restarts with every release and every successor
partition. On `mainline` (v3 Step 09, unreleased) these are measured at 1000
sequences per cell in `results-v3/statistics/` (`experiments/statistics.py`):
at $p = r = 0.90$, negatives labelled 50 trials late give a false-authorization
rate of 0.39 and withheld negatives 1.0, against at most 0.012 when pending trials
count as failures (proved valid in `statistical-method.md` §11, and costly when
positive labels are late too); 100 partitions at per-partition α give 0.26
family-wise. From that table the owner chose (`spec.md` O-3, O-4, O-8), and since issue
#43 the default `Policy` applies them:
- **Pending trials** count as failures until labelled (`pending = "pessimistic"`).
- **Partitions** spend α by registration order (`partition_alpha = "online"`).
- **Epochs** spend their partition's α across releases (`epoch_alpha = "lineage"`).

So one α covers every partition and epoch, and delayed or withheld labels cannot open the gate. The cost: qualification is slower when labels are slow, and slower for later partitions and epochs. The v0.2.0 one-sided Wilson bound is retained as
`estimator = "wilson_legacy"` for comparison only; it is a fixed-sample interval
and exceeds its nominal error rate when read repeatedly. Neither bound protects
against an agent that behaves until it qualifies and then defects; that is the
job of the deterministic controls (per-operation checks, single-use tokens,
suspension), not the statistics.

Autonomous execution requires:

```text
n >= n_min
evidence is current, valid, and unsuspended
lower_bound >= policy.minimum_success_by_risk[risk_class]
```

Policy thresholds must be defined from explicit decision costs. Under the
limited assumptions that autonomous execution costs `(1-s) * C_failure` and
review has fixed cost `C_review`, the break-even condition is:

```text
s >= 1 - C_review / C_failure
```

Real policies may impose a higher safety floor or model more decision
alternatives and outcome-dependent costs.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/exe_auth_ctrl_loop/authority.py` | Domain records, evidence store, policy evaluation with the sequential bound, capability issuance, gateway with redemption-time checks |
| `src/exe_auth_ctrl_loop/shadow.py` | Frozen shadow trials, oracle labels, provenance-separated counts, epochs |
| `src/exe_auth_ctrl_loop/lifecycle.py` | `UNESTABLISHED / QUALIFYING / AUTONOMOUS / SUSPENDED` state machine over shadow evidence |
| `src/exe_auth_ctrl_loop/sequential.py` | Beta-mixture confidence sequence (anytime-valid lower bound); Wilson kept as legacy |
| `src/exe_auth_ctrl_loop/risk.py` | Host-owned risk envelopes, sequence budgets, decision- and redemption-time guards |
| `src/exe_auth_ctrl_loop/tools.py` | Host-owned `ToolCatalog` (JSON Schema validation, risk classification, Anthropic tool definitions) and the gateway-only `HandlerTable` |
| `src/exe_auth_ctrl_loop/providers.py` | OpenAI Structured Output models and proposal generator |
| `src/exe_auth_ctrl_loop/executor.py` | Claude client-tool loop with per-operation authorization and commit-before-act |
| `src/exe_auth_ctrl_loop/pipeline.py` | OpenAI-to-Claude orchestration, ledger commit, audit and shadow freezing |
| `src/exe_auth_ctrl_loop/store.py` | SQLite store for authority state: WAL, `BEGIN IMMEDIATE` transactions, versioned schema, append-only triggers (v3 Step 02) |
| `src/exe_auth_ctrl_loop/ledger.py` | Durable hash chain, keyed by the host (v3 Step 08), with decision commit and an anchoring demonstration |
| `src/exe_auth_ctrl_loop/principals.py` | Principals, host-held HMAC keyring, attenuating grants, signed approvals and releases, permission-change events (v3 Step 05) |
| `src/exe_auth_ctrl_loop/revocation.py` | Synchronous revocation: one transaction per revoking event, policy authority (v3 Step 06) |
| `src/exe_auth_ctrl_loop/mcp.py` | MCP binding: signed authority metadata on `tools/call`; extension 0.2 with server-side redemption (`McpServer`, v3 Step 07) |
| `docs/v2/` | v2 specification, threat model, method note, per-mechanism contracts, gate reports |
| `docs/paper-v2/` | Claims matrix, manuscript and its plan, reproducibility, release plan (v0.3.0) |
| `docs/v3/` | v3 plan, specification, threat-model delta, claims delta for 0.4.0, revocation fixture, gate reports, 0.4.0 release plan |
| `docs/mcp-extension.md` | Key specification for the MCP extension |
| `experiments/` | Simulation harness through the shipped loop (`simulate.py`, `run.py`), the scenario-family driver (`statistics.py`, `families.json`), configs |
| `results/`, `results-v3/` | Hash-manifested outputs: v0.3.0 (cited by the paper-v2 matrix) and the proposed 0.4.0 (scenarios, families, agreement check) |
| `examples/example.py` | Offline deterministic example |
| `examples/mcp_demo.py` | Offline MCP round trip, including the denial paths |
| `examples/live_example.py` | Live OpenAI and Anthropic API example using a harmless mock handler |
| `tests/` | Offline acceptance tests for every mechanism above, including adversarial and concurrency cases |

## Getting started

Requires Python 3.10+.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

### Run offline tests and example

The tests use fake provider clients and never call an external API:

```bash
pytest
python examples/example.py
python examples/denials.py
python examples/mcp_demo.py
```

### Lint and type-check

```bash
ruff check .
mypy src
```

### Run the live cross-model example

Set credentials and explicit model identifiers:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export OPENAI_MODEL="your-pinned-openai-model"
export ANTHROPIC_MODEL="your-pinned-claude-model"
python examples/live_example.py
```

The live example gives both models real API roles but exposes only
`record_refund_request`, a local mock handler that does not move money. Its
seeded evidence is explicitly illustrative and must not be treated as
production evidence.

## Simulated evaluation

`experiments/` drives the real controller, lifecycle, shadow log, risk policy,
and gateway with a synthetic proposal stream from an empty evidence store, and
`results/` holds the seeded, hash-manifested outputs of fifteen scenarios
(stable, never-qualifies, correlated failures, distribution shift, delayed
defection, turnover, audit rates, risk envelopes, censoring, oracle noise, a
suspension/release cycle, and two comparators). It is a simulation: read
[experiments/README.md](experiments/README.md) for what is modelled, where the
adjudication is unrealistically strong, and what the numbers do not show. The
harness behind `results/` wired the components differently from the shipped
loop: it labelled every autonomous execution, committed decisions itself,
froze audits after execution, and ran human-approved executions without the
gateway. The speed at which the gate withdraws autonomy under defection and
shift, and the finding that audit rate changes nothing that executes, depend on
the first of these. On `mainline` (v3 Step 09, unreleased) the harness drives
the shipped `CrossModelAuthorityLoop` with fake model clients and none of these
differences remain. Its outputs, under the default policy (pessimistic pending
and online and lineage α spending, issue #43), are in `results-v3/scenarios/`:
- **Defection:** `delayed_defection` has 76 failures after autonomy, against 20 in
  `results/`, because under autonomy only audits are labelled.
- **Cold start:** it takes a median of 139 steps, against 84.
- **Fast churn:** with configuration changing every 60 steps, no seed qualifies,
  which is the cost of a family-wise α.

`results/` stays the v0.3.0 artifacts the paper-v2 claims matrix cites, unchanged
(owner decision at v3 Step 10); `results-v3/` is the 0.4.0 set of record.

```sh
python -m experiments.run --all --output results
```

## Human approval

When a decision routes to `human_approval`, `ClaudeExecutionAgent.run()`
returns without invoking the handler. On `mainline` (v3 Step 05, unreleased) a
reviewer approves with a signed `Approval`: a registered human principal's HMAC
over the proposal's digest and partition, with an expiry. The caller passes it
to `run(approvals=...)`; the gateway verifies it, records it, and lets it back
exactly one token. An approval for another proposal or partition, an expired or
rescinded one, a reused one, or one signed by anyone but an active human is
refused. A lifecycle release likewise needs a human's signature over the
partition, its current epoch, the reason, and (on `mainline`, issue #41) the
oracle the new epoch is bound to, so the releasing human chooses who labels next. Every token records the principal
that asked and the grant it acted under, traceable to a human's root grant
(`Principals.lineage`). Denials, draft states, and clarification requests cannot
be overridden by approval; they require a corrected proposal or policy change.
In the released 0.3.1 the approval is not authenticated: whoever calls `run()`
with `approved_proposal_ids` is treated as the reviewer, and a release accepts
any non-empty actor name.

On `mainline` (v3 Step 08, unreleased), with a shadow log attached, the loop
freezes every human-routed proposal on a partition that is not `AUTONOMOUS` as a
`SHADOW` trial when it commits the decision, before the run returns to ask for
approval; none is skipped, so no one chooses which proposals become evidence.
An oracle then labels each trial with a `SignedLabel`, and a partition can reach
`AUTONOMOUS` from an empty store through the shipped loop alone. An approved
re-run of a frozen proposal is not counted again. In the released 0.3.1 the loop
does not freeze human-routed proposals; a host must call
`ShadowEvidenceLog.freeze` itself (see
[docs/v2/shadow-evidence.md](docs/v2/shadow-evidence.md)).

An `audit` route is not a human gate. It executes unattended like
`autonomous`; what the pre-outcome draw selects is which executions get an
independent label afterwards. With a shadow log attached to the loop, the
proposal is frozen before the token is issued and the label is bound to that
frozen form (see [docs/v2/audit-commitment.md](docs/v2/audit-commitment.md)).

## Versions and lineage

| Artifact | Identifier | Status |
|---|---|---|
| Software v0.1.0 | commit `f070e01`, DOI 10.5281/zenodo.21983062 | released 2026-08-17 |
| Software v0.2.0 | commit `b266eb7`, DOI 10.5281/zenodo.22730700 | released 2026-09-12 |
| Concept DOI (all versions) | 10.5281/zenodo.21983061 | resolves to the latest release |
| Design disclosure | DOI 10.5281/zenodo.21894658; TDCommons dpubs 11356 | published 2026-08 |
| Paper v1 | manuscript on the v0.2.0 design, August 2026 | not published; retained as written |
| Software v0.3.0 (the v2 design) | tag `v0.3.0` (commit `78bdf9d`, merged as #10), DOI 10.5281/zenodo.23226913; see [docs/paper-v2/release-plan.md](docs/paper-v2/release-plan.md) | released 2026-10-08 |
| Paper v2 | draft in [docs/paper-v2/manuscript.md](docs/paper-v2/manuscript.md), built on the v0.3.0 artifacts | draft; not submitted, no preprint |
| Software v0.3.1 | tag `v0.3.1` (commit `3032a11`), DOI 10.5281/zenodo.23705921; fixes and tests from the v0.3.0 claims audit (see [CHANGELOG.md](CHANGELOG.md)) | released 2026-10-27 |
| Software 0.4.0 (v3) | `mainline`; see [docs/v3/plan.md](docs/v3/plan.md), the claims delta [docs/v3/claims-delta.md](docs/v3/claims-delta.md), the [CHANGELOG](CHANGELOG.md) entry, and the [release plan](docs/v3/release-plan.md) | proposed; not tagged or released |

What v2 changes relative to the v1 design and paper, in one paragraph: a
partition can now earn autonomy from an empty evidence store, through shadow
trials frozen before human review and labelled by an independent oracle; the
evidence gate is an anytime-valid confidence sequence instead of a fixed-sample
bound read at every decision; an issued token is refused at redemption once
its justification is withdrawn; risk class is derived by the host from the
arguments; decisions are committed to the ledger before any token exists;
audited operations execute unattended and are labelled afterwards; and the
design is evaluated in a seeded simulation that reports where it fails. The
claims-to-evidence map is
[docs/paper-v2/claims-matrix.md](docs/paper-v2/claims-matrix.md).

## Production trust boundary

This remains an in-process prototype. A production deployment should:

- place the gateway and credentials in a separate service identity and process;
- prevent both model runtimes from accessing handlers or credentials directly;
- sign capabilities or store opaque capability references server-side;
- consume capabilities transactionally before performing side effects;
- use durable append-only storage and cryptographic tool receipts, and anchor
  the ledger head somewhere the controller cannot write;
- authenticate human approvals with role and scope checks;
- accept at `issue()` only decisions the controller produced, not any decision
  object that names the proposal and an executing route;
- carry a suspension to every successor partition, including one created by a
  policy-version change;
- enforce idempotency and replay protection at each external service;
- isolate Claude code or shell execution in an OS sandbox;
- independently verify outcomes through readback, tests, or service receipts;
- pin dependency and model versions used for every evidence partition; and
- red-team prompt injection, confused-deputy, data poisoning, and
  credential-exfiltration paths.

The core boundary remains unchanged: OpenAI proposes, Claude requests,
deterministic policy authorizes, and the gateway executes.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See
[NOTICE](NOTICE) for attribution.
