# Run contracts in CI

A contract earns its keep when every pull request must pass it. This page sets up a
required GitHub Actions check: what it runs, what makes it fail, and what it keeps
when it does. The [tutorial](tutorial.md) builds the contract it runs.

```text
pull request
   │
   ├─ codegen --check ───────────► the committed driver base still matches the model
   │
   ├─ pytest --quint-require-complete
   │     Quint regenerates the histories (fixed seed) ─► replay each against the code
   │     every witness, sample and coverage case passes
   │     every mutant is caught
   │     every contract in quint_required_contracts ran, unfiltered
   │
   ├─ report action (on failure too) ► one PR comment: what ran, what failed, how to reproduce it
   │                                ├► artifact: traces, mismatch reports, results.json
   │                                └► README block still matches the evidence
   │
   └─ required check in the branch ruleset ─► merge blocked unless all of the above passed
```

## 1. Name the required contracts

In the pytest configuration that collects the contracts:

```ini
[pytest]
quint_required_contracts =
    lru-cache
```

Without this line, a run that never collects a contract (a moved file, a renamed
test) could still finish green. With it, `--quint-require-complete` fails unless
each named contract was collected and ran every case.

## 2. Add the job

```yaml
name: Contracts
on:
  pull_request:
  push:
    branches: [main]
jobs:
  contracts:
    name: Quint contracts
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write  # the report's PR comment
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: '3.13'
      # Any Node.js 22+. pytest-quint-connect installs Quint 0.33.1 with npm on first use.
      - uses: actions/setup-node@v7
        with:
          node-version: '24'
      # Where the plugin installs its locked Quint, kept between runs. pyproject.toml makes the key
      # match at least one file; the rest change when your pinned pytest-quint-connect can.
      - uses: actions/cache@v6
        with:
          path: ${{ runner.temp }}/quint-cache
          key: quint-${{ runner.os }}-${{ hashFiles('**/requirements*.txt', '**/uv.lock', '**/poetry.lock', '**/pyproject.toml') }}
      - run: python -m pip install -r requirements-dev.txt  # your test dependencies, including pytest-quint-connect
      - name: Check the generated driver against the model
        env:
          PYTEST_QUINT_CONNECT_CACHE: ${{ runner.temp }}/quint-cache
        run: pytest-quint-connect codegen --check contracts/cache/quint-connect.json
      - name: Replay the model's histories against the code
        env:
          PYTEST_QUINT_CONNECT_CACHE: ${{ runner.temp }}/quint-cache
        # The JUnit file goes beside results.json, in the directory the report step uploads.
        run: >-
          pytest -c contracts/quint.ini contracts --quint-require-complete
          --junitxml=contracts/.artifacts/quint-connect/junit.xml
      # Comments on the pull request, uploads every receipt's directory and checks the README block.
      # vX.Y.Z: the version of pytest-quint-connect you installed (see below).
      - uses: gigaverse-app/pytest-quint-connect/report@vX.Y.Z
        if: ${{ !cancelled() }}
```

Adjust the install step and the paths to your project, and follow its conventions:

- **Triggers.** Use the triggers your other workflows use. With both `push` and
  `pull_request` on every branch, the job runs twice for each pull-request commit;
  the `push` filter above keeps it to `main`.
- **Permissions.** The job-level block replaces the workflow-level `permissions:` for
  this job. If your workflow already has one, keep it, and give this job `contents: read`
  and `pull-requests: write`, plus anything else it needs from the workflow's block.
- **Pin the report action to the installed version.** The action renders the report
  with its own copy of pytest-quint-connect, not the one your tests installed, so a
  different version can render the README block differently and fail the check. Pin
  it to the release you installed and bump both together. If your workflows pin
  Actions to commit SHAs, write
  `uses: gigaverse-app/pytest-quint-connect/report@<sha>  # vX.Y.Z`, with the SHA from
  `gh api repos/gigaverse-app/pytest-quint-connect/commits/vX.Y.Z --jq .sha`.
- **uv.** Install with `uv sync --locked` and prefix both commands with `uv run`.

### Run through tox or nox

If tests run through tox, run the contracts as their own tox environment, and have
the CI job call `tox -e contracts`. tox hides the environment from tests, so pass the
cache directory through, and pin the plugin to the same version as the report action:

```ini
[testenv:contracts]
deps =
    pytest
    pytest-quint-connect==X.Y.Z
pass_env = PYTEST_QUINT_CONNECT_CACHE, QUINT
commands =
    pytest-quint-connect codegen --check contracts/cache/quint-connect.json
    pytest -c contracts/quint.ini contracts --quint-require-complete --junitxml=contracts/.artifacts/quint-connect/junit.xml
```

tox installs your package non-editably, so the tests import the installed copy unless
`contracts/quint.ini` puts the source directory first on `pythonpath`. Add the
manifest's [`attest`](manifest.md) to prove which copy ran. nox passes the environment
through unless a session restricts it.

Without the cache, each run installs Quint with `npm ci` once. The plugin keeps each
Quint pin in its own directory under the cache, so a stale cache costs one install,
never a wrong Quint.

### Keep contracts out of the main suite

The layout above keeps each contract in `contracts/NAME/` with one `contracts/quint.ini`
that is used only through `-c`. Make sure your main test command does not collect
`contracts/`: set `testpaths` in its configuration (for example `testpaths = tests`),
or add `--ignore=contracts`. Otherwise the main run collects the contract tests
without their configuration, and they fail to import. A repository with no pytest
configuration at all collects everything, so give it one in the file it already uses
for tool settings, such as `[tool.pytest.ini_options]` in `pyproject.toml`, `[pytest]`
in `tox.ini`, or `[tool:pytest]` in `setup.cfg`. Add `.artifacts/` to `.gitignore`.
If the repository type-checks its tests, install `pytest` and `pytest-quint-connect`
where the type checker runs, because the driver imports both. `-c` also makes `contracts/`
the rootdir: node ids, the artifacts directory and the printed `reproduce:` commands
are relative to it or say so.

## 3. Make it required

In the repository's branch ruleset (**Settings > Rules > Rulesets**), add the job's
check name, `Quint contracts`, to the required status checks. If your repository
already has one aggregate gate job, add `contracts` to its `needs` instead and keep
the gate as the only required check.

## 4. Show results on the pull request and in the README

The report step needs no inputs. On a same-repository pull request it keeps one
comment up to date: a verdict, a header of the key numbers (contracts, cases passed,
mutants caught, real bugs found, disagreements in this pull request), and, for each
failure, the state and action where the code diverged, the differing fields and the
command that reproduces it locally:

```text
### ❌ Quint contracts: 1 test failed, 1 passed
1 contracts · 30/34 cases passed · 2/2 mutants caught · 0 real bugs found · 4 disagreements in this PR
lru-cache · witness-evictsLeastRecentlyUsedTest: observed state differs from the model at
state 3, after fetch(key='a', value=0)
- entries[0].key: values differ; expected: 'b'; actual: 'a'

cd examples/lru_cache
pytest 'test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]'
```

To show the contracts in your README, add the two marker lines where the block
belongs, fill it once from a local run, and commit it:

```bash
printf '<!-- pytest-quint-connect:start -->
<!-- pytest-quint-connect:end -->
' >> README.md
pytest -c contracts/quint.ini contracts --quint-require-complete
pytest-quint-connect report '**/.artifacts/quint-connect/results.json' --readme README.md
```

The block is Markdown and HTML. In a `README.rst` or another format the markers would
show as text, so keep the block in a Markdown file such as `contracts/README.md`, link
it from your README, and point the step at it with `readme: contracts/README.md`. When
the file is missing or has no markers, the step leaves it alone and adds a warning
annotation naming it.

From then on the step fails when the block no longer matches the receipts, and
prints the same command to refresh it, pinned to the renderer version that ran. A run
that skipped a contract the block lists cannot judge it, and says so instead of failing.
The block records a fingerprint of the evidence it shows, so a release that only lays
the same evidence out differently passes with a note to refresh, never a failure. Fork pull requests cannot be
commented on with their own token; their report is in the job summary and the
artifact. See [the action's inputs](reference.md#github-action).

When CI also runs a teaching example or a smoke-test contract, report only your real
contracts: set the step's `receipts` input to their receipts, one per line, and refresh
the block from the same receipts. This repository reports its dogfood contract and not
the LRU example it runs as a smoke test:

```yaml
- uses: gigaverse-app/pytest-quint-connect/report@v1
  if: ${{ !cancelled() }}
  with:
    receipts: dogfood/.artifacts/quint-connect/results.json
```

## What fails the check

| Situation | Result |
| --- | --- |
| The code reaches a different state than the model after some step | the case fails with a field-level diff and a `--quint-replay` command |
| A declared mutant is not caught by any history | its `mutant-NAME` case fails: the contract has a blind spot |
| A declared action is never reached by the generated histories | the `coverage` case fails |
| The model changed and the committed driver base was not regenerated | `codegen --check` exits 1 with a diff |
| Quint cannot typecheck the model or generate traces | every case of that contract errors at setup |
| A required contract was not collected, or the run was filtered with `-k` | `--quint-require-complete` fails the session; the receipt says `partial` |
| The README contract block no longer matches complete receipts | the report step fails with a diff and the refresh command |

None of these can turn into a skip or a pass. A run that crashes mid-way leaves the
receipt at `running`, never an earlier `passed`.

## What a failed run leaves behind

The uploaded artifact holds what you need to reproduce a failure without rerunning
CI:

- `results.json`, the receipt: status, Quint version and commands, seeds, the
  SHA-256 of the manifest, model and `sources`, each mutant's detecting history, and
  the manifest's actions and invariant with the first witness step by step;
- every generated history as an `.itf.json` trace, plus the Quint logs;
- a `mismatch-TEST-CASE.json` for each failed history;
- `junit.xml`, whose cases carry `quint_trace`, `quint_seed` and `quint_detected_by`.

Download the trace and replay it locally:

```bash
pytest -c contracts/quint.ini contracts/cache/test_cache_contract.py::test_cache --quint-replay path/to/sample-5.itf.json
```

A replay rewrites `results.json` with status `replay-only`. Rerun the whole contract
before you refresh the README block or trust the receipt again.

## Repositories with a parallel test suite

Contract tests refuse to run under pytest-xdist. If your main suite runs with `-n`,
keep the contracts in their own directory and configuration and run them as this
separate job: `pytest -c contracts/quint.ini contracts --quint-require-complete`
([details](reference.md#beside-a-parallel-test-suite)). Deselect them from the
parallel run with `-m "not quint_contract"` if they share a directory.

## A real example

[pytest-selective's selection contract](https://github.com/gigaverse-app/pytest-selective/tree/main/contracts/selection)
runs as its own job, `Selection contract (Quint)`, feeding that repository's
required gate. Each step in its histories is a real Git commit, and the contract
checks which tests the selector picks. It runs `codegen --check`, then
`pytest -c contracts/selection/quint.ini contracts/selection --quint-require-complete`,
and uploads `contracts/selection/.artifacts/quint-connect/` on every run.
