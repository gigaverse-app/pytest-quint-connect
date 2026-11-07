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
that only sample. When a sampled trace carries both an event and the metadata, the
plugin requires them to agree, which catches a model action that labels itself with
the wrong event.

## Mutants

Mutation controls prove that the observation can see the faults that matter. Build
each one in the factory from `context.mutant`: a deliberately wrong implementation,
a compiled source mutation, or a post-call corruption of state. Keep them in the
consumer's code; the plugin only knows their names. A mutant counts as detected only
when some generated history produces an observed-state mismatch. If building or
running it raises any other exception, the mutant case fails.
