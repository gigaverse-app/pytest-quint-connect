# How CI runs here

pytest-quint-connect's CI uses two sister tools.
[pytest-selective](https://github.com/gigaverse-app/pytest-selective) decides which
work a change can affect. [CI-esta](https://github.com/gigaverse-app/ci-esta) handles
setup, timing evidence, the required gate and the per-run report.

## Selection

```text
select-tests ──► plan-lanes (.github/test-lanes.json) ──► lanes: {tests, dogfood, package, routing}
             └─► file selection (same graph) ──► mode: none | scoped | full, files
                    │
test ×6, minimum-pytest ◄── tests lane, then only the selected test files (scripts/run_ci_tests.py)
build (uv build, twine, release test, wheel smoke), consumer-install ◄── package lane
dogfood ◄── dogfood lane
routing ◄── routing lane (ci_tests on real Git snapshots)
quality, lint-ci ◄── always (lint, format, docs links, gate contract, workflow lint)
Validation gate ◄── every job; a lane job may be skipped only when the planner succeeded and said false
```

- **Lanes.** [.github/test-lanes.json](../.github/test-lanes.json) maps each lane to its
  inputs. Inputs name executable file types (`*.py`, `*.qnt`, `*.json`), never a whole
  directory that also holds a README.
    - `tests`: the package, the unit and plugin tests, the example and the dogfood files that
      tests read.
    - `dogfood`: the package, the example and the dogfood contract. Its root is the one
      dogfood test file rather than `dogfood/`, because pytest-selective selects a lane for
      any changed file under its roots, including `dogfood/README.md`. A new `dogfood/test_*.py`
      is therefore unmapped until it is added to the roots, and planning falls back to every lane.
    - `package`: what `uv build`, `twine check`, the release test and the wheel smoke read: the
      package, `README.md` and `LICENSE` (pyproject embeds both in the distribution), the
      release metadata, the smoke script and the example it runs.
    - `routing`: the routing regressions, their helper and the read declarations.

    Lockfiles, `pyproject.toml`, `pytest.ini` files and the CI workflow and its scripts run
    every lane. `graph.global_inputs` lists these environment files explicitly; otherwise
    pytest-selective's built-in policy would treat the Read the Docs
    `documentation/requirements.{in,txt}` as test dependencies.
    A documentation-only change (docs pages, other Markdown, `documentation/`, `mkdocs.yml`)
    runs no lane: no tests, no build, no smoke. Only `quality`, `lint-ci`, the planner, the
    gate and the separate Documentation workflow run. An edit to `README.md` also rebuilds
    and checks the package.
- **Files.** Inside the `tests` lane, pytest-selective runs only the test files that
  import, or declare as read, something the change touched. Inputs that tests read
  without importing them are declared in
  [tests/.gv-dependencies.yaml](../tests/.gv-dependencies.yaml). Any change to the plugin
  package itself selects every test, because pytest loads the plugin into every session.
- **Failing safe.** If planning fails, every lane and every test runs, and the gate still
  fails on the planner. An empty selection never starts pytest; that would run everything.
- **Full runs.** Pushes to `main` select against the previous `main` commit. Manual
  dispatches, release checks and pull requests labelled `test:full` run everything; the
  label takes effect at the next push.
- **Routing regressions.** [ci_tests/test_routing.py](../ci_tests/test_routing.py) commits
  real changes to a snapshot of this repository. It asserts which lanes and which test
  files each one selects, using the same pytest-selective revision as the workflow,
  including that a documentation-only commit selects nothing. They run in the `routing`
  lane, so a documentation-only change does not run them.

## CI-esta

| Concern | Where | What it does |
| --- | --- | --- |
| Python environments | `setup-cached-uv` in every uv job | installs the locked environment with uv's package cache; the expanded `.venv` is not cached, because this environment installs faster than it restores |
| Test timings | `learn-test-timings` after each `test` matrix cell | keeps the JUnit durations of every run as a `test-timings-PY-OS` artifact |
| Lane reuse | `resolve-lane-evidence` in `select-tests`; a receipt step closes each `test` cell and `minimum-pytest` | the tests lane is not run again when every one of its seven jobs already passed, on a first attempt, for the same inputs ([.github/validation-inputs.json](../.github/validation-inputs.json)) and the same selected test files: after a squash merge, on a version-only release PR, or on a PR push that changes nothing the tests read. A reused lane emits no new receipt, so proof never outlives the run that earned it |
| Required gate | `Validation gate` job (`validation-gate` with `lanes`/`lane-jobs`) | the one check branch protection requires; `select-tests` reports a fully proven tests lane as not needed, so the gate accepts its skipped jobs |
| Gate contract | `ci-esta check-workflow` in `quality` | fails if a job's lane condition, the gate's `lane-jobs` or the lane file drift apart |
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
uv run --no-project --python 3.14 --with "ci-esta>=1.1.0,<2.0.0" \
  ci-esta check-workflow --workflow .github/workflows/ci.yml --lanes .github/test-lanes.json
```
