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
| `mutants` | no | Deliberate faults your driver factory builds from `context.mutant`. Each becomes a `mutant-NAME` case that must be caught. An entry is a name, or an object with a `description`; see [mutants](#mutants). |
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
| `attest` | no | `{"package": "name", "source": "dir"}`: after every step, every imported module of `package` must come from `source`, so a stale installed copy cannot stand in for the checkout under test. |
| `description`, `limits` | no | Documentation. `limits` is copied into the receipt. |

## Mutants

A `mutants` entry is either a bare name or an object:

```json
"mutants": [
  "drops-newest",
  {"name": "stale-read", "description": "get() returns the value but does not refresh recency"},
  {
    "name": "unmerged-outcomes",
    "description": "merge_reports keeps only the first worker's outcome for each test",
    "regression": {"ref": "389c5bb", "summary": "parallel workers' outcomes were dropped when reports merged"}
  }
]
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | What `HistoryContext.mutant` carries and the case id uses. |
| `description` | yes | What the fault does, in one line. The PR comment and README block show it beside where the mutant was caught. |
| `regression` | no | `{"ref", "summary"}`: the mutant reproduces a real, previously shipped defect. `ref` names the fix (commit, PR or URL) and `summary` says in one line what was wrong. |

Unknown fields are rejected in the entry and in `regression`. A bare name stays valid
and has no description.

An ordinary mutant stops at the first history that catches it. A `regression` mutant
is replayed through every history, and the receipt records which ones diverged, so the
report can say "Catches the bug fixed in 389c5bb: 16 of 21 histories diverge". Mark
only a defect that was really shipped and fixed, and point `ref` at the fix: the
receipt proves how many histories diverge through the mutant, while the claim that the
mutant is that defect is yours.

Model and historical paths are relative to the manifest. Edit the model, manifest
and driver together when behavior changes; review an intended weakening openly
rather than dropping a witness, mutant or observed field to make a run pass.
