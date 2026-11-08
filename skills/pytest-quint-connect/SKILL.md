---
name: pytest-quint-connect
description: Connect a Quint specification to a real Python implementation with pytest-quint-connect, so Quint-generated histories run as pytest cases. Use to add model-based tests for stateful Python code, to write a contract manifest and driver, to add mutation controls, to wire a required CI check, or to debug a replay mismatch.
license: Apache-2.0
---

# Connect a Quint model to Python code

Work in the user's repository. Find the stateful behavior to check and its public
API before writing anything. Read
[the manifest reference](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/manifest.md),
[drivers](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/drivers.md) and
[limits](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/limits.md).
Copy the shape of
[examples/lru_cache](https://github.com/gigaverse-app/pytest-quint-connect/tree/main/examples/lru_cache).

1. **Model the boundary, not the code.** In the `.qnt` file, keep an `event` record
   (`op` plus arguments) and an `observed` record holding only what the real API
   exposes. Every action sets both. Add a read-only `inspect` action for witness
   assertions. Write `run …Test` witnesses for the scenarios the user cares about,
   and an invariant for sampling. Typecheck with Quint 0.33.1.
2. **Write the manifest** beside the model: declare every action with the API it
   drives, the witnesses, seeded bounds, the implementation `sources`, and `attest`
   when a stale installed package could shadow the checkout.
3. **Write the driver.** `step()` calls the real API with `step.args`; `observe()`
   reads real state back as plain data shaped like `observed`. Never compute the
   expected value in the driver. Return a factory from the driver fixture so each
   history gets fresh state; use other fixtures (database, `tmp_path`) as needed.
4. **Add mutants** for each property that matters: a deliberately wrong variant built
   from `context.mutant`, declared as `{"name", "description"}` so reports say what it
   catches. Confirm each is detected.
   When the contract surfaces a real bug and you fix it, add a `bugs_found` entry
   (`summary`, `fixed_in`, and `found_by`, `regression_test` and a `mutant` that
   reintroduces it when you have them); never record an invented fault as found. A mutant that survives means the
   observation or the histories cannot see that fault: fix that, do not delete the mutant.
5. **Declare the test** with `quint_contract(manifest, driver=…)`, list the contract
   in `quint_required_contracts`, and run `pytest --quint-require-complete` in CI with
   Node.js 22+ available. Add `gigaverse-app/pytest-quint-connect/report@v1` as the next
   step (`if: ${{ !cancelled() }}`, job permission `pull-requests: write`): it posts one
   sticky PR comment, uploads the receipts and mismatches, and keeps a README block
   current. Add the `<!-- pytest-quint-connect:start -->` / `<!-- pytest-quint-connect:end -->`
   lines to the README, fill them once with
   `pytest-quint-connect report '**/.artifacts/quint-connect/results.json' --readme README.md`,
   and commit the result
   ([CI setup](https://github.com/gigaverse-app/pytest-quint-connect/blob/main/docs/ci.md)).
6. **On a mismatch**, read the reported state, action and field differences, then
   rerun the printed `--quint-replay` command. Decide whether the code or the model is
   wrong; never weaken the model, drop a witness or remove an observed field to pass.

Report what was verified: histories generated, cases passed, mutants detected, and
the limits from the docs that apply.
