# Drivers

A driver applies model steps to the real implementation and reads its state back.
It never computes the expected state: expectations come only from the trace.

```python
from pytest_quint_connect import Step


class Driver:
    def step(self, step: Step) -> None: ...  # required
    def observe(self) -> object: ...  # required: plain data, compared with the model
    def close(self) -> None: ...  # optional: called when the history ends, even on failure
```

The `driver=` fixture returns a **factory**: `(HistoryContext) -> Driver`. The plugin
calls it once per history, so each history starts from fresh state, and closes the
driver when that history ends. A mutant case calls it once per trace. The fixture
itself is an ordinary pytest fixture: it can depend on `tmp_path`, a database or
any other fixture, and its teardown runs after the case, pass or fail.

`HistoryContext` carries `contract`, `trace` (the `.itf.json` path), `work_dir` (an
empty directory, deleted after the history) and `mutant` (`None`, or the mutant name
to build).

## Generated typed drivers

```bash
pytest-quint-connect codegen quint-connect.json -o lru_cache_driver.py
```

`codegen` runs `quint compile --target json` on the model and writes a module with:

- a `TypedDict` for each record type and for each variant of each sum type (unit
  variants may omit `value`), and a union alias for each sum type;
- `<Contract>Driver`, an abstract subclass of `pytest_quint_connect.typed.ActionDriver`
  with one abstract method per manifest action, using the Quint action's own
  parameter names and types, plus `observe()` returning the observed state's type.

Subclass it and implement each method against the real code. The base's `step()`
calls the method for each model step, taking each argument from the event field the
Quint action assigns it to. In report-merge, `reject(kind, location)` writes
`mismatch: kind`, so `kind` is read from `mismatch`. An event label can also differ
from the Quint action's name, as when `inspection` emits `op: "inspect"`. Under the
mbt convention, arguments come from the nondeterministic picks of the same name.

Generation stops with an error when a parameter is not carried by any event field,
or one label is emitted by two actions with different arguments.

What this catches, and where:

| Drift | Caught by |
| --- | --- |
| An action added or renamed in the model | the regenerated base has a new abstract method; instantiating an incomplete driver raises `TypeError` |
| A parameter's or an observed field's type changes | mypy or pyright on the driver |
| The model or the manifest's actions/observe edited without regenerating | every history fails: the base records a fingerprint of both, and names the command to rerun |
| A committed generated module is out of date | `pytest-quint-connect codegen … --check` exits 1 with a diff |

The generated module is deterministic and `ruff format`-stable; commit it, and run
`--check` in CI. It contains no behavior. Which real API each action calls, and how
state is read back, stay in your subclass. That code is the trusted part of the
contract, and mutants are what check it.

## Step

| Attribute | Meaning |
| --- | --- |
| `action` | Model action name. Always one of the manifest's `actions`. |
| `args` | Arguments: the event record without its action field (event convention), or the nondeterministic picks that were made (mbt convention). |
| `picks` | Every `mbt::nondetPicks` entry when the trace has them, with `None` for unpicked names. |
| `index`, `state` | Position in the trace and the full decoded state. |

## Observations

Return plain Python data. The model fixes each value's type, and only the shapes
below match it:

| Quint | Python observation |
| --- | --- |
| `int` | `int` (never `bool`) |
| `bool`, `str` | `bool`, `str` |
| record | mapping with exactly the record's string keys |
| `a -> b` map | mapping; keys are matched by these same rules, so `1` and `"1"` differ |
| `Set[T]` | `set` or `frozenset` |
| `List[T]`, tuple | `list` or `tuple` |
| variant `Hit(3)`, unit `Miss` | `{"tag": "Hit", "value": 3}`, `{"tag": "Miss"}`, or a `Variant` |

Project only what the model specifies. The projection is trusted code: keep it
small and obviously correct, because a projection that hides a field also hides
the bugs in it.

## Step conventions

**Event (default).** The model keeps a state variable, usually `event`, holding a
record whose `op` field names the action and whose other fields are its arguments,
and a variable, usually `observed`, holding what the driver must read back. Every
action sets both. Named witnesses need this convention: in Quint 0.33.1,
`quint test --out-itf` writes no `mbt::` metadata. End a witness assertion with a
read-only `inspect` action so the last step is not a silent repeat of a mutation.

**MBT metadata.** `quint run --mbt` adds `mbt::actionTaken` and `mbt::nondetPicks`
to each state. With `"convention": "mbt"` the action name and arguments come from
that metadata, and `observe` lists the state variables to compare. It suits contracts
that only sample, and its `step` must choose actions with `any { … }`: a step that
calls one action directly is recorded as `mbt::actionTaken = "step"`, which names no
action. When a sampled trace carries both an event and the metadata, the plugin
requires them to agree, which catches a model action that labels itself with the
wrong event. A step recorded under the `init` or `step` name is accepted, because it
carries no action of its own to compare.

## Mutants

Mutation controls prove that the observation can see the faults that matter. Build
each one in the factory from `context.mutant`: a deliberately wrong implementation,
a compiled source mutation, or a post-call corruption of state. Keep them in the
consumer's code; the plugin only knows their names. A mutant counts as detected only
when some generated history produces an observed-state mismatch. If building or
running it raises any other exception, the mutant case fails.
