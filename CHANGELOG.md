# Changelog

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
