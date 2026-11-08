---
name: pytest-quint-connect
description: Add model-based tests to a Python repository with pytest-quint-connect. Writes a small Quint model of one stateful API, a typed driver, mutants that prove the check can fail, and a CI job that replays Quint-generated histories against the real code. Use to adopt pytest-quint-connect, add a contract, wire it into CI, or debug a replay mismatch.
license: Apache-2.0
---

# Set up pytest-quint-connect

You are adding a **contract** to this repository:

- a **model**: a small Quint file stating what one stateful API should do;
- a **driver**: Python that applies each model step to the real code and reads its state back;
- **mutants**: deliberately broken variants of the code that the contract must catch.

Quint generates **histories** (sequences of steps) from the model, and pytest replays each one
against the real code. The human reviews the model; you write everything else.

Do the steps in order. Run each step's check and fix what fails before moving on. Stop and ask
the human only for a secret, a sign-in, a repository setting you cannot change, or a decision
about what the code is *supposed* to do. If you cannot get past a failure, stop and report the
exact command and its full error output.

Reference, when a step needs more: [manifest](https://pytest-quint-connect.readthedocs.io/en/latest/docs/manifest/),
[drivers](https://pytest-quint-connect.readthedocs.io/en/latest/docs/drivers/),
[CI](https://pytest-quint-connect.readthedocs.io/en/latest/docs/ci/),
[limits](https://pytest-quint-connect.readthedocs.io/en/latest/docs/limits/), and the complete
[LRU cache example](https://github.com/gigaverse-app/pytest-quint-connect/tree/main/examples/lru_cache)
to copy the shape from.

## 1. Preflight

- **Read the repository's rules first**: `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING*`, `tox.ini`,
  `noxfile.py`, `pyproject.toml`/`setup.cfg`, `.pre-commit-config.yaml` and `.github/workflows/`.
  They override the commands below. Run tests the way the repository does (`tox -e …`,
  `nox -s …`, `uv run`, `make`), and match its formatter, line length, type checker, Action
  pinning (tags or commit SHAs) and commit conventions.
- **Check the tools**: Python 3.11+, Node.js 22+ with npm (`node --version`, `npm --version`).
  Add `pytest-quint-connect` to the test dependencies the way the repository manages them, or
  `pip install pytest-quint-connect`. On first use the plugin installs its pinned Quint 0.33.1
  with npm into a user cache; `quint` does not need to be on `PATH`.
- **Pick one stateful behavior** with a public API: operations that change state and a way to
  read the state back (a cache, queue, registry, state machine, session). Prefer the one whose
  rules matter most and are easiest to state. Read its code, docs and tests.
- **Choose small bounds**: two or three keys, small integers, tiny capacities; `max_steps` 6-10,
  `n_traces` 8-16. Small values find most bugs and keep the run fast.

## 2. Model and manifest

Put everything in `contracts/<name>/`, outside the repository's main test paths (step 6).

Write `<name>.qnt`, modeling the API boundary, not the implementation:

- an `event` record (`op` plus the arguments) and an `observed` record holding only what the
  public API exposes. Use one event record type for every action; give unused fields a fixed value;
- `init`, one action per API operation, and a read-only `inspect` action. Every action sets both
  `event'` and `observed'`;
- a `step` action that picks arguments with `nondet x = Set(…).oneOf()` and chooses an action with
  `any { … }`, and an invariant (a `val`) that must hold in every state;
- two to four **witnesses** for the scenarios the user cares about, each ending in an assertion:
  `run evictsOldestTest = init.then(put("a", 1)).then(…).then(all { assert(…), inspect })`.

Do not use Quint built-in names as parameters or definitions. `next`, `get`, `put`, `set`,
`keys`, `size`, `length`, `head`, `item`, `map`, `filter`, `range` and `fail` all fail with
`QNT101 Built-in name '…' is redefined`. Use `key`, `value`, `amount`, `entry` and the like.

Write `quint-connect.json` beside it: every action mapped to the API it drives, the witnesses,
the mutants (step 4), seeded bounds, and the implementation files in `sources`. Paths in
`sources` are relative to the manifest, or to `root` when set. If an installed copy of the
package could shadow the checkout, add `attest`: `source` is the directory that *contains* the
package directory (for a `src/` layout, `"src"`), relative to `root`.

**Check:** `pytest-quint-connect typecheck contracts/<name>/quint-connect.json` prints
`… typechecks with Quint 0.33.1`. On an error, it shows Quint's diagnostic with the line.

## 3. Driver base, driver and test

Generate the typed base with the default output name (no `-o`), so the CI check compares the
same file:

```bash
pytest-quint-connect codegen contracts/<name>/quint-connect.json
```

It writes `<module>_driver.py` beside the manifest. Commit it and never edit it; it is already
exempt from your linters and formatters.

In `contracts/<name>/conftest.py`, subclass the generated base. Each method calls the real API
with its arguments; `observe()` reads the real state back as plain data shaped like `observed`.
**Never compute an expected value in the driver**: expectations come only from the model. The
fixture returns a factory, so every history starts from fresh state:

```python
@pytest.fixture
def cache_driver():
    return lambda context: CacheDriver(MUTANTS.get(context.mutant, RealCache))
```

Declare the test in `contracts/<name>/test_<name>_contract.py`:

```python
from pytest_quint_connect import quint_contract

test_cache = quint_contract("quint-connect.json", driver="cache_driver")
```

Add `contracts/quint.ini` (a config used only with `-c`, so the main suite never loads it):

```ini
[pytest]
pythonpath = <paths that make the code and the generated base importable, relative to contracts/>
quint_required_contracts = <name>
```

## 4. Mutants

For each rule that matters, add a mutant: a plausible bug built in the factory from
`context.mutant` (a subclass that skips one update, a patched function undone in `close()`).
List their names in the manifest. **Each one must be detected.** A surviving mutant means the
observation or the histories cannot see that fault: widen `observed` or add a witness. Never
delete the mutant, drop a witness or remove an observed field to make a run pass.

## 5. Run it

```bash
pytest -c contracts/quint.ini contracts --quint-require-complete
```

Use the repository's runner around it (step 6). Expect one summary line per contract test, with
equal numbers on both sides of each slash (node ids are relative to `contracts/`, the rootdir
that `-c` sets):

```text
<name>/test_<name>_contract.py::test_cache: 17/17 cases passed; 15 fresh histories; 1/1 mutants detected
```

Cases are witnesses + `n_traces` samples + mutants + one coverage case.

- **A mismatch** prints the state, the action and the differing fields, then a `reproduce:`
  command. Run it from the same directory. Decide whether the code or the model is wrong. If it
  is the code, report it to the human rather than bending the model to fit.
- **A replay rewrites `results.json`** with status `replay-only`. Rerun the full command before
  you report results or refresh the README block.
- **A setup error** means Quint could not generate histories. It shows Quint's own message, such
  as `Assertion failed` with the file and line of a witness. Fix the model.

## 6. Wire it into the repository

- **Keep contracts out of the main suite.** If the main pytest config has no `testpaths`, set it
  to the existing test directories, or add `--ignore=contracts`. Run the repository's normal test
  command and confirm it does not collect `contracts/`.
- **Add a runner entry** if the repository uses one. For tox:

  ```ini
  [testenv:contracts]
  pass_env = PYTEST_QUINT_CONNECT_CACHE, QUINT
  commands =
      pytest-quint-connect codegen --check contracts/<name>/quint-connect.json
      pytest -c contracts/quint.ini contracts --quint-require-complete
  ```

  nox passes the environment through unless you restrict it.
- **Add a CI job** from [Run contracts in CI](https://pytest-quint-connect.readthedocs.io/en/latest/docs/ci/),
  adapted to the existing workflows: the same checkout, Python and install steps, Node.js 22+,
  `codegen --check`, the contract run, then the report step with `if: ${{ !cancelled() }}` and
  job permission `pull-requests: write`. Write the JUnit file into the artifacts directory,
  `contracts/.artifacts/quint-connect/junit.xml`. Pin Actions the way the repository does. If it
  pins commit SHAs, pin the report action too:
  `gh api repos/gigaverse-app/pytest-quint-connect/commits/v1 --jq .sha`, then
  `uses: gigaverse-app/pytest-quint-connect/report@<sha>  # v1`.
- **README block.** The report keeps a results table between
  `<!-- pytest-quint-connect:start -->` and `<!-- pytest-quint-connect:end -->` in a Markdown
  file. If the README is Markdown, add those two lines where the table belongs. Otherwise (a
  `README.rst`, for example), put them in `contracts/README.md`, set the report step's
  `readme: contracts/README.md`, and link that file from the README. Fill the block once and commit it:
  `pytest-quint-connect report '**/.artifacts/quint-connect/results.json' --readme <file>`.
- **Making the check required** needs a repository admin: tell the human the job's check name.

## 7. Verify and report

Run, and keep the output: `codegen --check` (prints `… is up to date`), the contract run (the
summary line above), the repository's normal test command, and its lint and type checks. Then
report:

```text
Contract: <name> (contracts/<name>/)
Behavior: <the API and the rules, in two or three plain sentences>
Review this: contracts/<name>/<name>.qnt (<N> lines). It is the specification; the rest is plumbing.
Verified: <command> -> "<summary line>"; codegen --check -> up to date; <repository test command> -> passed
Mutants: <name>: <the bug it simulates> (detected)
Not covered: <behavior outside the model or the bounds>; see the limits page
Needs a human: review the model; make "<check name>" a required check
```

An agent can write a wrong model, and a wrong model checks the wrong rules. The human's review
of the `.qnt` file is the point of the exercise: say so in the report.
