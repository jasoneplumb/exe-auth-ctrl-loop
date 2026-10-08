# v2 threat model

Scope: the lifecycle and evidence contract in `spec.md`. Prototype scope: single process, in-memory. This document states what is trusted, what is not, and what is explicitly unsupported.

## Trust classification

| Component | Classification | Consequence |
|---|---|---|
| Host process (controller, gateway, evidence store, policy) | **Trusted** | Compromise of the host voids every guarantee here |
| Proposer and executor models (OpenAI, Claude) | **Potentially adversarial**, including prompt-injected | Produce proposals and requests only. Cannot set `risk_class`, route, token, label, or frozen digest |
| Tool handlers and adapters | Trusted to run only behind the gateway; their outputs are untrusted data | Output text never grants authority |
| Adjudication oracle and human reviewers | **Fallible** | Labels can be wrong or late. Handled by A5, conservative censoring, and a required scope statement. Not assumed correct |
| Audit RNG | Trusted host randomness | Default `random.SystemRandom`. A seeded RNG (tests, experiments) is deterministic and MUST be labeled so; "independent of outcomes" holds only if the draw is recorded before any outcome and the RNG state is not model-observable |
| Ledger (`EventLedger`) | Hash chain, **in-memory** | Detects in-chain edits. Does not detect truncation, replacement of the whole chain, or tampering by the host. No external anchor |
| Clock | Trusted | Skew affects expiry and staleness |

## Assets and threats

| ID | Threat | Containment (layer) | Residual |
|---|---|---|---|
| TM-1 | Model asserts or forges authority in text | Controller-only routing; tokens issued by gateway (deterministic) | None within trust boundary |
| TM-2 | Argument drift after approval | Digest binding, redeem-time check (deterministic) | Value-level limits need Step 06 envelopes |
| TM-3 | Selective labels: human filtering inflates successes | Provenance rules; `HUMAN_APPROVED` excluded (spec §3) | Reviewer-induced shift in the proposal process (A3) |
| TM-4 | Optional stopping / repeated looks | Time-uniform bound (statistical, Step 04) | Valid only under A1-A5 |
| TM-5 | Cold start used as bypass (human approves everything, then claims history) | `n=0` blocked; only admissible shadow/audit/autonomous evidence counts | Shadow oracle error (A5) |
| TM-6 | Suspension laundering via config churn | Successor inherits `lineage_suspended` (spec §2) | Requires lineage tracking in a durable store |
| TM-7 | Stale or replayed token after suspension or policy change | Redemption-time checks (Step 05) | Not enforced in baseline code (gap 2) |
| TM-8 | Concurrent double redemption | Atomic consume (Step 05) | Not enforced in baseline code |
| TM-9 | Strategic defection after qualification | Per-operation checks, risk envelopes, severe-failure suspension (deterministic) | **Not statistically bounded.** Damage up to envelope and detection delay |
| TM-10 | Adaptive agent biases which proposals are shadowed or audited | Host-owned draw recorded before outcome; A3 | Fails if the agent can influence the draw or the proposal stream composition |
| TM-11 | Label non-response used to steer the sample | Deadline converts missing labels to `INCONCLUSIVE` (spec §3 rule 5) | Reduced power, reported censoring |
| TM-12 | Model lowers its own risk class | Host-derived risk (spec §8) | Host classifier correctness |

## Statistical versus deterministic separation

The statistical gate (spec §6) answers one question: is the oracle-positive rate plausibly at least $r$, with error budget $\alpha$ over all stopping times. It assumes A1-A5. The deterministic layer (spec §7) answers a different question: what can happen when those assumptions fail. Neither substitutes for the other, and a result MUST NOT cite the statistical gate as evidence of robustness against TM-9 or TM-10.

## Unsupported adversaries (explicit non-goals)

- A compromised or malicious host, operator, or reviewer with ledger write access.
- Collusion between the oracle and the agent.
- Side channels leaking audit RNG state to a model.
- Adversaries who can modify the process memory or the gateway's token table.
- Denial of service.
- Real-world outcome validity of oracle labels beyond the declared validity scope.
- Network deployment properties (signed tokens, transactional store); these are production requirements, not prototype properties.

## Prototype versus production guarantees

| Property | Prototype (this repo) | Production requirement |
|---|---|---|
| Token single use | Flag in process; not atomic across threads (baseline) | Transactional consume before handler |
| Evidence store | In-memory, monotonic versions | Durable, versioned, append-only |
| Ledger | In-memory hash chain | External anchor or write-once store |
| Audit randomness | `SystemRandom` default | Auditable CSPRNG with committed seed or draw |
| Suspension lineage | Not implemented | Durable lineage graph |
