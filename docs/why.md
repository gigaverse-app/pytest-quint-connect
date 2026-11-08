# Why pytest-quint-connect

Coding agents change code faster than anyone can review every line. What has to
stay true is a short list of rules, and rules written only in prose or scattered
across unit tests get lost: an agent can rewrite the tests together with the bug.

pytest-quint-connect gives those rules a home. You describe the high-level behavior
once, as a small [Quint](https://quint.sh) model that you can test in the abstract.
pytest-quint-connect connects your implementation to that model and replays its
histories against the real code on every run. A change by a person or by an agent
is checked against the rules, not against tests the same change may have edited.

```text
 the rules (Quint model)  ──generate──►  histories: init, store a, store b, fetch a, store c, ...
          ▲                                          │
     reviewed by people                              ▼ replay each step
                                       your code ──► read the state back ──► compare with the model
                                                                               │
                                                     pytest: pass, or a field-level diff and a replay command
```

## What you get

### Rules that are short enough to review

The [LRU cache model](../examples/lru_cache/lru.qnt)
is about 60 lines. It says what eviction, recency and hits mean, with named
scenarios such as "store a, store b, read a, store c: b is evicted". The code it
checks can be anything with the same public behavior. When the rules change, the
diff is in the model, where a reviewer sees it, instead of buried in test fixtures.

### Refactors checked against the same histories

The example ships two implementations, a list and an `OrderedDict` refactor. Both
replay the same 15 generated histories:

```text
test_lru_contract.py::test_lru_cache: 17/17 cases passed; 15 fresh histories; 1/1 mutants detected
test_lru_contract.py::test_ordered_refactor: 17/17 cases passed; 15 fresh histories; 1/1 mutants detected
34 passed in 2.27s
```

An agent asked to "simplify the cache" gets the same verdict: the refactor either
reaches the model's state after every step, or it does not.

### Plausible bugs caught, with the exact step

Ask an agent to make reads faster and it may stop reads from refreshing recency.
Every unit test that only checks the returned value still passes. The contract
fails 4 of the 15 histories and names the first step that differs:

```text
Quint contract 'lru-cache': implementation state differs from the model
  trace:  .artifacts/quint-connect/lru-cache/witness-evictsLeastRecentlyUsedTest-0.itf.json
  state:  3 after action fetch(key='a', value=0)
  seed:   7
  4 difference(s):
  entries[0].key: values differ
      expected: 'b'
      actual:   'a'
  ...
  reproduce: pytest test_lru_contract.py::test_lru_cache --quint-replay .artifacts/quint-connect/lru-cache/witness-evictsLeastRecentlyUsedTest-0.itf.json
```

The replay command reruns only that history, which is what you hand back to the
agent, or to yourself, to fix.

### Proof that the tests can fail

A test suite that cannot fail proves nothing. Each contract declares *mutants*:
deliberately wrong versions that every run must catch. A mutant counts as caught
only when some history shows an observed difference. A crash is not detection.

| Contract | Histories | Mutants caught |
| --- | --- | --- |
| [LRU cache example](../examples/lru_cache/README.md) | 15 | 1 of 1 |
| pytest-obligation's report merge | 21 | 6 of 6 |
| [pytest-selective's test selection](https://github.com/gigaverse-app/pytest-selective/tree/main/contracts/selection) | 19 | 3 of 3 |
| [This plugin's own receipts](../dogfood/README.md) | 8 | 4 of 4 |

If the observation or the histories cannot see a fault, its mutant survives and the
run fails. That tells you the contract has a blind spot before a real bug finds it.

### Spec and code that cannot quietly drift apart

`pytest-quint-connect codegen` turns the model into a typed Python base class with
one method per model action. Add an action to the model and the regenerated base
has a new abstract method, so an unchanged driver fails to instantiate. Change a
type and mypy or pyright flags the driver. Edit the model without regenerating and
every history fails, naming the command to rerun ([details](drivers.md#generated-typed-drivers)).

### A result CI can require

`pytest --quint-require-complete` fails unless every required contract ran every
history and caught every mutant. A run filtered with `-k`, or a single replay, is
recorded as partial, never as a pass. The run writes a `results.json` receipt with
the Quint version, commands, seeds and file hashes. [Run contracts in CI](ci.md).

## When to use it

Use it for stateful behavior with rules you can state but tests keep missing:
caches, queues, schedulers, retry and recovery logic, permission and lifecycle
state machines, merge and selection algorithms. Start from the parts where a
wrong answer is expensive and the rules are stable.

It checks the histories it generates, within the model's bounds, one step at a
time. It does not prove the code correct, model concurrency you did not model, or
replace unit tests. [What a passing run shows](limits.md) lists the edges.

**Next:** [write your first contract](tutorial.md), or
[let your agent set it up](../skills/pytest-quint-connect/SKILL.md).
