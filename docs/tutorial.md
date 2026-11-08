# Tutorial: your first contract

This tutorial builds the [LRU cache example](../examples/lru_cache/README.md) one
step at a time: the rules, the contract, the connection to the code, a mutant that
proves the check can fail, and a CI run. It takes about 20 minutes. Every file it
shows is in [examples/lru_cache](../examples/lru_cache/README.md), so you can run each
step as you read.

!!! tip "Prefer a coding agent?"
    Give it one line: `Add pytest-quint-connect to this repository. Fetch
    https://pytest-quint-connect.readthedocs.io/en/latest/agent-setup.md and follow it.`
    It writes the model, driver, mutants and CI job, and you review the model. This
    tutorial shows what it builds and why.

You need Python 3.11+ and Node.js 22+ with npm. On first use the plugin installs its
locked Quint 0.33.1 into a per-user cache.

```bash
pip install pytest-quint-connect
```

## The code under test

A cache with room for two entries. `put` and `get` count as uses; when the cache is
full, `put` evicts the entry used least recently. `items()` lists the entries, oldest
first. [lru_cache.py](../examples/lru_cache/lru_cache.py) has three versions: the
original, an `OrderedDict` refactor, and one with a bug.

## 1. Write the rules as a model

The model describes behavior at the API boundary, not the implementation. Two
state variables carry everything the plugin needs:

- `event`: what happened in this step (the operation and its arguments);
- `observed`: what the real API should show afterwards.

```quint
action fetch(key: str): bool = {
  val hits = observed.entries.select(e => e.key == key)
  all {
    event' = { op: "fetch", key: key, value: 0 },
    observed' = if (hits.length() == 0) { ...observed, lastRead: Miss }
      else { entries: without(observed.entries, key).append(hits[0]), lastRead: Hit(hits[0].value) },
  }
}
```

That is the whole rule for reads: a hit moves the entry to the newest position and
returns its value; a miss changes nothing. Name the scenarios you care about as
*witnesses*, `run …Test` definitions that end in an assertion:

```quint
run evictsLeastRecentlyUsedTest = init.then(store("a", 1)).then(store("b", 2)).then(fetch("a"))
  .then(store("c", 0))
  .then(all { assert(observed.entries == [{ key: "a", value: 1 }, { key: "c", value: 0 }]), inspect })
```

Read it as: store a, store b, read a, store c, and b (not a) is gone. A `step` action
lets Quint also generate random histories, and an invariant (`withinCapacity`) is
checked while sampling. The full model is [lru.qnt](../examples/lru_cache/lru.qnt), 61 lines.

## 2. Declare the contract

[quint-connect.json](../examples/lru_cache/quint-connect.json) ties the model to the
test run. It lists every action and the API it drives, the witnesses, the mutants
every run must catch, and seeded bounds, so each run regenerates the same histories:

```json
{
  "version": 1,
  "name": "lru-cache",
  "module": "lru_cache",
  "model": "lru.qnt",
  "invariant": "withinCapacity",
  "actions": {
    "init": "construct a fresh cache with capacity 2",
    "store": "cache.put(key, value)",
    "fetch": "cache.get(key), observed as Hit(value) or Miss",
    "inspect": "read cache.items() without changing it"
  },
  "witnesses": ["evictsLeastRecentlyUsedTest", "overwriteRefreshesTest", "missKeepsContentsTest"],
  "mutants": ["stale-read"],
  "bounds": {"seed": 7, "max_samples": 200, "max_steps": 8, "n_traces": 12},
  "sources": ["lru_cache.py"]
}
```

Unknown fields fail collection, so a typo cannot silently weaken the check. The
[manifest reference](manifest.md) covers every field.

## 3. Generate a typed driver base

```bash
pytest-quint-connect codegen quint-connect.json  # writes lru_cache_driver.py
```

The generated `LruCacheDriver` has one abstract method per action, typed from the
model, plus `observe()` returning the model's `View` type. Commit it and never edit it.

## 4. Connect the code

Subclass the base in `conftest.py`. Each method calls the real API, and `observe()`
reads the real state back. The driver never computes what the answer should be:
expectations come only from the model.

```python
class CacheDriver(LruCacheDriver):
    def __init__(self, cache_type: type) -> None:
        self.cache_type = cache_type
        self.cache = cache_type(2)
        self.last_read: Read = {"tag": "NoRead"}

    def init(self) -> None:
        self.cache = self.cache_type(2)
        self.last_read = {"tag": "NoRead"}

    def store(self, key: str, value: int) -> None:
        self.cache.put(key, value)
        self.last_read = {"tag": "NoRead"}

    def fetch(self, key: str) -> None:
        value = self.cache.get(key)
        self.last_read = {"tag": "Miss"} if value is None else {"tag": "Hit", "value": value}

    def inspect(self) -> None:
        pass

    def observe(self) -> View:
        return {
            "entries": [{"key": key, "value": value} for key, value in self.cache.items()],
            "lastRead": self.last_read,
        }


@pytest.fixture
def lru_driver(request: pytest.FixtureRequest) -> Callable[[HistoryContext], CacheDriver]:
    chosen = IMPLEMENTATIONS[request.config.getoption("--lru-implementation")]

    def start(context: HistoryContext) -> CacheDriver:
        return CacheDriver(IMPLEMENTATIONS[context.mutant] if context.mutant else chosen)

    return start
```

The fixture returns a factory, so every history starts from a fresh cache.

## 5. Declare the test and run it

```python
# test_lru_contract.py
from pytest_quint_connect import quint_contract

test_lru_cache = quint_contract("quint-connect.json", driver="lru_driver")
test_ordered_refactor = quint_contract("quint-connect.json", driver="ordered_driver")
```

```bash
pytest --quint-require-complete
```

```text
test_lru_contract.py::test_lru_cache: 17/17 cases passed; 15 fresh histories; 1/1 mutants detected
test_lru_contract.py::test_ordered_refactor: 17/17 cases passed; 15 fresh histories; 1/1 mutants detected
34 passed in 2.27s
```

Each test became 17 ordinary pytest cases: 3 witnesses, 12 sampled histories, one
mutant and a coverage check that every declared action was reached. The second test
runs the `OrderedDict` refactor against the same histories.

## 6. Prove the check can fail

The manifest's `stale-read` mutant is a plausible bug: reads return the right value
but no longer refresh recency. The fixture builds that cache when
`context.mutant == "stale-read"`, and the run passes only because some history
caught it. To see what a developer would see, run the buggy cache as the main
implementation:

```bash
pytest --lru-implementation stale-read -k test_lru_cache
```

```text
Quint contract 'lru-cache': implementation state differs from the model
  trace:  .artifacts/quint-connect/lru-cache/witness-evictsLeastRecentlyUsedTest-0.itf.json
  state:  3 after action fetch(key='a', value=0)
  4 difference(s):
  entries[0].key: values differ
      expected: 'b'
      actual:   'a'
  ...
  reproduce: pytest test_lru_contract.py::test_lru_cache --quint-replay .artifacts/quint-connect/lru-cache/witness-evictsLeastRecentlyUsedTest-0.itf.json
4 failed, 13 passed, 17 deselected
```

Run the printed `reproduce` command to replay exactly that history while you fix it.
Add a mutant for each property you rely on. If one is not caught, the observation or
the histories cannot see that fault: widen them, never delete the mutant.

## 7. Require it in CI

List the contract in `pytest.ini` so a run that skips it cannot pass:

```ini
[pytest]
quint_required_contracts = lru-cache
```

Then run `pytest --quint-require-complete` and `pytest-quint-connect codegen --check`
on every pull request. [Run contracts in CI](ci.md) has the workflow.

## Where to go next

- Choose a step convention or use a hand-written driver: [drivers](drivers.md).
- Know what a passing run shows and what it does not: [limits](limits.md).
- Have a coding agent write the model, manifest and driver for your code:
  [the agent skill](../skills/pytest-quint-connect/SKILL.md).
