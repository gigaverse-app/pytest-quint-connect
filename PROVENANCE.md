# Provenance

pytest-quint-connect was extracted from two working, application-specific Quint
replay runners at Gigaverse, with authorization to open-source them:

| Source | Commit | Files |
| --- | --- | --- |
| [pytest-obligation](https://github.com/gigaverse-app/pytest-obligation) | `6c923bff2c7fd1220cb837d14fbbf565e1b5c0e5` | `scripts/quint/replay.py`, `scripts/quint/test_replay.py`, `scripts/quint/README.md`, `scripts/quint/package.json`, `scripts/quint/package-lock.json`, `docs/quint/report-merge/` |
| [Due](https://github.com/gigaverse-app/due) | `3709d585ffc6dd391e823e81d8000304f3ff906f` | `scripts/ci/check_quint_report.py`, `docs/quint/report-provenance/` |

Both sources are Apache-2.0 projects of the same copyright holder.

## What changed in the extraction

- The generation, ITF decoding and replay engine is separated from pytest and has no
  knowledge of either source repository: their hardcoded repository layout,
  `pytest_obligation` import check, `docs/quint` discovery and source-mutation helper
  became manifest fields (`root`, `attest`, `sources`) and consumer-owned driver code.
- The single opaque runner became pytest cases: one per witness, sampled history,
  mutant and action-coverage check, with fixtures, JUnit, selection and saved-trace replay.
- The ITF decoder was rewritten. The original stringified map keys and decoded sets
  to sorted lists, which is correct only for its one contract. The new decoder keeps
  every Quint type distinct. [itf-py](https://github.com/konnov/itf-py) 0.5.0 was
  evaluated and not used: it decodes records to namedtuples (so `{a: 1, b: 2}`,
  `{b: 1, a: 2}` and the tuple `(1, 2)` compare equal), and it raises on every Quint
  0.33.1 trace that contains a unit variant (`{"#tup": []}` is not a valid field name).
- `--mbt` metadata is supported alongside the original explicit `event`/`observed`
  convention, and cross-checked against it when both are present.
- Due's runner contributed the checks for required exit status and per-trace output
  status. Its report-provenance contract replays a shell workflow and has not been
  migrated.

## Baseline reproduction

On pytest-obligation `6c923bff`, with Quint 0.33.1 on Windows,
`python scripts/quint/replay.py --topic report-merge` passed **21 generated
histories** (5 witnesses, 16 samples) and **detected all six mutations**. The same
contract under pytest-quint-connect, from an installed wheel, generated the same 21
histories (every `event` and `observed` value identical, state for state), passed
all 28 cases and detected all six mutations. The original runner's negative
controls (altered observation, empty trace, missing generation, missing tool with a
stale success receipt, setup error during a mutant, edited historical model) are
covered by `tests/test_replay.py` and `tests/test_plugin.py`.

Copyright Gigaverse. Licensed under Apache-2.0; see [LICENSE](LICENSE).
