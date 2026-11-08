<p align="center"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-quint-connect/main/docs/assets/readme-hero-dark.png?v=fbbc73f6a0f1">
  <img alt="pytest-quint-connect: Quinty the starfish inspects a contract. Model-based testing for Python under pytest." src="https://raw.githubusercontent.com/gigaverse-app/pytest-quint-connect/main/docs/assets/readme-hero-light.png?v=fb4d663f9ca2" width="100%">
</picture></p>

# pytest-quint-connect

[![PyPI](https://img.shields.io/pypi/v/pytest-quint-connect?logo=pypi&logoColor=white)](https://pypi.org/project/pytest-quint-connect/)
[![CI](https://github.com/gigaverse-app/pytest-quint-connect/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gigaverse-app/pytest-quint-connect/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Quint 0.33.1](https://img.shields.io/badge/Quint-0.33.1-6E56CF)](https://quint.sh)
[![Typed](https://img.shields.io/badge/typing-py.typed-3776AB)](src/pytest_quint_connect/py.typed)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Agent skill](https://img.shields.io/badge/Agent_Skill-ready-2563EB)](skills/pytest-quint-connect/SKILL.md)
[![Documentation](https://readthedocs.org/projects/pytest-quint-connect/badge/?version=latest)](https://pytest-quint-connect.readthedocs.io/en/latest/)

**Model-based testing for Python under pytest.** Write the rules of a stateful API
once, as a small [Quint](https://quint.sh) model. pytest-quint-connect replays the
histories Quint generates from it against your real code, and fails when the code
disagrees.

### Set it up with your coding agent

Paste this into Claude Code, Codex, Cursor or another coding agent, in your repository:

```text
Add pytest-quint-connect to this repository. Fetch https://pytest-quint-connect.readthedocs.io/en/latest/agent-setup.md and follow it.
```

The agent picks one part of your code that keeps state, writes its rules down as a
short model, connects the model to the code, adds deliberate bugs the check must
catch, adds a CI job, and reports what it verified. **You review the model.** It is
the specification. An agent can write a wrong model, which then checks the wrong
rules, so read it before you merge. The tests themselves run without AI, in CI, on
every change. This is the split Quint's authors describe in
[Reliable Software in the LLM Era](https://quint.sh/posts/llm_era) and
[Quint Connect](https://quint.sh/posts/quint_connect). To do it by hand, follow the
[tutorial](docs/tutorial.md).

### In plain words

- **Model**: a short Quint file that says what your API should do: its operations,
  and what you should be able to read back after each one.
- **History**: a sequence of operations generated from the model, with the expected
  state after each step. Some are scenarios you name; the rest are sampled from a fixed seed.
- **Driver**: a few lines of Python that apply each step to your real code and read
  its state back. It never computes the answer.
- **Mutant**: a deliberately broken version of your code. Every run must catch it,
  which proves the check can fail.

Hand-written tests check the cases someone thought of, and a change can rewrite them
along with the code. A contract checks every generated history against rules kept
apart from the code. So a change by a person or a coding agent is judged against the
rules, not against tests the same change may have edited.
[Why, with real output](docs/why.md) · [Tutorial](docs/tutorial.md) ·
[Run it in CI](docs/ci.md)

[Read the documentation](https://pytest-quint-connect.readthedocs.io/en/latest/) · [Related tools](https://pytest-quint-connect.readthedocs.io/en/latest/ecosystem/)

This is a community Python integration in the spirit of
[Quint Connect](https://github.com/quint-co/quint-connect) (Rust) and
[Quint's model-based testing guide](https://quint.sh/docs/model-based-testing).
It is not an official Quint project and is not endorsed by its maintainers.

```text
test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest] FAILED

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

## Quick start

Install the plugin, plus Node.js 22+ with npm. On first use the plugin installs its
locked Quint 0.33.1 into a per-user cache. To use your own Quint, pass `--quint PATH`
or set `QUINT`.

```bash
pip install pytest-quint-connect
```

To try unreleased changes, install from Git instead:
`pip install "pytest-quint-connect @ git+https://github.com/gigaverse-app/pytest-quint-connect"`.

Generate a typed driver base from the model, then declare the contract:

```bash
pytest-quint-connect codegen quint-connect.json  # writes lru_cache_driver.py
```

```python
# test_cache_contract.py
from pytest_quint_connect import quint_contract

test_cache = quint_contract("quint-connect.json", driver="cache_driver")
```

Subclass the generated base. It has one typed method per model action and an
`observe()` typed as the model's observed state, so a type checker flags a driver
that has drifted from the spec:

```python
# conftest.py
import pytest
from lru_cache_driver import LruCacheDriver, Read, View  # generated
from my_cache import LRUCache


class CacheDriver(LruCacheDriver):
    def __init__(self) -> None:
        self.cache, self.last = LRUCache(2), {"tag": "NoRead"}

    def init(self) -> None: ...
    def inspect(self) -> None: ...

    def store(self, key: str, value: int) -> None:  # apply one model action to the real code
        self.cache.put(key, value)

    def fetch(self, key: str) -> None:
        value = self.cache.get(key)
        self.last: Read = {"tag": "Miss"} if value is None else {"tag": "Hit", "value": value}

    def observe(self) -> View:  # read the real state back
        return {"entries": [{"key": k, "value": v} for k, v in self.cache.items()], "lastRead": self.last}


@pytest.fixture
def cache_driver():
    return lambda context: CacheDriver()  # called once per history: fresh state each time
```

```bash
pytest --quint-require-complete
```

In CI, add the report as the next step. It keeps one comment on each pull request
with what ran, what failed and the command that reproduces each failure, and keeps a
README block like [this repository's](#measured) current:

```yaml
- run: pytest --quint-require-complete
- uses: gigaverse-app/pytest-quint-connect/report@vX.Y.Z  # your installed version; job permission: pull-requests: write
  if: ${{ !cancelled() }}
```

A hand-written driver that implements `step(step)` and `observe()` works too; see
[drivers](docs/drivers.md).

[examples/lru_cache](examples/lru_cache) is the complete version. It checks a
list-based cache and an `OrderedDict` refactor against the same generated
histories, catches a cache whose reads forget to refresh recency, and runs from
the installed wheel in CI.

## What a contract run does

```text
quint-connect.json ──► quint typecheck ──► quint test  (named witnesses)  ──► witness-*.itf.json ─┐
                                      └──► quint run --mbt (seeded samples) ──► sample-*.itf.json  ─┤
                                                                                                 ▼
pytest: test_x[witness-…] test_x[sample-N] ──► fresh driver per history ──► step → observe → compare
        test_x[mutant-…]   every trace through the mutant; only an observed mismatch counts
        test_x[coverage]   every declared action reached
                                                                                                 ▼
                     .artifacts/quint-connect/results.json, traces, logs, mismatch-*.json
```

- **Real pytest cases.** Each witness, sample, mutant and the coverage check is a
  parametrized case: selectable with `-k`, listed by `--collect-only`, reported in
  JUnit (with `quint_trace`, `quint_seed` and `quint_detected_by` properties),
  with ordinary fixture setup and teardown.
- **Fresh Quint, every run.** Traces are regenerated from the model with the
  manifest's seed and bounds; the output directory is emptied first. Collection
  never runs Quint; a Quint failure is a setup error for every case of that contract.
- **Mutation controls.** A mutant passes only when some generated history produces
  an observed-state mismatch. An exception while building or running the mutant fails
  the case: it is not detection. Reports call mutants *planted bugs*. Bugs a contract
  found and that were fixed (`bugs_found`) lead the report; a planted bug that
  reintroduces one is replayed through every history, and the report says how many diverge.
- **Complete or not.** With `--quint-require-complete`, the session fails unless every
  contract in `quint_required_contracts` ran every case and passed. A `-k`-filtered run
  is recorded as `partial`, a replay as `replay-only`; a crash leaves `running`, never
  an earlier `passed`.
- **Actionable failures.** A mismatch names the trace, state, action and arguments,
  a field-level expected/actual difference, the seed and a command that replays that
  exact trace (`--quint-replay`, optionally `--quint-mutant`). An exception from the
  driver keeps its own type and message and gains the same location as a note.

## To / Read

| To | Read |
| --- | --- |
| See why, with real output | [docs/why.md](docs/why.md) |
| Build your first contract step by step | [docs/tutorial.md](docs/tutorial.md) |
| Make contracts a required CI check | [docs/ci.md](docs/ci.md) |
| Set it up with a coding agent | [the agent setup procedure](skills/pytest-quint-connect/SKILL.md), also served raw at [agent-setup.md](https://pytest-quint-connect.readthedocs.io/en/latest/agent-setup.md) |
| Write a manifest | [docs/manifest.md](docs/manifest.md) |
| Write a driver and choose a step convention | [docs/drivers.md](docs/drivers.md) |
| Know what a passing run does and does not prove | [docs/limits.md](docs/limits.md) |
| Configure options and read the receipt | [docs/reference.md](docs/reference.md) |
| See it on production integrations | [pytest-obligation's report-merge contract](https://github.com/gigaverse-app/pytest-obligation/tree/main/docs/quint/report-merge), [pytest-selective's selection contract](https://github.com/gigaverse-app/pytest-selective/tree/main/contracts/selection) |

## Measured

This repository's own contracts, from the receipts of its last complete CI run. The block is
generated, and CI fails when it no longer matches the evidence:

<!-- pytest-quint-connect:start -->
<!-- pytest-quint-connect:contracts lru-cache,session-receipts -->
<a href="https://github.com/gigaverse-app/pytest-quint-connect" title="Mutants: deliberately broken copies of the code. Each must make some history diverge, proving the contract would notice that kind of bug"><img alt="Quint contracts: 2 contracts passed · 23 histories · 6/6 planted bugs caught" src="https://img.shields.io/badge/Quint_contracts-2_contracts_passed_%C2%B7_23_histories_%C2%B7_6%2F6_planted_bugs_caught-brightgreen"></a>

| | Contract | Cases passed | [Planted bugs caught](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/why.md#proof-that-the-tests-can-fail "Mutants: deliberately broken copies of the code. Each must make some history diverge, proving the contract would notice that kind of bug") |
| :-: | :-- | --: | --: |
| ✅ | `lru-cache` `test_lru_contract.py::test_lru_cache`<br><sub>15 histories (3 witnesses, 12 samples, seed 7) · every action covered · Quint 0.33.1</sub> | 17/17 | 1/1 |
| ✅ | `lru-cache` `test_lru_contract.py::test_ordered_refactor`<br><sub>15 histories (3 witnesses, 12 samples, seed 7) · every action covered · Quint 0.33.1</sub> | 17/17 | 1/1 |
| ✅ | `session-receipts` `test_session_contract.py::test_session_receipts`<br><sub>8 histories (4 witnesses, 4 samples, seed 11) · every action covered · Quint 0.33.1</sub> | 13/13 | 4/4 |

**Bugs these contracts found**

1. `session-receipts` · **Valid histories whose step calls one action without any { … } were rejected: Quint records mbt::actionTaken as step, and the event cross-check refused it.** Fixed in #3 · found by the first run of this contract · regression test `tests/test_replay.py::test_unbranched_step_is_recorded_under_the_step_name_and_still_replays`. No [planted bug](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/why.md#proof-that-the-tests-can-fail "Mutants: deliberately broken copies of the code. Each must make some history diverge, proving the contract would notice that kind of bug") re-checks it.

**[Planted bugs](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/why.md#proof-that-the-tests-can-fail "Mutants: deliberately broken copies of the code. Each must make some history diverge, proving the contract would notice that kind of bug") these contracts catch**

- `lru-cache` · `stale-read`: get() returns the value but does not refresh recency, so a just-read entry can be evicted next. Caught by `witness-evictsLeastRecentlyUsedTest` at state 3, after `fetch(key='a', value=0)`.
- `session-receipts` · `filtered-counts-as-complete`: a -k run that skipped cases is judged as if every case ran. Caught by `witness-filteredRunIsNeverPassedTest` at state 1, after `session(kind='filtered', strict=False)`.
- `session-receipts` · `replay-counts-as-complete`: replaying one saved trace no longer marks the session incomplete. Caught by `witness-replayIsNeverCompleteTest` at state 2, after `session(kind='replay', strict=True)`.
- `session-receipts` · `failed-session-keeps-old-receipt`: a failed or still-running session leaves the previous results.json in place. Caught by `witness-staleSuccessIsReplacedTest` at state 2, after `session(kind='noQuint', strict=False)`.
- `session-receipts` · `require-complete-ignored`: --quint-require-complete is accepted but never changes the exit code. Caught by `witness-filteredRunIsNeverPassedTest` at state 2, after `session(kind='filtered', strict=True)`.

<sub>Generated by [pytest-quint-connect](https://github.com/gigaverse-app/pytest-quint-connect) from contract receipts; [what a passing run proves](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/limits.md).</sub>
<!-- pytest-quint-connect:end -->

- [`lru-cache`](examples/lru_cache): the list and `OrderedDict` implementations pass the same
  histories, and the stale-read cache fails 4 of 15.
- [`session-receipts`](dogfood): each step is a real pytest session of the example, and the
  mutants break the plugin's own completeness and receipt logic.

Adopters measure their own contracts the same way; these figures come from their CI:

| Contract | Histories | Cases | Planted bugs (mutants) detected | Notes |
| --- | --- | --- | --- | --- |
| pytest-obligation `merge_reports` | 21 (5 witnesses, 16 samples) | 28 | 6/6 | same 21 histories, state for state, as the runner it replaced |
| [pytest-selective's selection](https://github.com/gigaverse-app/pytest-selective/tree/main/contracts/selection) | 19 (7 witnesses, 12 samples) | 23 | 3/3 | each step is a real Git commit selected by `affected()`; the mutants drop base-tree edges, stop after direct importers and ignore global inputs |

Quint 0.33.1, TypeScript backend. See [PROVENANCE.md](PROVENANCE.md) for where the
engine came from and how the baseline was reproduced.

### Duplicates kept out

Local hooks and the required PR check prevent new copies. The weekly audit
records progress against the accepted backlog. [Setup and cleanup workflow](CONTRIBUTING.md#duplication).

<!-- dry-lint-progress:start -->
<a href="https://github.com/gigaverse-app/dry-lint"><picture><source media="(prefers-color-scheme: dark)" srcset="https://dry-lint-counter.gigaverse-oss.workers.dev/v1/sign.svg?theme=dark&amp;key=bc47fb5de1a72359c95c190667ac9a84218d2ce822909a59d7e114ff2779542f&amp;name=gigaverse-app%2Fpytest-quint-connect&amp;cleaned=0&amp;remaining=5&amp;dismissed=0"><img alt="dry-lint in gigaverse-app/pytest-quint-connect: new duplicates kept out of its pull requests, 0 old duplicates cleaned up, 5 remaining" src="https://dry-lint-counter.gigaverse-oss.workers.dev/v1/sign.svg?key=bc47fb5de1a72359c95c190667ac9a84218d2ce822909a59d7e114ff2779542f&amp;name=gigaverse-app%2Fpytest-quint-connect&amp;cleaned=0&amp;remaining=5&amp;dismissed=0" width="100%"></picture></a>

**Reviewed duplication debt** · Counts from the committed baselines.

| Accepted pairs | Remaining | Retired | Dismissed |
| :-- | --: | --: | --: |
| Functions | 5 | 0 | 0 |
| Models | Not initialized | — | — |

[Accepted backlog](.dry-lint/baseline.json) · [Weekly audit](https://github.com/gigaverse-app/pytest-quint-connect/actions/workflows/dry-lint-audit.yml)

Retired means a side was removed, or (functions only) the judge now finds the pair separate. It is not a count of refactors. Dismissals stay separate; an uninitialized model baseline is unknown.
<!-- dry-lint-progress:end -->

## Meet Quinty

<p>
<img src="https://raw.githubusercontent.com/gigaverse-app/pytest-quint-connect/main/docs/assets/quinty/inspector-320.png" alt="Quinty in a deerstalker, inspecting a contract with a magnifier" width="150">
<img src="https://raw.githubusercontent.com/gigaverse-app/pytest-quint-connect/main/docs/assets/quinty/chef-320.png" alt="Quinty in a chef's hat, stamping approval on a passing contract" width="150">
<img src="https://raw.githubusercontent.com/gigaverse-app/pytest-quint-connect/main/docs/assets/quinty/explorer-320.png" alt="Quinty with a map, tracing the path where the code left the model" width="150">
</p>

Quinty is a starfish: five arms, a *quint*. On a pull request, Quinty stamps approval when every
history matched the model, follows the map to the spot where the code left the model when one did
not, and otherwise inspects the contract.

## Related projects

| Project | What it does |
| --- | --- |
| [pytest-obligation](https://github.com/gigaverse-app/pytest-obligation) | Conformance proofs for background work that must survive lost messages and dead workers |
| [Due](https://github.com/gigaverse-app/due) | Durable obligations for Python: PostgreSQL remembers what is owed |
| [pytest-selective](https://github.com/gigaverse-app/pytest-selective) | Explainable test impact analysis for pytest |
| [CI-esta](https://github.com/gigaverse-app/ci-esta) | CI observability and honest merge gates |
| [dry-lint](https://github.com/gigaverse-app/dry-lint) | Finds code that re-implements what the codebase already does |

## License

Apache 2.0. See [CONTRIBUTING.md](CONTRIBUTING.md) to work on pytest-quint-connect
itself and [RELEASING.md](RELEASING.md) for publishing.

---

[![CI measured by CI-esta](https://img.shields.io/badge/CI_measured_by-CI--esta-EA580C?logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI%2BPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iMTUiIGZpbGw9IiNFQTU4MEMiLz48cGF0aCBkPSJNMTUgMzRsMTEgMTEgMjAtMjIiIGZpbGw9Im5vbmUiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSI3IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48cGF0aCBkPSJNNDYgN2gxMGwtMTAgMTBoMTAiIGZpbGw9Im5vbmUiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIzLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg%3D%3D)](https://github.com/gigaverse-app/ci-esta)

[![Tests selected by pytest-selective](https://img.shields.io/badge/tests_selected_by-pytest--selective-2563EB)](https://github.com/gigaverse-app/pytest-selective)

<sub>Pull requests run only the [test lanes](.github/test-lanes.json) and test files a
change can affect, chosen by [pytest-selective](https://github.com/gigaverse-app/pytest-selective).
[CI-esta](https://github.com/gigaverse-app/ci-esta) reports this repository's CI timing,
keeps its test durations and owns its required gate. Neither is a dependency of
pytest-quint-connect. See [how CI runs here](docs/ci-toolkit.md).</sub>
