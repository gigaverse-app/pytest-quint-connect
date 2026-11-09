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
      # The contracts' own dependency set: pytest and pytest-quint-connect>=X.Y.Z,<2 (see below).
      - run: python -m pip install -e '.[contracts]'
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
      - uses: gigaverse-app/pytest-quint-connect/report@v1
        if: ${{ !cancelled() }}
```

Adjust the install step and the paths to your project, and follow its conventions:

- **Its own job, on Python 3.11+.** pytest-quint-connect and the driver base it
  generates need Python 3.11+. Run the contracts as this separate job on one such
  Python, not as an entry in a test matrix, even when the matrix covers older versions.
- **No `needs:`.** Leave the job independent of your other jobs, so an unrelated
  failure never hides the contract's result. If you gate merges on one aggregate job,
  add this job to that gate's `needs` instead (section 3).
- **Action versions and runners.** Use the runner, and the versions or commit-SHA pins,
  your other workflows already use for `actions/checkout`, `setup-python` and `cache`;
  older majors work the same. When you pin a SHA, add its `# vX.Y.Z` comment. Any
  Node.js 22+ works.
- **Cache key.** Hash the file that pins pytest-quint-connect, so the cache changes when
  the pin can: `hashFiles('tox.ini')` when it lives in `tox.ini`, otherwise your
  requirements file, lock file or `pyproject.toml`.
- **Triggers.** Use the triggers your other workflows use. With both `push` and
  `pull_request` on every branch, the job runs twice for each pull-request commit;
  the `push` filter above keeps it to `main`.
- **Permissions.** The job-level block replaces the workflow-level `permissions:` for
  this job. If your workflow already has one, keep it, and give this job `contents: read`
  and `pull-requests: write`, plus anything else it needs from the workflow's block.
- **Its own dependency set.** The plugin loads in every pytest session of the environment
  it is installed in, so install it, with `pytest`, from a dependency group or extra the
  main suite does not use, named `contracts`, with the release you adopted as the floor:
  `pytest-quint-connect>=X.Y.Z,<2`. With uv or PEP 735 groups, `[dependency-groups]
  contracts = [...]` in `pyproject.toml` (not in `default-groups`) and
  `uv sync --locked --group contracts`; with extras, `[project.optional-dependencies]` and
  `pip install -e '.[contracts]'`; otherwise a `requirements-contracts.txt`.
- **The report action by its major tag.** `report@v1` upgrades with every 1.x release.
  It renders with its own copy of pytest-quint-connect, not the one your tests installed,
  and judges a README block another 1.x release wrote by the evidence the block records,
  so a newer action passes the check with a note to refresh the block's layout, never a
  failure. Pin a commit only when your tooling rejects tags (zizmor's `unpinned-uses`, or
  an all-SHA policy CI enforces): `uses: gigaverse-app/pytest-quint-connect/report@<sha>  # vX.Y.Z`,
  the release you installed or newer, with the SHA from
  `gh api repos/gigaverse-app/pytest-quint-connect/commits/vX.Y.Z --jq .sha`.
- **Updaters.** Dependabot and Renovate move `@v1` only to a new major; raise the package's
  `<2` with it. Your dependency updates move the package floor. A SHA pin moves with your
  action updater. If an updater rewrites tags to digests (Renovate's `pinGitHubActionDigests`,
  a custom workflow), that is your SHA policy: keep the pin at the package's release or newer.
- **uv.** Install with `uv sync --locked --group contracts` and prefix both commands with `uv run`.
- **Containers and Nix.** If your jobs run inside a `container:` or a Nix shell
  (`nix develop --command …`), run this job the same way and add Node.js 22+ to that
  environment (`nodejs_24` in the flake's dev shell, or in the image) instead of
  `setup-node`; Python then comes from the same environment, so drop `setup-python` too:

  ```yaml
      steps:
        - uses: actions/checkout@v7
        - uses: cachix/install-nix-action@v31
        - run: nix develop .#contracts --command uv sync --locked --group contracts
        - run: nix develop .#contracts --command uv run pytest-quint-connect codegen --check contracts/cache/quint-connect.json
        - run: >-
            nix develop .#contracts --command uv run pytest -c contracts/quint.ini contracts
            --quint-require-complete --junitxml=contracts/.artifacts/quint-connect/junit.xml
        - uses: gigaverse-app/pytest-quint-connect/report@v1
          if: ${{ !cancelled() }}
  ```
- **Sharing totals.** The report step's `share-totals` input defaults to `true`: on the
  default branch it sends merged pull requests' tallies to the
  [global counter](reference.md#the-global-counter). Set `share-totals: 'false'` to opt out.
- **Forks.** GitHub disables a fork's inherited workflows even when its Actions settings
  allow them. `gh api repos/OWNER/REPO/actions/workflows --jq '.workflows[]|{name,state}'`
  shows `disabled_fork` for each; enable them with `gh workflow enable "<name>" -R OWNER/REPO`
  (or the Actions tab), then close and reopen the pull request to start its runs.

### Run through tox or nox

If tests run through tox, run the contracts as their own tox environment, left out of
`env_list`, and have the CI job call `tox -e contracts`. tox hides the environment from
tests, so pass the cache directory through. With classic tox:

```ini
[testenv:contracts]
deps =
    pytest
    pytest-quint-connect>=X.Y.Z,<2
pass_env = PYTEST_QUINT_CONNECT_CACHE, QUINT
commands =
    pytest-quint-connect codegen --check contracts/cache/quint-connect.json
    pytest -c contracts/quint.ini contracts --quint-require-complete --junitxml=contracts/.artifacts/quint-connect/junit.xml
```

When the other environments use tox-uv's `runner = uv-venv-lock-runner`, install the
`contracts` dependency group from the lock instead of `deps`:

```ini
[testenv:contracts]
runner = uv-venv-lock-runner
dependency_groups = contracts
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
state 3, after fetch(key='a')
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
the README is also your PyPI description, make that link an absolute GitHub URL:
relative links break on PyPI. When the file is missing or has no markers, the step
leaves it alone and adds a warning annotation naming it.

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
