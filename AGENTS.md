# Working on pytest-quint-connect

To adopt the plugin in another repository, use
[the portable skill](skills/pytest-quint-connect/SKILL.md). This file is about
working on pytest-quint-connect itself.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup and validation:
  `uv sync --locked`, `uv run pre-commit run --all-files`, `uv run pytest`. Tests
  that run Quint use `QUINT` or the plugin's locked npm install; they fail, never
  skip, when Quint is unavailable.
- **Expectations come only from traces.** The engine and drivers never compute an
  expected state. Do not add a fallback that turns a missing trace, witness, tool or
  observation into a pass or a skip.
- **Only an observed mismatch detects a mutant.** Exceptions while building or
  running a mutant fail its case (`tests/test_plugin.py::test_mutant_setup_error_is_not_detection`).
- **Filtered is not complete.** Keep `partial`, `replay-only` and `running` receipts
  distinct from `passed` (`tests/test_plugin.py` filtered/replay/stale-receipt tests).
- **ITF types stay distinct.** Map keys keep their type; lists, tuples and records
  never compare equal (`tests/test_itf.py`). Add a collision test with any decoder change.
- **Generated code carries types, never behavior.** `codegen` output must stay
  deterministic, `ruff format`-stable and fingerprinted; regenerate
  `examples/lru_cache/lru_cache_driver.py` whenever the generator changes
  (`tests/test_codegen.py::test_committed_example_driver_is_current` fails otherwise).
- Reproduce plugin bugs with `pytester` on a copy of `examples/lru_cache` and real
  Quint, as `tests/test_plugin.py` does. Store new ITF fixtures only as real Quint output.
- Keep [docs/limits.md](docs/limits.md) honest when behavior changes: bounded
  sampling, trusted projections and serial histories are the claim's edges.

## Commits and pull requests

- Use Conventional Commits for commits and PR titles:
  `type(optional-scope): description`. Allowed types: `feat`, `fix`, `perf`,
  `revert`, `docs`, `style`, `refactor`, `test`, `build`, `ci`, `chore`. Mark breaking
  changes with `!` and explain the migration in the PR body.
- Repair an invalid PR title in place; do not close or replace the PR.
- Squash merges use the checked PR title. Release Please derives versions and the
  changelog; do not hand-edit versions. See [RELEASING.md](RELEASING.md).

## GitHub Actions versions

Use published major-version tags for established Actions (for example
`actions/checkout@v7`). Do not replace them with commit hashes unless asked.

## Shared agent instructions

This `AGENTS.md` is the single source for Codex and Claude. `CLAUDE.md` is a tracked
relative symlink to it, managed by [agentsync.toml](agentsync.toml):

```bash
npx --yes @dallay/agentsync@1.52.3 apply
npx --yes @dallay/agentsync@1.52.3 status
```
