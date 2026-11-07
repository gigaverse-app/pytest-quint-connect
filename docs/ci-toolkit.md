# How CI runs here

pytest-quint-connect's CI uses two sister tools.
[pytest-selective](https://github.com/gigaverse-app/pytest-selective) decides which
work a change can affect. [CI-esta](https://github.com/gigaverse-app/ci-esta) handles
setup, timing evidence, the required gate and the per-run report.

## Selection

```text
select-tests ──► plan-lanes (.github/test-lanes.json) ──► lanes: {tests, dogfood}
             └─► file selection (same graph) ──► mode: none | scoped | full, files
                    │
test ×6, minimum-pytest ◄── tests lane, then only the selected test files (scripts/run_ci_tests.py)
build (always) ─ wheel smoke step ◄── tests lane;  consumer-install ◄── tests lane
dogfood ◄── dogfood lane
quality, lint-ci ◄── always (lint, docs links, routing regressions, gate contract)
Validation gate ◄── every job; a lane job may be skipped only when the planner succeeded and said false
```

- **Lanes.** [.github/test-lanes.json](../.github/test-lanes.json) maps each lane to its
  inputs. `tests` covers the package, the unit and plugin tests, the example and the
  dogfood files that tests read. `dogfood` covers the package, the example and `dogfood/`.
  Lockfiles, `pyproject.toml`, any pytest configuration and the CI workflow run every lane.
  A prose-only change runs no lane, so no tests.
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
  files each one selects, using the same pytest-selective revision as the workflow.

## CI-esta

| Concern | Where | What it does |
| --- | --- | --- |
| Python environments | `setup-cached-uv` in every uv job | installs the locked environment with uv's package cache; the expanded `.venv` is not cached, because this environment installs faster than it restores |
| Test timings | `learn-test-timings` after each `test` matrix cell | keeps the JUnit durations of every run as a `test-timings-PY-OS` artifact |
| Required gate | `Validation gate` job (`validation-gate` with `lanes`/`lane-jobs`) | the one check branch protection requires |
| Gate contract | `ci-esta check-workflow` in `quality` | fails if a job's lane condition, the gate's `lane-jobs` or the lane file drift apart |
| Workflow lint | `lint-ci-code` | actionlint, action-validator and ShellCheck over every workflow |
| Run report | `observe-ci.yml` (`report.yml@v1`) | after each CI run: gate latency, the critical path through `needs`, step timings, runner usage and pytest-selective's explanation of the selection, as one sticky pull-request comment and a `ci-esta-RUN-ATTEMPT` artifact |

First-attempt pushes to a pull request cancel that pull request's earlier run.
Reruns, `main` and manual runs keep their own concurrency groups.

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
