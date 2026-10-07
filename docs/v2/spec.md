# v2 specification: authority lifecycle and evidence admission

Status: Step 01 contract. Documentation and fixtures only; no behavior in `src/` implements this yet. Keywords MUST/MUST NOT/SHOULD are normative for later steps. Derived from `authority.py` at baseline `86fcf83` (see `baseline.md`). The machine-readable counterpart is `docs/v2/lifecycle_fixture.json`, checked by `tests/test_v2_spec_fixture.py`.

## 1. Scope and trust boundary (unchanged from v1)

- Models (proposer, executor) produce proposals and requests only. They never grant authority and never reach a handler except through `ExecutionGateway` with a live token.
- The host-owned controller and gateway are trusted (see `threat-model.md`).
- Every operation is evaluated, not only the plan.
- Prototype guarantees (this repo) are in-process and in-memory. Production guarantees (durable store, signed or server-held tokens, external ledger anchor, transactional consume) are stated only as requirements, never as properties of this code.

## 2. Partition identity

A partition is the exact 12-field `PartitionKey` (`authority.py:64`). Evidence MUST NOT be pooled, copied, averaged, or carried across any change to any field. A change to any field, including `policy_version` and `environment_version`, yields a different partition (a *successor*) that starts in `UNESTABLISHED`. The predecessor is flagged `retired`: its evidence is immutable and no proposal can route through it.

**Lineage rule.** A successor of a `SUSPENDED` partition inherits the suspension flag (`lineage_suspended`). The successor cannot take `GATE_MET` until a recorded human release covers the lineage. This prevents clearing a suspension by editing a prompt-version string.

## 3. Evidence provenance

Every evidence record carries a persistent `provenance` and these fields: `partition`, `epoch`, `frozen_at`, `frozen_digest` (digest of the proposal as frozen), `label` (`POSITIVE | NEGATIVE | INCONCLUSIVE`), `oracle_id`, `oracle_version`, `labeled_at`, `reviewer_edit` (bool), `severe` (bool).

| Provenance | Definition | Counts toward the qualification gate | Counts toward successes | Inconclusive |
|---|---|---|---|---|
| `SHADOW` | An autonomous-path proposal frozen (§4) before any human review, labeled by the oracle (§5), never executed because of its label | Yes | `POSITIVE` only | Counts in `n` and in `censored`; never a success |
| `AUTONOMOUS` | A real execution authorized with route `AUTONOMOUS` and no human gate | Yes | `POSITIVE` only | As `SHADOW` |
| `AUDIT` | An autonomous execution that the pre-outcome audit draw also selected for independent labelling. Its frozen form (§4) is recorded before the token is issued and labelled by the oracle afterwards; no human gates it | Yes, labeled on the frozen form | `POSITIVE` only | As `SHADOW`; an audit never labelled expires to `INCONCLUSIVE` |
| `HUMAN_APPROVED` | Any execution that occurred because a human approved it, or whose proposal a human edited | **No** | **No** | Dropped |

Rules:

1. `HUMAN_APPROVED` records never enter autonomy evidence, including approved executions that succeed. They are retained in the ledger for audit only.
2. A `SHADOW` or `AUDIT` record with `reviewer_edit = true`, or whose `frozen_digest` does not match the proposal at labeling, is inadmissible.
3. `severe = true` is a safety signal, not evidence. It applies from any provenance, including `HUMAN_APPROVED`, and is processed before any counting (same as `adjudicate` at baseline).
4. `INCONCLUSIVE` (v1 `PENDING`, `PARTIAL`, `DISPUTED`) counts in `n` and `censored` and never in `successes`. It behaves as a failure for the bound. This is conservative: a missing label can only delay qualification. The censored fraction MUST be reported.
5. Unlabeled frozen trials past the label deadline become `INCONCLUSIVE` automatically. They cannot be silently dropped, because dropping them lets non-response select the sample.
6. Provenance classes are counted and reported separately; the gate input is their sum only when each satisfies rules 1-5.

## 4. Freeze point

The freeze point of a shadow or audit trial is the instant the controller computes `proposal_digest` and appends the pre-review ledger record. After that point the proposal is immutable. A reviewer may add a separate decision record but cannot alter the frozen record or its label. The audit draw (`audit_draw`) MUST be recorded at or before the freeze point and before any outcome exists.

## 5. Adjudication oracle

An oracle is a declared procedure with `oracle_id`, `oracle_version`, and a validity scope. Permitted oracle kinds:

- `CONSTRAINT_CHECK`: deterministic check of the frozen proposal against `success_criteria` and host policy.
- `SANDBOX_REPLAY`: replay of the proposed action in a controlled environment with no real side effects.
- `INDEPENDENT_REVIEW`: a reviewer who sees only the frozen form.

Labels: `POSITIVE` (criteria met), `NEGATIVE` (criteria violated), `INCONCLUSIVE` (oracle could not decide, timed out, or was disputed).

**Estimand.** The evidence supports a claim about *oracle-adjudicated acceptability of proposals*: the probability that a proposal drawn from the partition's proposal process is labeled `POSITIVE` by the named oracle under its validity scope. It does **not** establish real-world autonomous execution success. Equivalence between oracle labels and real outcomes is an unvalidated assumption (A5) and MUST be stated wherever a result is reported.

## 6. Statistical claim (what the gate bounds)

Let $p$ be the partition's per-trial probability of a `POSITIVE` label (non-`POSITIVE` includes `NEGATIVE` and `INCONCLUSIVE`) within one epoch. Let $L_t$ be the lower confidence bound after $t$ admitted trials, and $r$ the policy's required bound for the risk class. The gate claims:

$$\Pr\big(\exists\, t:\ L_t \ge r \ \text{and}\ p < r\big) \le \alpha$$

That is, under assumptions A1-A5, the probability that autonomy is *ever* granted while the true oracle-positive rate is below $r$ is at most $\alpha$. This is a time-uniform (anytime-valid) claim over all stopping times. The baseline Wilson bound at fixed $z$ does not support it (repeated looks); Step 04 selects and verifies a mechanism that does. No claim is made about the first-order rate after qualification, about individual operations, or about adversaries outside A1-A4.

Assumptions (all MUST be stated in any results):

- **A1** Within an epoch and partition, trial labels are independent Bernoulli($p$) (or the mechanism is shown valid under the weaker declared condition).
- **A2** The proposal process is stationary within an epoch. Drift is handled by expiry (`max_evidence_age`) and epoch reset, not by the bound.
- **A3** Shadow, audit, and autonomous trials are drawn from the same proposal process (no selection of which proposals are shadowed or audited by the agent or operator).
- **A4** Censoring is conservative (rule 4), so non-response cannot raise $L_t$.
- **A5** Oracle labels are valid for the claimed estimand within the declared scope.

## 7. Deterministic containment layer (separate from statistics)

These controls hold regardless of A1-A5 and are the only defence against adaptive or malicious behavior: policy-prohibited effects (`DENY`); per-operation evaluation; digest-bound single-use tokens with expiry; live invalidation at redemption (Step 05); host-owned risk envelopes and sequence budgets (Step 06); suspension on any severe failure. The statistical gate MUST NOT be described as defeating an adversary that defects strategically after qualifying; containment limits blast radius, and suspension limits duration.

## 8. Risk scope policy

Risk is host-owned. The model-supplied `risk_class` string in a proposal is a *request* and cannot lower the host-derived class. Arguments (amount, recipient, effect set) and the ordered action sequence are checked against host envelopes independent of the partition's evidence; qualification for a low-risk class grants nothing for a higher class. Detailed envelopes are Step 06.

## 9. Lifecycle states

Initial: none. A partition is created in `UNESTABLISHED` on first proposal.

| State | Entry guard | Allowed routes | Exit |
|---|---|---|---|
| `UNESTABLISHED` | First proposal for the exact key, or successor creation | `HUMAN_APPROVAL`, `REVISION`, `CLARIFICATION`, `DENY` | First admitted trial, or severe failure |
| `QUALIFYING` | At least one admitted trial in the current epoch, gate not met | Same as above | `GATE_MET`, severe failure |
| `AUTONOMOUS` | `GATE_MET`: $n \ge n_{min}$, $L_t \ge r$, evidence valid and fresh, policy version matches, not suspended, `lineage_clear` | `AUTONOMOUS`, `AUDIT` (plus the above) | `GATE_LOST`, expiry, severe failure |
| `SUSPENDED` | Severe failure, or inherited lineage suspension | `HUMAN_APPROVAL`, `DENY` only | `HUMAN_RELEASE` to `QUALIFYING` |

Persistent provenance fields on a partition record: `epoch`, `state`, `retired`, `lineage_suspended`, `suspension_reason`, per-provenance counters (`successes`, `failures`, `censored`), `evidence_version`, `collected_from`, `collected_until`, `policy_version`.

Transitions (full table in the fixture; IDs are stable):

| ID | From | Event | Guard | To | Failure response / note |
|---|---|---|---|---|---|
| T-01 | none | `FIRST_PROPOSAL` | none | `UNESTABLISHED` | Route `HUMAN_APPROVAL`, reason `NO_EXACT_EVIDENCE`. Never autonomous at `n=0` |
| T-02 | `UNESTABLISHED` | `TRIAL_ADMITTED` (POSITIVE/NEGATIVE) | admissible | `QUALIFYING` | Counters updated; the first trial can be accepted without any human approval |
| T-03 | `UNESTABLISHED` | `TRIAL_INCONCLUSIVE` | admissible | `QUALIFYING` | `censored` +1; no success |
| T-04 | `UNESTABLISHED` | `GATE_MET` | $n < n_{min}$ | blocked | No silent path to autonomy |
| T-05 | `UNESTABLISHED` | `HUMAN_APPROVED_OUTCOME` | none | blocked (no state change) | Not admissible evidence (rule 1) |
| T-06 | `QUALIFYING` | `TRIAL_ADMITTED` | gate not met | `QUALIFYING` | Counters updated |
| T-07 | `QUALIFYING` | `GATE_MET` | all entry guards hold | `AUTONOMOUS` | Records evidence version relied on |
| T-08 | `QUALIFYING` | `GATE_MET` | any entry guard fails | blocked | Reason codes added; stays `QUALIFYING` |
| T-09 | `QUALIFYING` | `TRIAL_INCONCLUSIVE` | admissible | `QUALIFYING` | `censored` +1; cannot raise $L_t$ |
| T-10 | `QUALIFYING` | `EVIDENCE_EXPIRED` | age $>$ `max_evidence_age` | `QUALIFYING` | `EVIDENCE_STALE`; fresh trials needed |
| T-11 | `AUTONOMOUS` | `AUTONOMOUS_OUTCOME` or `AUDIT_OUTCOME` (non-severe) | $L_t \ge r$ after update | `AUTONOMOUS` | Counters updated |
| T-12 | `AUTONOMOUS` | `AUTONOMOUS_OUTCOME` or `AUDIT_OUTCOME` (non-severe) | $L_t < r$ after update | `QUALIFYING` | `GATE_LOST`; autonomy withdrawn, evidence kept |
| T-13 | `AUTONOMOUS` | `EVIDENCE_EXPIRED` | age $>$ `max_evidence_age` | `QUALIFYING` | Autonomy withdrawn |
| T-14 | `AUTONOMOUS` | `HUMAN_APPROVED_OUTCOME` | none | `AUTONOMOUS` | Not counted (rule 1) |
| T-15 | any non-suspended | `SEVERE_FAILURE` | any provenance | `SUSPENDED` | Processed before counting; outstanding tokens invalid at redemption (Step 05) |
| T-16 | `SUSPENDED` | `TRIAL_ADMITTED` | admissible | `SUSPENDED` | Recorded, not counted; epoch stays closed |
| T-17 | `SUSPENDED` | `GATE_MET` | none | blocked | Fail closed |
| T-18 | `SUSPENDED` | `HUMAN_RELEASE` | recorded human act, named actor, reason | `QUALIFYING` | `epoch`+1, counters reset to 0; never directly to `AUTONOMOUS` |
| T-19 | any | `POLICY_CHANGE` or `KEY_FIELD_CHURN` | any of the 12 key fields differs | same state, `retired = true` | Successor is `UNESTABLISHED` (T-01); old grants invalid at redemption (Step 05) |
| T-20 | `SUSPENDED` | `KEY_FIELD_CHURN` | successor created | same state, `retired = true` | Successor has `lineage_suspended = true` |
| T-21 | `UNESTABLISHED` | `AUTONOMOUS_OUTCOME` | none | blocked | No autonomous execution exists to adjudicate; unknown partition is an error, not a bootstrap |

## 10. Spec-level invariants for later tests

1. No state transitions to `AUTONOMOUS` except `QUALIFYING` via `GATE_MET` with every guard true.
2. `SUSPENDED` exits only via `HUMAN_RELEASE`, only to `QUALIFYING`, with `epoch` incremented and counters reset.
3. `HUMAN_APPROVED` never changes counters.
4. `INCONCLUSIVE` never increases `successes` and never raises $L_t$.
5. Any `SEVERE_FAILURE` yields `SUSPENDED` regardless of state or provenance.
6. Successor partitions never inherit counters, and inherit suspension.

## 11. Decisions deferred to later steps (open)

- **O-1** Step 04: the specific time-uniform bound and its tuning of $\alpha$, spending across partitions, and `n_min` interplay.
- **O-2** (closed 2026-10-07, owner's decision): `AUDIT` executes unattended and is labelled afterwards. The gateway issues for `AUDIT` without approval; `CrossModelAuthorityLoop.commit` freezes the proposal as `AUDIT` provenance under its decision id before the token exists. v1's `AWAITING_AUDIT` status is removed.
- **O-3** Step 02: oracle validation procedure for A5 and label deadline length.
- **O-4** Step 05: revocation epoch representation and the exact consume linearization.
