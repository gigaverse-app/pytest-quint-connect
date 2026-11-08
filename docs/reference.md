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
    [--status-file PATH] [--artifact-dir DIR] [--ledger FILE] [--assets URL]
```

Reads `results.json` receipts (files or quoted recursive globs, such as
`'**/.artifacts/quint-connect/results.json'`) and renders them. It only reads receipts;
it never runs Quint or imports the project. Receipts under a plugin `_work` directory are
skipped: they belong to sessions a driver ran inside one history.

- **Comment**: a headline that says what matters now (cases passed and planted bugs caught,
  or each test's status, worst first), with bugs the contracts found earlier credited on a
  separate line as history. Then one table row per contract test (histories, cases passed,
  mutants caught, actions covered), failing rows first, under a summary line of the totals.
  Then two tables, each under its own collapsed summary: the bugs the contracts found
  (summary, how found, fix, regression test, and how many histories diverge when its planted
  bug reintroduces it), and every planted bug (mutant) with what it breaks and the case and
  step that first caught it. When every contract passed every case and caught every planted
  bug, the contract table starts collapsed too; otherwise it stays open, as does the planted
  bugs table when one was not caught. Tables span the comment's width. "Planted bugs" links
  to an explanation that shows on hover. Then, collapsed
  under "What `NAME` says", what each contract claims, so a reviewer can judge the model
  itself: each declared action and the code it drives, the invariant, the witnesses, the
  bounds, and, when it declares witnesses, the first one step by step (each action and the observation the model
  expects after it, read from its generated trace; at most 20 states). Receipts from
  earlier plugin versions lack these fields and show no such block. When the comment would exceed
  GitHub's limit, these blocks are left out first. Then each failure with its state,
  action and differences, and a
  `pytest 'NODE[CASE]'` command that reproduces it. The command regenerates the same
  histories from the manifest's seed. Statuses stay distinct: `failed`, `interrupted`
  (the session never finished), `partial` (filtered), `not run` (collected, nothing
  selected), `replay-only` and `passed`.
- **README block**: the same tables, collapsed the same way, the same claims and a badge, between the lines
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
  - uses: gigaverse-app/pytest-quint-connect/report@vX.Y.Z  # the installed pytest-quint-connect version
    if: ${{ !cancelled() }}
```

It finds every receipt in the workspace, writes the comment to the job summary, uploads
each receipt's directory (traces, logs, mismatches) as the `quint-connect-KEY-ATTEMPT`
artifact, creates or updates one comment on same-repository pull requests, and fails the
step when `README.md`'s block is stale. When the `readme` file does not exist or has no
markers, the step adds a warning annotation naming the file and leaves it alone. The
block is Markdown and HTML: next to a `README.rst`, keep it in a Markdown file such as
`contracts/README.md` and set `readme` to it.

Pin the action to the version of pytest-quint-connect your tests install. It renders
with its own copy of the code, so another version can render the README block
differently and fail the check. With commit SHAs, write `report@<sha>  # vX.Y.Z`.

| Input | Default | |
| --- | --- | --- |
| `receipts` | `**/.artifacts/quint-connect/results.json` | files or globs, one per line |
| `readme` | `README.md` | document holding the block |
| `readme-mode` | `check` | `check`, `write` (rewrite in the workspace; commit it yourself) or `off` |
| `post-comment` | `true` | comment on same-repository pull requests |
| `sticky-key` | `quint-connect` | one comment per key; use distinct keys for separate reporting jobs |
| `title` | | names the job in its comment |
| `share-totals` | `true` | on the default branch, add merged pull requests' tallies to [the global counter](#the-global-counter); `false` opts out |

Outputs: `status` (the worst contract status, or `none`), `markdown-path`, and `readme`
(`current`, `stale`, `unverifiable`, `written`, `absent` or `off`).

The comment belongs to the `github-actions[bot]` comment that ends with this key's
marker. A report for an older run or attempt, or for a commit the pull request has moved
past, never replaces it. A fork pull request's token cannot comment, so it keeps the job
summary and the artifact.

The comment opens with a full-width tally of the pull request's **disagreements**: a
history where the code reached a different state than the model, or a mutant that no
history caught. Crashes and setup errors are not disagreements. A disagreement is
*caught* on the first push that reports it and *fixed* once a later push runs that case
and it passes; a case that later fails again is open again, and a case filtered out of a
push changes nothing. The tally lives in the comment as hidden JSON
(`<!-- pytest-quint-connect-ledger ... -->`). Before rendering, the action reads it back
from the bot-owned comment with this key, so every push extends it. From the command
line, pass the previous comment as `report --ledger FILE`; an empty or missing file
starts a new tally.

### The global counter

On a push to the default branch, the action sends each recently merged pull request's final tally to
pytest-quint-connect's public counter, a [Cloudflare Worker](../counter) at `https://pytest-quint-connect-counter.gigaverse-oss.workers.dev`, which
adds it to the [sign in the README](../README.md#impact). A contribution holds the repository's counter key, the pull
request number, the disagreements it caught, how many were fixed before merge, how many were still failing when it
merged, its pushes, and the plugin version: no code, names, paths or people.

- **Pseudonymous, not anonymous.** The counter key is the SHA-256 of the repository's numeric GitHub id
  (`GITHUB_REPOSITORY_ID`), never of its name. It ties the repository's pull requests together, but a name does not
  lead to it, and the counter refuses to look a repository up by name. The default-branch run prints the key.
- **Counted once.** The counter keys contributions on the repository's key and pull request number, so each run can
  resend the 30 most recently merged pull requests: a resend changes nothing, and a failed send is caught up by the
  next run. Sending never fails the job.
- **The repository's own sign.** While it shares its totals, the README block opens with the repository's sign: what
  its own contracts caught (`report --counter-sign`, which the action passes).
- **Opt out** with `share-totals: "false"`. Nothing is sent, and the block shows no sign.

The send needs the job to run on pushes to the default branch, and a token that can read pull requests.

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
| `mutant-NAME` | Some generated trace, replayed through the mutant, mismatches. A mutant named by a `bugs_found` entry replays every trace and records each that diverged. |
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

Each contract entry records the manifest, bounds, Quint version, `mutants` (names) and
`mutant_specs` (each mutant as the manifest's object form, with its `description` when
given), `bugs_found` (as the manifest declares them), the exact Quint commands, trace paths, SHA-256 of the manifest, model, historical model and sources,
and per test: outcomes, completeness, steps replayed, imported modules (with
`attest`), each mutant's detecting trace with its differences, and `findings`: for a
failed history its state, action, arguments, first differences and mismatch file, and for
a surviving mutant how many histories it survived. A detected mutant's entry in
`mutations` says where it was first caught: `detected_by` (the case id of the trace),
`state`, `action`, `arguments` and the differences, and `caught_by_sample`: whether a sampled
history caught it too (witnesses replay first; samples then replay until one does). When
every detected mutant has `caught_by_sample: false`, the session summary and the PR comment
say that only the named witnesses catch the mutants; the entry of a mutant named by a found bug adds
`histories` (how many were replayed) and `diverged` (the case ids that mismatched, in
generation order). `root` is the session's rootdir, and
the mismatch paths are relative to it. `config_file` is the configuration file given
with `-c`, relative to `root` (`null` when pytest found its configuration itself); the
PR comment's reproduce command repeats it. A `--quint-replay` session rewrites
`results.json` with mode `replay-only`; rerun the full contract to restore complete
evidence. Beside it, each
contract's directory holds the ITF traces, Quint logs and a `mismatch-TEST-CASE.json`
for every failed history. Upload the whole directory from CI, including on failure.

JUnit cases carry `quint_trace`, `quint_seed` and `quint_detected_by` properties.
