# Reference

## Command-line options

| Option | Meaning |
| --- | --- |
| `--quint PATH` | Quint executable. Also `quint_executable` (ini) or `QUINT` (environment). Default: the locked install. |
| `--quint-artifacts DIR` | Where traces, logs and `results.json` go. Default: `.artifacts/quint-connect` under rootdir (ini `quint_artifacts`). |
| `--quint-require-complete` | Fail the session unless every collected contract test ran every case and passed, and every contract in `quint_required_contracts` was collected. A replay or a `-k`-filtered run always fails it. Use it in CI. |
| `--quint-replay TRACE` | Replay one saved `.itf.json` through exactly one selected contract test instead of generating. Never complete coverage. |
| `--quint-mutant NAME` | With `--quint-replay`: replay through that mutant. |

## `pytest-quint-connect typecheck`

```text
pytest-quint-connect typecheck MANIFEST [--quint PATH]
```

Typechecks the contract's model with its pinned Quint (the locked install, `--quint` or
`QUINT`), so `quint` need not be on `PATH`. Exit 2 shows Quint's diagnostic, with the
file and line. Run it while writing a model, before `codegen`.

## `pytest-quint-connect codegen`

```text
pytest-quint-connect codegen MANIFEST [-o OUTPUT] [--class-name NAME] [--quint PATH] [--check]
```

Writes a typed driver base for the contract (default output: `MODULE_driver.py` beside
the manifest, where `MODULE` is the manifest's `module`). Prefer the default: then
`codegen --check MANIFEST` in CI compares the same file without repeating `-o`.
`--check` writes nothing and exits 1 with a diff if the file differs from what would be
generated, or with the path it looked for when that file does not exist. Exit 2 means
the manifest, model or Quint could not be used. See [drivers](drivers.md#generated-typed-drivers).

## `pytest-quint-connect report`

```text
pytest-quint-connect report RECEIPT... [--comment PATH] [--readme PATH [--check]]
    [--title TEXT] [--head SHA] [--run-url URL] [--repository-root DIR]
    [--status-file PATH] [--artifact-dir DIR]
```

Reads `results.json` receipts (files or quoted recursive globs, such as
`'**/.artifacts/quint-connect/results.json'`) and renders them. It only reads receipts;
it never runs Quint or imports the project. Receipts under a plugin `_work` directory are
skipped: they belong to sessions a driver ran inside one history.

- **Comment**: one table row per contract test (histories, cases passed, mutants caught,
  actions covered), then each failure with its state, action and differences, and a
  `pytest 'NODE[CASE]'` command that reproduces it. The command regenerates the same
  histories from the manifest's seed. Statuses stay distinct: `failed`, `interrupted`
  (the session never finished), `partial` (filtered), `not run` (collected, nothing
  selected), `replay-only` and `passed`.
- **README block**: the same table and a badge, between the lines
  `<!-- pytest-quint-connect:start -->` and `<!-- pytest-quint-connect:end -->`, which the
  document must already contain. Text outside them is never touched. The block holds no
  commit, run or failure detail, so it changes only when the evidence changes.
  `--readme` writes it; `--readme --check` writes nothing and exits 1 with a diff and the
  command that refreshes it. When this run lacks the evidence to judge the block (a
  contract it lists did not run, or a session was not complete), the check says it was
  not checked and exits 0.

Exit 2 means a file is not a receipt or the document's markers are malformed.

## GitHub Action

Add one step after the step that runs pytest:

```yaml
permissions:
  contents: read
  pull-requests: write
steps:
  # ... checkout, Node.js 22+, Python, then:
  - run: pytest --quint-require-complete
  - uses: gigaverse-app/pytest-quint-connect/report@v1
    if: ${{ !cancelled() }}
```

It finds every receipt in the workspace, writes the comment to the job summary, uploads
each receipt's directory (traces, logs, mismatches) as the `quint-connect-KEY-ATTEMPT`
artifact, creates or updates one comment on same-repository pull requests, and fails the
step when `README.md`'s block is stale. When the `readme` file does not exist or has no
markers, the step adds a warning annotation naming the file and leaves it alone. The
block is Markdown and HTML: next to a `README.rst`, keep it in a Markdown file such as
`contracts/README.md` and set `readme` to it.

| Input | Default | |
| --- | --- | --- |
| `receipts` | `**/.artifacts/quint-connect/results.json` | files or globs, one per line |
| `readme` | `README.md` | document holding the block |
| `readme-mode` | `check` | `check`, `write` (rewrite in the workspace; commit it yourself) or `off` |
| `post-comment` | `true` | comment on same-repository pull requests |
| `sticky-key` | `quint-connect` | one comment per key; use distinct keys for separate reporting jobs |
| `title` | | names the job in its comment |

Outputs: `status` (the worst contract status, or `none`), `markdown-path`, and `readme`
(`current`, `stale`, `unverifiable`, `written`, `absent` or `off`).

The comment belongs to the `github-actions[bot]` comment that ends with this key's
marker. A report for an older run or attempt, or for a commit the pull request has moved
past, never replaces it. A fork pull request's token cannot comment, so it keeps the job
summary and the artifact.

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
`attest`), each mutant's detecting trace with its differences, and `findings`: for a
failed history its state, action, arguments, first differences and mismatch file, and for
a surviving mutant how many histories it survived. `root` is the session's rootdir, and
the mismatch paths are relative to it. `config_file` is the configuration file given
with `-c`, relative to `root` (`null` when pytest found its configuration itself); the
PR comment's reproduce command repeats it. A `--quint-replay` session rewrites
`results.json` with mode `replay-only`; rerun the full contract to restore complete
evidence. Beside it, each
contract's directory holds the ITF traces, Quint logs and a `mismatch-TEST-CASE.json`
for every failed history. Upload the whole directory from CI, including on failure.

JUnit cases carry `quint_trace`, `quint_seed` and `quint_detected_by` properties.
