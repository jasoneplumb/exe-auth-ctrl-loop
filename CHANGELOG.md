# Changelog

## [0.3.0] - 2026-10-08

The v2 design: earn authority from zero, gate it with an anytime-valid bound,
and withdraw it at redemption (#10, closing #9). Behaviour in the authority
core **changes**; see "Changed" before upgrading. Developed as ten gated steps
(`docs/v2/gates/`), reviewed over three cycles with every finding addressed,
and evaluated in a seeded simulation whose artifacts reproduce bit-for-bit
(`results/manifest.json`). Still a single-process research prototype: see
`SECURITY.md` and `experiments/README.md` before relying on any of it.

### Added

- Shadow evidence: proposals routed to a human are frozen first and labelled
  by an independent oracle; `SHADOW`, `AUDIT`, and `AUTONOMOUS` provenance
  count toward autonomy, `HUMAN_APPROVED` never does; inconclusive and
  missing labels count as failures and censoring is reported (`shadow.py`,
  `docs/v2/shadow-evidence.md`)
- Lifecycle: `UNESTABLISHED / QUALIFYING / AUTONOMOUS / SUSPENDED` with a
  single transition table, recorded transitions, named-human release into a
  new epoch, retirement on churn, and lineage suspension (`lifecycle.py`,
  `docs/v2/lifecycle.md`, `docs/v2/spec.md`)
- Anytime-valid evidence gate: Beta(1/2,1/2)-mixture confidence sequence
  (`sequential.py`, `docs/v2/statistical-method.md`)
- Redemption-time invalidation: the gateway re-checks policy version,
  prohibited effects, partition suspension and evidence identity, and any
  `RedemptionGuard`; check-and-consume under a lock; informed human approval
  (`docs/v2/gateway-redemption.md`)
- Host-owned risk envelopes, sequence budgets, and conflict rules, enforced at
  decision time and again at redemption; `ToolRegistry` delegates risk
  classification to an attached `RiskPolicy` (`risk.py`,
  `docs/v2/risk-envelopes.md`)
- Decisions committed to the ledger, audit draw included, before any token is
  issued; a failed commit refuses the step; `anchor()`/`verify_anchor()` as a
  demonstration of external anchoring (`ledger.py`, `pipeline.py`,
  `docs/v2/audit-commitment.md`)
- Offline simulated evaluation: fifteen seeded scenarios, two comparators,
  hash-manifested artifacts (`experiments/`, `results/`)
- `docs/v2/threat-model.md`, per-step gate reports in `docs/v2/gates/`, and
  the paper v2 plan in `docs/paper-v2/`

### Changed

- `Policy.estimator` defaults to `"beta_mixture"`; `"wilson_legacy"` restores
  the 0.2.0 bound. The new bound is more conservative at every `n` (60 perfect
  trials to clear 0.90 instead of 40). Treat the upgrade as a policy-version
  change.
- `Route.AUDIT` executes unattended and is labelled afterwards; it is no
  longer a pre-execution human review. `ExecutionStatus.AWAITING_AUDIT` is
  removed.
- `AuthorizationToken` gains a required `partition` field and a
  `revocation_reason`; `ExecutionGateway.revoke` takes a reason.
- `ToolDefinition.risk_class` may be `None` (defer to the registry's
  `RiskPolicy`); a registry with a policy refuses tools that carry their own.
- `RedemptionGuard` takes `(token, proposal)` and has `commit()`.
- `AuthorityController` and `ExecutionGateway` accept guards; `ClaudeExecutionAgent`
  accepts a `commit` hook; `CrossModelAuthorityLoop` accepts a `shadow` log.
- README, SECURITY.md: claims about audit independence and ledger tamper
  evidence narrowed to what is shown.

### Fixed

- `LifecycleManager.record_severe` raised on a partition that was already
  suspended (found by the simulation harness).
- `ShadowEvidenceLog` republishing made old evidence look fresh (Step 02
  defect, fixed in Step 03).
- From review of #10: a declared evidence invalidation could be overwritten by
  a republish of fresh counts; a count-only budget with a metric could raise
  inside the consume lock; `record_severe` on a retired partition created a
  state no call could leave; retirement was enforced only through the
  lifecycle guard (the store is now invalidated too); a parameter-scoped
  budget pooled proposals lacking the parameter; a handler exception escaped
  `ClaudeExecutionAgent.run()` instead of becoming a failed step.

### Documentation

- `examples/denials.py`: the six denial paths, offline (#6)
- README voice and claims framing pass (#8); stale private-repo note removed
  from `CONTRIBUTING.md`; v0.2.0 archive DOI added to `CITATION.cff`

### Note

Archived to Zenodo on release, which mints a version DOI for v0.3.0; it is
added to `CITATION.cff` `identifiers` after the archive exists, as for v0.2.0.
`results/manifest.json` names the branch commit the artifacts were generated
at; it is regenerated at the release commit in a follow-up, with the same
canonical hash.

## [0.2.0] - 2026-08-20

Additive. No behaviour in the authority core changed: `authority.py`,
`executor.py`, `ledger.py`, `pipeline.py`, `providers.py` and `tools.py` are
AST-identical to 0.1.0 once documentation is stripped, so `wilson_lower_bound`,
its `z = 1.96` default, evidence adjudication and capability issuance all mean
exactly what they meant in 0.1.0. Downstream pins may move without re-verifying
policy.

### Added

- MCP protocol binding: signed authority metadata carried on `tools/call`
  `_meta`, with digest binding, verification, and extension-prefix validation
  (`mcp.py`, `docs/mcp-extension.md`, `examples/mcp_demo.py`)
- Software archive DOIs and defensive-publication identifiers (`CITATION.cff`)
- Repo-topic-aligned keywords, trove classifiers, and README badges

### Documentation

- `src/` inline documentation reformed to the intent template throughout

### Note

Archived to Zenodo on release, which mints a version DOI for v0.2.0. The
`doi:` in `CITATION.cff` is the concept DOI (10.5281/zenodo.21983061) and
resolves to the latest version, so it needs no change per release; the v0.2.0
version DOI is added to `identifiers` after the archive exists, since no
release can contain the identifier minted from it.

## [0.1.0] - 2026-08-17

First public release.

### Added

- Cross-model execution-authority control loop package: domain records,
  partitioned evidence store, Wilson lower-bound estimator, policy evaluation,
  single-use capability issuance, and execution gateway (`authority.py`)
- Host-owned tool registry with JSON Schema validation and Anthropic tool
  definitions (`tools.py`)
- OpenAI Structured Output proposal generator (`providers.py`)
- Claude client-tool execution loop with per-operation authorization
  (`executor.py`)
- OpenAI-to-Claude pipeline orchestration with event recording (`pipeline.py`)
- Tamper-evident in-memory event hash chain (`ledger.py`)
- Offline deterministic example and live cross-model example using a harmless
  mock handler
- Acceptance tests covering authority, providers, execution, mutation,
  approval, and ledger behavior
- CI workflow: ruff, mypy, pytest, and offline example on Python 3.10 and 3.13
- Claude review workflow triggered by the `review-requested` label

### Documentation

- README covering architecture, safety invariants, evidence partitioning,
  conservative bound and policy, and the production trust boundary
- SECURITY.md, CONTRIBUTING.md, Apache-2.0 LICENSE, and NOTICE
- CI badge (#1) and Zenodo DOI badge
- CITATION.cff and README links to the published design disclosure
  (Technical Disclosure Commons 11356; DOI 10.5281/zenodo.21894658)
