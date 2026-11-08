# Contract manifest

A contract is a JSON file, conventionally `quint-connect.json` beside the model. A
test module declares it with `quint_contract(path, driver="fixture_name")`; the path
is relative to that module. Unknown fields are rejected, so a typo fails collection
instead of silently weakening a check.

```json
{
  "version": 1,
  "name": "lru-cache",
  "module": "lru_cache",
  "model": "lru.qnt",
  "invariant": "withinCapacity",
  "actions": {"init": "new cache", "store": "cache.put", "fetch": "cache.get", "inspect": "read only"},
  "witnesses": ["evictsLeastRecentlyUsedTest"],
  "mutants": [
    {"name": "stale-read", "description": "get() returns the value but does not refresh recency"}
  ],
  "bounds": {"seed": 7, "max_samples": 200, "max_steps": 8, "n_traces": 12},
  "sources": ["lru_cache.py"]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Always `1`. |
| `name` | no | Contract name used in `quint_required_contracts`, artifact paths and reports. Default: the manifest's directory name. |
| `module` | yes | Quint module passed as `--main`. |
| `model` | yes | `.qnt` file, relative to the manifest. |
| `invariant` | yes | Invariant checked while sampling (`quint run --invariant`). |
| `actions` | yes | Every action name a trace may contain, mapped to the implementation boundary it drives. An undeclared action in a trace fails; a declared action no history reaches fails the `coverage` case. |
| `bounds` | yes | `seed`, `max_samples`, `max_steps` and `n_traces` for `quint run`. `n_traces` sampled histories become `sample-0` … `sample-N-1`. The seed is also passed to `quint test`. |
| `witnesses` | no | Names of `run …Test` definitions. Each must produce exactly one trace and becomes a `witness-NAME` case. |
| `mutants` | no | Planted bugs: deliberate faults your driver factory builds from `context.mutant`. Each becomes a `mutant-NAME` case that must be caught. An entry is a name, or an object with a `description`; see [mutants](#mutants). |
| `bugs_found` | no | Real bugs this contract found and that were fixed, each optionally re-checked by a mutant; see [bugs found](#bugs-found). |
| `convention` | no | `event` (default) or `mbt`; see [drivers](drivers.md#step-conventions). |
| `event`, `action_field` | no | For the event convention: the state variable holding the step record (default `event`) and its action-name field (default `op`). |
| `observe` | no | State variable the driver's observation is compared with (default `observed`), or a list of variable names compared as one record. |
| `mbt` | no | Pass `--mbt` to `quint run` (default `true`). |
| `init`, `step` | no | Initial and step action names for `quint run` (default `init`, `step`). |
| `backend` | no | Quint simulator backend (default `typescript`, which needs no download). |
| `quint_version` | no | Exact Quint version required (default `0.33.1`). |
| `historical`, `historical_sha256` | no | A frozen model kept as evidence (for example the model of a fixed bug) and its SHA-256. Any edit fails every case of the contract. |
| `root` | no | Directory, relative to the manifest, that `sources` and `attest.source` are relative to. Default: the manifest's directory. |
| `sources` | no | Implementation files whose hashes go into the receipt. A missing file fails collection. |
| `attest` | no | `{"package": "name", "source": "dir"}`: after every step, every imported module of `package` must come from `source`, so a stale installed copy cannot stand in for the checkout under test. `source` is the directory that *contains* the package directory, relative to `root`: `"src"` for `src/name/`, `"."` for `name/` at the root. Use it when a runner installs the package non-editably (tox does by default), and put `source` first on the contract config's `pythonpath` so the checkout is what gets imported. |
| `description`, `limits` | no | Documentation. `limits` is copied into the receipt. |

## Mutants

Reports call mutants *planted bugs* and explain the term on hover: deliberately broken
copies of the code, each of which must make some history diverge. A `mutants` entry is
a bare name or an object:

```json
"mutants": [
  "drops-newest",
  {"name": "stale-read", "description": "get() returns the value but does not refresh recency"}
]
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | What `HistoryContext.mutant` carries and the case id uses. |
| `description` | yes | What the fault does, in one line. The PR comment and README block show it beside where it was caught. |

Unknown fields are rejected. A bare name stays valid and has no description. A mutant
stops at the first history that catches it, unless a found bug names it.

## Bugs found

When adopting a contract surfaces a real bug (a witness or a historical model shows
the code doing the wrong thing, you write a regression test and fix the code), record
it. Reports lead with these:

```json
"bugs_found": [
  {
    "summary": "A rejected report shard leaked its earlier cases and new suites into the combined report",
    "fixed_in": "#210",
    "found_by": "the historical audit model pinned to 389c5bb",
    "regression_test": "package/tests/core/test_profile_coverage.py::test_rejected_shard_cannot_promote_or_mutate_any_prior_evidence",
    "mutant": "shard-leaks-before-rejection"
  }
]
```

| Field | Required | Meaning |
| --- | --- | --- |
| `summary` | yes | What was wrong, in one sentence. |
| `fixed_in` | yes | The fix: a commit, PR (`#210`) or URL. |
| `found_by` | no | How the contract found it: a witness, a sampled history, a historical model. |
| `regression_test` | no | The pytest node id of the test added with the fix. |
| `mutant` | no | A declared mutant that reintroduces the bug. Every history is replayed through it, and the receipt records which diverged. |

Unknown fields are rejected, `mutant` must name a declared mutant, and two bugs cannot
name the same one. The report then says, for example, "16 of 21 histories diverge when
planted bug `shard-leaks-before-rejection` reintroduces it". The count is what the run
proves; the rest of the entry is your account, shown as written. Record only bugs that
were really fixed. Without a `mutant` the report says no planted bug re-checks it.

Model and historical paths are relative to the manifest. Edit the model, manifest
and driver together when behavior changes; review an intended weakening openly
rather than dropping a witness, mutant or observed field to make a run pass.
