# Reference

## Command-line options

| Option | Meaning |
| --- | --- |
| `--quint PATH` | Quint executable. Also `quint_executable` (ini) or `QUINT` (environment). Default: the locked install. |
| `--quint-artifacts DIR` | Where traces, logs and `results.json` go. Default: `.artifacts/quint-connect` under rootdir (ini `quint_artifacts`). |
| `--quint-require-complete` | Fail the session unless every collected contract test ran every case and passed, and every contract in `quint_required_contracts` was collected. A replay or a `-k`-filtered run always fails it. Use it in CI. |
| `--quint-replay TRACE` | Replay one saved `.itf.json` through exactly one selected contract test instead of generating. Never complete coverage. |
| `--quint-mutant NAME` | With `--quint-replay`: replay through that mutant. |

## `pytest-quint-connect codegen`

```text
pytest-quint-connect codegen MANIFEST [-o OUTPUT] [--class-name NAME] [--quint PATH] [--check]
```

Writes a typed driver base for the contract (default output: `MODULE_driver.py` beside
the manifest). `--check` writes nothing and exits 1 with a diff if the file differs
from what would be generated. Exit 2 means the manifest, model or Quint could not be
used. See [drivers](drivers.md#generated-typed-drivers).

## ini options

| Option | Meaning |
| --- | --- |
| `quint_required_contracts` | Contract names a complete run must include (one per line). |
| `quint_timeout` | Seconds allowed for each Quint command (default 300). |
| `quint_executable`, `quint_artifacts` | As the command-line options. |

## Beside a parallel test suite

Contract tests refuse to run in pytest-xdist workers ([limits](limits.md)). In a
repository whose main suite runs with `-n`, keep contracts in their own directory,
outside the main `testpaths`, and run them as their own CI job:

```bash
pytest -c contracts/quint.ini contracts --quint-require-complete
```

A config passed with `-c` holds the contract's `pythonpath` and
`quint_required_contracts` without adding a `pytest.ini` that collection of the main
suite could pick up. Tools that fingerprint every conventional pytest config file,
such as pytest-selective's collection policy, then see no new policy input.

## Quint installation

Without a configured executable, the plugin copies its bundled `package.json` and
`package-lock.json` (Quint 0.33.1) into a per-user cache and runs
`npm ci --ignore-scripts` once. The cache is `PYTEST_QUINT_CONNECT_CACHE`, else
`%LOCALAPPDATA%\pytest-quint-connect` on Windows or
`$XDG_CACHE_HOME/pytest-quint-connect` (`~/.cache/…`) elsewhere. The reported
`quint --version` must equal the contract's `quint_version`. On Windows, npm's
`quint.cmd` shim is run as `node cli.js`, because `cmd.exe` would reinterpret `|`
and `^` in arguments.

## Cases

| Case id | Passes when |
| --- | --- |
| `witness-NAME` | Every state of that witness's trace matches. |
| `sample-N` | Every state of sampled trace `N` matches. |
| `mutant-NAME` | Some generated trace, replayed through the mutant, mismatches. |
| `coverage` | Every declared action occurs in some generated trace. |
| `replay-FILE` | (replay mode only) Every state of the saved trace matches. |

## Artifacts

`results.json` is rewritten with `status: running` when a session that will run
contracts starts, and finalized at the end:

| `status` | Meaning |
| --- | --- |
| `passed` | Every collected contract test ran every case and passed, and every required contract was collected. |
| `partial` | Nothing failed, but cases were filtered out or a required contract is missing. |
| `failed` | A case failed or errored, or generation failed. |
| `running` | The session did not finish. |

Each contract entry records the manifest, bounds, Quint version, the exact Quint
commands, trace paths, SHA-256 of the manifest, model, historical model and sources,
and per test: outcomes, completeness, steps replayed, imported modules (with
`attest`) and each mutant's detecting trace with its differences. Beside it, each
contract's directory holds the ITF traces, Quint logs and a `mismatch-TEST-CASE.json`
for every failed history. Upload the whole directory from CI, including on failure.

JUnit cases carry `quint_trace`, `quint_seed` and `quint_detected_by` properties.
