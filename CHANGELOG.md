# Changelog

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
