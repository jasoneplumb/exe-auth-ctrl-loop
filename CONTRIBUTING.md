# Contributing

Thanks for your interest. This repository is currently private and staged for a
future public release; the workflow below applies either way.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gate

Run all of these from the project root before opening a pull request:

```bash
ruff check .
mypy src
pytest
python examples/example.py
```

CI runs the same commands on Python 3.10 and 3.13; all must pass.

## Ground rules

- **Preserve the safety invariants.** The README lists them. A change that
  weakens an invariant (e.g., widens a capability's scope, lets a model output
  reach a handler without a fresh decision) needs explicit discussion in the PR
  description, not just passing tests.
- **Tests accompany behavior changes.** The suite is offline and deterministic;
  fake provider clients live in `tests/test_providers.py`. Do not add tests
  that call external APIs.
- **Keep examples runnable.** `examples/example.py` must stay offline and
  deterministic; `examples/live_example.py` must never register a handler with
  real side effects.
- **Pin versions in evidence partitions.** Anything that changes a model,
  prompt, tool, policy, or environment version must thread that change through
  `PartitionKey` rather than reusing an existing partition.

## Pull requests

- Branch from `mainline`; keep PRs focused and small.
- Describe what changed and why; link an issue where one exists.
- CI must be green before merge.
