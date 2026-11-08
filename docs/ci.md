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
   ├─ upload .artifacts/quint-connect ─► traces, mismatch reports, results.json (also on failure)
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
permissions:
  contents: read
jobs:
  contracts:
    name: Quint contracts
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: '3.13'
      # pytest-quint-connect installs Quint 0.33.1 with npm on first use.
      - uses: actions/setup-node@v7
        with:
          node-version: '24'
      # Where the plugin installs its locked Quint, kept between runs.
      - uses: actions/cache@v5
        with:
          path: ${{ runner.temp }}/quint-cache
          key: quint-${{ runner.os }}-${{ hashFiles('requirements*.txt', 'uv.lock') }}
      - run: python -m pip install -r requirements-dev.txt  # your test dependencies, including pytest-quint-connect
      - name: Check the generated driver against the model
        env:
          PYTEST_QUINT_CONNECT_CACHE: ${{ runner.temp }}/quint-cache
        run: pytest-quint-connect codegen --check tests/contracts/quint-connect.json
      - name: Replay the model's histories against the code
        env:
          PYTEST_QUINT_CONNECT_CACHE: ${{ runner.temp }}/quint-cache
        run: pytest tests/contracts --quint-require-complete --junitxml=.artifacts/quint-connect/junit.xml
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: quint-contracts
          path: .artifacts/quint-connect/
          # upload-artifact skips dot-directories unless told otherwise.
          include-hidden-files: true
          if-no-files-found: warn
```

Adjust the install step and the paths to your project. With uv, install with
`uv sync --locked` and prefix both commands with `uv run`. Without the cache, each run
installs Quint with `npm ci` once; the cache key changes when your lockfile does,
which is when the plugin's pinned Quint can change.

## 3. Make it required

In the repository's branch ruleset (**Settings > Rules > Rulesets**), add the job's
check name, `Quint contracts`, to the required status checks. If your repository
already has one aggregate gate job, add `contracts` to its `needs` instead and keep
the gate as the only required check.

## What fails the check

| Situation | Result |
| --- | --- |
| The code reaches a different state than the model after some step | the case fails with a field-level diff and a `--quint-replay` command |
| A declared mutant is not caught by any history | its `mutant-NAME` case fails: the contract has a blind spot |
| A declared action is never reached by the generated histories | the `coverage` case fails |
| The model changed and the committed driver base was not regenerated | `codegen --check` exits 1 with a diff |
| Quint cannot typecheck the model or generate traces | every case of that contract errors at setup |
| A required contract was not collected, or the run was filtered with `-k` | `--quint-require-complete` fails the session; the receipt says `partial` |

None of these can turn into a skip or a pass. A run that crashes mid-way leaves the
receipt at `running`, never an earlier `passed`.

## What a failed run leaves behind

The uploaded artifact holds what you need to reproduce a failure without rerunning
CI:

- `results.json`, the receipt: status, Quint version and commands, seeds, the
  SHA-256 of the manifest, model and `sources`, and each mutant's detecting history;
- every generated history as an `.itf.json` trace, plus the Quint logs;
- a `mismatch-TEST-CASE.json` for each failed history;
- `junit.xml`, whose cases carry `quint_trace`, `quint_seed` and `quint_detected_by`.

Download the trace and replay it locally:

```bash
pytest tests/contracts/test_cache_contract.py::test_cache --quint-replay path/to/sample-5.itf.json
```

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
