# How CI runs here

pytest-quint-connect's CI uses [CI-esta](https://github.com/gigaverse-app/ci-esta)
for setup, timing evidence, the required gate and the per-run report.

| Concern | Where | What it does |
| --- | --- | --- |
| Python environments | `setup-cached-uv` in every uv job | installs the locked environment with uv's package cache; the expanded `.venv` is not cached, because this environment installs faster than it restores |
| Test timings | `learn-test-timings` after each `test` matrix cell | keeps the JUnit durations of every run as a `test-timings-PY-OS` artifact |
| Required gate | `Validation gate` job (`validation-gate`) | the one check branch protection requires; fails on any missing, failed, cancelled or unexpectedly skipped prerequisite |
| Workflow lint | `lint-ci-code` | actionlint, action-validator and ShellCheck over every workflow |
| Run report | `observe-ci.yml` (`report.yml@v1`) | after each CI run: the gate's latency, the critical path through `needs`, step timings and runner usage, as one sticky comment on the pull request and a `ci-esta-RUN-ATTEMPT` artifact |

First-attempt pushes to a pull request cancel that pull request's earlier run.
Reruns, `main` and manual runs keep their own concurrency groups.

The report runs from the default branch's copy of `observe-ci.yml`, after the CI
run has completed, and reads the run as data without checking out the pull
request. Its numbers are observations of one run: compare matched runs and keep
the run ids before claiming a speed-up.

To check the wiring statically:

```bash
uvx --from ci-esta ci-esta adoption --root .
```
