# Contributing to pytest-quint-connect

Use Python 3.11+, [uv](https://docs.astral.sh/uv/) and Node.js 22+ with npm.

```bash
uv sync --locked
uv run pre-commit install
uv run pre-commit run --all-files
uv run pytest
uv build
uv run twine check --strict dist/*
uv run python scripts/smoke_distribution.py dist/pytest_quint_connect-*.whl
```

The first test run installs the plugin's locked Quint into your user cache with
`npm ci`. To use an existing Quint 0.33.1, set `QUINT=/path/to/quint`.

## Tests

| Suite | What it covers | Runs Quint |
| --- | --- | --- |
| `tests/test_itf.py` | ITF decoding, type collisions, real Quint fixtures | no |
| `tests/test_compare.py` | type-directed comparison and difference paths | no |
| `tests/test_contract.py` | manifest validation, historical hash | no |
| `tests/test_replay.py` | engine: mismatches, malformed traces, fresh drivers, cleanup, attestation | no |
| `tests/test_plugin.py` | pytest sessions on the LRU example: cases, JUnit, replay, filtering, receipts, mutants, teardown | yes |
| `tests/test_release.py` | version agreement across pyproject, package, manifest and lock | no |
| `scripts/smoke_distribution.py` | built wheel in a fresh environment, run from outside the checkout | yes |

For a bug, first add a failing test at the boundary where it appears: a decoder
collision in `test_itf.py`, a session behavior with `pytester` in `test_plugin.py`.
ITF fixtures in `tests/data` are real Quint 0.33.1 output; regenerate them with Quint
rather than writing them by hand.

## Changing the Quint version

Update `src/pytest_quint_connect/toolchain/package.json`, run
`npm install --package-lock-only --ignore-scripts` in that directory, update
`DEFAULT_QUINT_VERSION` in `contract.py`, the README badge and the docs, and run the
whole suite and the smoke script. One version is supported at a time.

## Pull requests

Explain the user-visible problem and resulting behavior, list the commands you ran
and their results, and state remaining limits. Use
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for commits
and the PR title (`fix(itf): keep map key types`); the squash commit uses the title.
Release Please turns merged titles into a release PR; see [RELEASING.md](RELEASING.md).
