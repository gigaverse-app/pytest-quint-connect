# What a passing run shows

A passing contract run shows that, for every generated history, the implementation
reached the model's expected observation after every step, and that each declared
mutant produced at least one observed difference. That is executable drift detection
for the histories that were generated. It is not a proof.

- **Bounded exploration.** Quint samples `n_traces` histories of at most `max_steps`
  steps from a fixed seed, plus the named witnesses. Behavior outside those histories
  is unchecked. Raise bounds or add witnesses for paths that matter.
- **Trusted adapters.** The driver's step mapping and observation projection are
  trusted. A projection that omits a field cannot detect a bug in it. Mutants are the
  check on this: keep at least one for each property you rely on.
- **Declared bugs.** For a found bug with a `mutant`, the receipt proves how many of
  the generated histories diverge through that mutant under the manifest's seed and
  bounds. That the mutant is the bug fixed in `fixed_in`, and the rest of the entry,
  are the manifest author's account, not something the run checks.
- **Serial histories.** Each history replays one step at a time in one thread.
  Concurrency, interleavings, timing, crashes between steps and asynchronous
  interruption are not modeled unless your driver and model represent them explicitly.
- **Observations only.** Only the projected state is compared. Logs, side effects
  outside the projection, and performance are not.
- **Quint version.** The plugin requires an exact Quint version (0.33.1 by default) and
  ships the npm lock for it. Other versions are not supported until they are tested.
- **xdist.** Contract tests refuse to run in pytest-xdist workers: workers would
  regenerate into the same trace directory and judge completeness per process. Run
  them without `-n`, or deselect them from a parallel run with `-m "not quint_contract"`.
- **ITF ambiguity.** ITF encodes a variant as `{"tag": …, "value": …}`, the same shape
  as a record with exactly those two fields. Such objects always decode as variants.
  `#unserializable` values can never match an observation.

- **Generated drivers.** The fingerprint that marks a generated base stale covers the
  main model file and the manifest's step mapping, not modules the model imports.
  Generated record types are `TypedDict`s, which are unhashable, so a `Set` of records
  cannot be returned as a Python set.

pytest-quint-connect does not use or require any proof tool. It complements, and does
not replace, ordinary unit and integration tests.
