# How CI runs here

pytest-quint-connect's CI uses two sister tools.
[CI-esta](https://github.com/gigaverse-app/ci-esta) plans which CI jobs a change can
affect, and handles setup, timing evidence, the required gate and the per-run report.
[pytest-selective](https://github.com/gigaverse-app/pytest-selective) supplies the import
graph and picks the test files inside the `tests` lane.

## Selection

```text
select-tests ──► plan-work (ci.yml's lane conditions + .github/test-lanes.json) ──► lanes: {tests, dogfood, package, routing}
             └─► file selection (same graph, scripts/ci_file_config.py) ──► mode: none | scoped | full, files
                    │
test ×6, minimum-pytest ◄── tests lane, then only the selected test files (scripts/run_ci_tests.py)
build (uv build, twine, release test, wheel smoke), consumer-install ◄── package lane
dogfood ◄── dogfood lane
routing ◄── routing lane (ci_tests on real Git snapshots)
quality, lint-ci ◄── always (lint, format, docs links, gate contract, workflow lint)
Validation gate ◄── every job; a lane job may be skipped only when the planner succeeded and said false
```

- **Lanes.** CI-esta's `plan-work` reads the lanes from `ci.yml`: each is the name a job's
  condition reads (`fromJSON(needs.select-tests.outputs.lanes).dogfood`). A change selects a
  lane through what that lane's jobs run: the tests that import it or declare reading it,
  and the scripts and actions the jobs execute.
    - `tests`: the `tests/` suite, run by `test` and `minimum-pytest`.
    - `dogfood`: what the `dogfood` job runs: `dogfood/`, `examples/lru_cache` (pytest), the
      sticky publisher's `node --test report/publish.test.cjs` (planned from Bun's module
      graph) and the local `./report` action.
    - `package`: what `build` and `consumer-install` run: every file the wheel contains, the
      `README.md` and `LICENSE` its metadata embeds, `tests/test_release.py` and its declared
      reads (the release metadata), and the smoke script and the example it copies.
    - `routing`: `ci_tests/`, and what they read: the workflow, the lane file and the read
      declarations.

    [.github/test-lanes.json](../.github/test-lanes.json) holds only what the workflow
    cannot show: module roots for the top-level test helpers and drivers, the scoped
    conftest pin and the startup floor budget, and two rules:
    - `python-environment` replaces CI-esta's built-in rule so that lockfiles,
      `pyproject.toml` and `pytest.ini` files run every lane, while the Read the Docs
      `documentation/requirements.{in,txt}` run none.
    - `report-action-runs-this-checkout`: the dogfood job's `./report` action installs this
      checkout's package from `github.action_path`, which the workflow graph cannot follow, so
      `src/**`, `README.md` (its contract block is checked) and the sticky banners run
      `dogfood`.

    The built-in `bun-environment` rule is off: no lane runs Bun, and `counter/` has its
    own workflow. A change to the planner job, or to the lane file it reads, runs every lane.
    A documentation-only change (docs pages, other Markdown, `documentation/`, `mkdocs.yml`)
    runs no lane: no tests, no build, no smoke, no Quint, Node or Windows/macOS runner. Only
    `quality`, `lint-ci`, the planner, the gate and the separate Documentation workflow run.
    An edit to `README.md` also rebuilds and checks the package. To see what a change runs
    and why, run `ci-esta lanes --config .github/test-lanes.json --base origin/main`.
- **Files.** Inside the `tests` lane, pytest-selective runs only the test files that
  import, or declare as read, something the change touched. Inputs that tests read
  without importing them are declared in
  [tests/.gv-dependencies.yaml](../tests/.gv-dependencies.yaml). The file graph is the
  lanes' graph (`ci-esta lanes --json`), with the files that decide how tests run
  (`ci.yml`, the runner and config scripts, the lane file) selecting every test file;
  [scripts/ci_file_config.py](../scripts/ci_file_config.py) derives it. Any change to the plugin
  package itself selects every test, because pytest loads the plugin into every session.
- **Failing safe.** If planning fails, every lane and every test runs, and the gate still
  fails on the planner. An empty selection never starts pytest; that would run everything.
- **Full runs.** Pushes to `main` select against the previous `main` commit. Manual
  dispatches, release checks and pull requests labelled `test:full` run everything; the
  label takes effect at the next push.
- **Routing regressions.** [ci_tests/test_routing.py](../ci_tests/test_routing.py) commits
  real changes to a snapshot of this repository. It asserts which lanes and which test
  files each one selects, using the same CI-esta revision as the workflow's `plan-work`,
  including that a documentation-only commit selects nothing. They run in the `routing`
  lane, so a documentation-only change does not run them.

## CI-esta

| Concern | Where | What it does |
| --- | --- | --- |
| Python environments | `setup-cached-uv` in every uv job | installs the locked environment with uv's package cache; the expanded `.venv` is not cached, because this environment installs faster than it restores |
| Test timings | `learn-test-timings` after each `test` matrix cell | keeps the JUnit durations of every run as a `test-timings-PY-OS` artifact |
| Lane reuse | `resolve-lane-evidence` in `select-tests` (`planned-lanes`, `lane-keys`, `admitted-lanes`); a receipt step closes each `test` cell and `minimum-pytest`; `ci-esta check-workflow` in `quality` proves `lane-keys` name every job the lane gates, each matrix cell included | the tests lane is not run again when every one of its seven jobs already passed, on a first attempt, for the same inputs ([.github/validation-inputs.json](../.github/validation-inputs.json)) and the same selected test files: after a squash merge, on a version-only release PR, or on a PR push that changes nothing the tests read. A reused lane emits no new receipt, so proof never outlives the run that earned it. Reuse after a merge needs the `PR evidence #N head SHA` step in `select-tests`: it ties each PR run to its pull request when GitHub leaves that link out |
| Required gate | `Validation gate` job (`validation-gate` with `lanes`/`lane-jobs`) | the one check branch protection requires; `select-tests` reports a fully proven tests lane as not needed, so the gate accepts its skipped jobs |
| Gate contract | `ci-esta check-workflow` in `quality` | fails if a job's lane condition, the gate's `lane-jobs` or the lanes the workflow and lane file define drift apart |
| Workflow lint | `lint-ci-code` | actionlint, action-validator and ShellCheck over every workflow |
| Run report | `observe-ci.yml` (`report.yml@v1`) | after each CI run: gate latency, the critical path through `needs`, step timings, runner usage and pytest-selective's explanation of the selection, as one sticky pull-request comment and a `ci-esta-RUN-ATTEMPT` artifact |

First-attempt pushes to a pull request cancel that pull request's earlier run.
Reruns, `main` and manual runs keep their own concurrency groups.

To see whether a run reused the tests lane, open the CI-esta comment: it lists reused lanes,
and the skipped test jobs carry no receipt step of their own.

**Deferred: timing feedback into planning.** CI-esta can restore learned durations
before planning, to size pytest shards. This suite runs unsharded, so there is nothing
for the timings to size yet. They are collected now, and can be restored once sharding
is worth it.

To check the wiring statically:

```bash
uvx --from ci-esta ci-esta adoption --root .
uv run --no-project --python 3.14 --with "ci-esta[selection]>=1.13.0,<2.0.0" \
  ci-esta check-workflow --workflow .github/workflows/ci.yml --lanes .github/test-lanes.json
```
