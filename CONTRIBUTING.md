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
| `tests/test_codegen.py` | typed driver generation from real `quint compile` IR, argument binding, staleness, mypy drift checks | yes |
| `tests/test_release.py` | version agreement across pyproject, package, manifest and lock | no |
| `scripts/smoke_distribution.py` | built wheel in a fresh environment, run from outside the checkout | yes |
| `dogfood/` | the plugin's own receipts and exit codes, through real pytest sessions | yes |
| `ci_tests/test_routing.py` | which CI lanes and test files a change selects, on real Git snapshots | no |

### Declare what tests read

CI runs only the test files a change can affect ([how CI runs here](docs/ci-toolkit.md)).
A test that reads a file it does not import (an ITF fixture, the example project, a
manifest) must declare it in [tests/.gv-dependencies.yaml](tests/.gv-dependencies.yaml),
or a change to that file will not select the test. Check after adding tests or fixtures:

```bash
SEL="pytest-selective[audit] @ git+https://github.com/gigaverse-app/pytest-selective.git@v1"
uv run --with "$SEL" pytest tests --no-selective -p pytest_selective.impact_audit_plugin --impact-audit-dir .impact-audit
uv run --no-project --with "$SEL" python scripts/ci_file_config.py .impact-audit/config.json
uv run --with "$SEL" pytest-selective audit --config .impact-audit/config.json --records .impact-audit
uv run --no-project --python 3.14 --with "$SEL" python -m unittest discover -s ci_tests -v
```

The audit must report `0 undeclared`. Add a routing case to `ci_tests/test_routing.py`
when you add a lane input or a new kind of fixture.

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

## Duplication

`uv run pre-commit install` installs both the staged dry-lint check and the
`prepare-commit-msg` trailer hook, alongside Ruff and documentation checks.
Put a TypeSafe key in `TYPESAFE_AI_API_KEY`, in the shell or the ignored `.env`. The hook uses
the `agent` preset (TypeSafe's Jev judge), Python 3.13 and the same accepted baseline as CI. Missing judge credentials
fail strict checking. The trailer records duplicates fixed before the first push,
so the PR sticky includes that local prevention credit.

The required judged `dry-lint` check rejects new blocking duplicates, incomplete
judgments and baseline widening. Forks detect candidates without secrets; the
trusted judge workflow assesses their artifacts. CI does not run pre-commit, so the
dedicated required job is where judging and its credentials live.

When a finding appears, inspect both functions. Reuse the existing owner when they
implement one concept, keep the contract's evidence guarantees (generation,
completeness, mutant detection) intact and run the affected tests. For genuinely
distinct concepts, add a narrow `distinct-from` comment and its reason. Do not weaken
thresholds or use blanket ignores to make CI pass.

The PR sticky tracks findings and fixes across pushes. Every default-branch push
can finish folding merged PRs into the repository ledger. The README sign shows
new duplicates prevented separately from old debt cleaned and remaining. Counts
are pairs, not lines or estimated time savings; dismissals never count as cleanup.
The cache-backed ledger starts a new accounting period if evicted.

The weekly/manual **dry-lint audit** retains its reports, then opens or updates one
`dry-lint/ratchet` PR with only the baseline, progress JSON and README metadata.
The inherited `GV_CI_CROSS_REPO_TOKEN` lets that PR run ordinary CI. New debt,
incomplete judging or missing authentication fails visibly. Humans and agents
choose refactors; the audit never edits application code or merges its own PR.

```bash
uv tool install --no-config --python 3.13 'dry-lint @ git+https://github.com/gigaverse-app/dry-lint@v1'
dry-lint sweep --budget-usd 5 --report audit.json --output audit.md
# Review both sides, refactor and test before recording progress.
dry-lint sweep --update --budget-usd 5 --readme README.md --repository gigaverse-app/pytest-quint-connect --repository-id 1449617982
```

Commit the generated metadata with a cleanup, or dispatch the audit after the
cleanup merges. A manual run of the **dry-lint** workflow also calls the same
reusable audit. Ratchet PRs target the selected branch. Never delete and
reinitialize the baseline. No-change audits produce no metadata diff. Keep raw
reports and verdict caches outside Git. The scope uses dry-lint's default
exclusions; changes to detection policy require an explicitly reviewed migration.

See the [full adoption playbook](https://github.com/gigaverse-app/dry-lint/blob/main/docs/adoption-playbook.md)
and [sweep semantics](https://github.com/gigaverse-app/dry-lint/blob/main/docs/sweeps.md).
