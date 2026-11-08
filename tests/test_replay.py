"""The replay engine on real Quint traces, without running Quint."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from lru_support import DATA, IMPLEMENTATIONS, MANIFEST, CacheDriver

from pytest_quint_connect import HistoryContext, ImportEscape, ReplayMismatch, Step, TraceError, replay_trace
from pytest_quint_connect.contract import Attestation, load_contract
from pytest_quint_connect.itf import load_trace
from pytest_quint_connect.replay import steps

CONTRACT = load_contract(MANIFEST)
SAMPLE = DATA / "lru" / "sample-5.itf.json"
WITNESS = DATA / "lru" / "witness-evicts.itf.json"


def factory(name: str = "list"):
    return lambda context: CacheDriver(IMPLEMENTATIONS[context.mutant or name])


def edited(tmp_path: Path, source: Path, edit) -> Path:
    document = json.loads(source.read_text())
    edit(document)
    path = tmp_path / source.name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_correct_and_refactored_caches_conform() -> None:
    for name in ("list", "ordered"):
        for trace in (SAMPLE, WITNESS):
            result = replay_trace(trace, CONTRACT, factory(name))
            assert result.actions[0] == "init" and len(result.actions) == len(load_trace(trace).states)


def test_behavioral_fault_is_a_mismatch_with_a_located_diff() -> None:
    with pytest.raises(ReplayMismatch) as caught:
        replay_trace(WITNESS, CONTRACT, factory("stale-read"), reproduce="pytest x --quint-replay y")
    mismatch = caught.value
    assert mismatch.state == 3 and mismatch.action == "fetch"
    assert {d.path for d in mismatch.diff} == {
        "entries[0].key",
        "entries[0].value",
        "entries[1].key",
        "entries[1].value",
    }
    text = str(mismatch)
    assert "seed:   7" in text and "reproduce: pytest x --quint-replay y" in text
    assert json.loads(json.dumps(mismatch.as_json()))["expected"]["entries"][0] == {"key": "b", "value": 2}


def test_changed_expectation_in_a_real_trace_is_rejected(tmp_path: Path) -> None:
    def corrupt(document: dict) -> None:
        document["states"][2]["observed"]["lastRead"] = {"tag": "Miss", "value": {"#tup": []}}

    with pytest.raises(ReplayMismatch, match="lastRead"):
        replay_trace(edited(tmp_path, WITNESS, corrupt), CONTRACT, factory())


@pytest.mark.parametrize("keep", [0, 1])
def test_empty_and_single_state_traces_fail(tmp_path: Path, keep: int) -> None:
    path = edited(tmp_path, SAMPLE, lambda document: document.update(states=document["states"][:keep]))
    with pytest.raises(TraceError, match="at least two"):
        replay_trace(path, CONTRACT, factory())


def test_undeclared_action_fails(tmp_path: Path) -> None:
    def rename(document: dict) -> None:
        document["states"][1]["event"]["op"] = "delete"
        document["states"][1]["mbt::actionTaken"] = "delete"

    with pytest.raises(TraceError, match="undeclared action 'delete'"):
        replay_trace(edited(tmp_path, SAMPLE, rename), CONTRACT, factory())


def test_event_label_must_agree_with_the_action_quint_took(tmp_path: Path) -> None:
    def mislabel(document: dict) -> None:
        state = document["states"][1]
        state["mbt::actionTaken"] = "store" if state["event"]["op"] == "fetch" else "fetch"

    with pytest.raises(TraceError, match="wrong event"):
        replay_trace(edited(tmp_path, SAMPLE, mislabel), CONTRACT, factory())


def test_unbranched_step_is_recorded_under_the_step_name_and_still_replays() -> None:
    # Found by the dogfood contract: session.qnt's `step` calls one action without `any`, so
    # Quint 0.33.1 records mbt::actionTaken as "step"; the event still names the real action.
    contract = load_contract(Path(__file__).resolve().parents[1] / "dogfood" / "quint-connect.json")
    trace = load_trace(DATA / "session" / "sample-0.itf.json")
    assert [state["mbt::actionTaken"] for state in trace.states][1:] == ["step"] * (len(trace.states) - 1)
    assert [step.action for step, _ in steps(trace, contract)][1:] == ["session"] * (len(trace.states) - 1)


def test_mbt_steps_carry_unwrapped_picks() -> None:
    planned = list(steps(load_trace(SAMPLE), CONTRACT))
    step, _ = planned[1]
    assert step.picks is not None and set(step.picks) == {"key", "value"}
    assert step.args["key"] == step.picks["key"]
    init, _ = planned[0]
    assert init.picks is not None and dict(init.picks) == {"key": None, "value": None}


class Recording:
    def __init__(self, fail_at: int | None = None) -> None:
        self.inner = CacheDriver(IMPLEMENTATIONS["list"])
        self.fail_at = fail_at
        self.closed = False

    def step(self, step: Step) -> None:
        if step.index == self.fail_at:
            raise RuntimeError("driver crashed")
        self.inner.step(step)

    def observe(self) -> Any:
        return self.inner.observe()

    def close(self) -> None:
        self.closed = True


def test_each_history_gets_a_fresh_driver_and_work_dir_and_is_closed_on_failure() -> None:
    made: list[tuple[Recording, HistoryContext]] = []

    def start(context: HistoryContext) -> Recording:
        assert context.work_dir.is_dir() and not any(context.work_dir.iterdir())
        driver = Recording(fail_at=2 if made else None)
        made.append((driver, context))
        return driver

    replay_trace(SAMPLE, CONTRACT, start)
    with pytest.raises(RuntimeError, match="driver crashed"):
        replay_trace(SAMPLE, CONTRACT, start)
    assert [driver.closed for driver, _ in made] == [True, True]
    assert made[0][0] is not made[1][0]
    assert not made[0][1].work_dir.exists() and not made[1][1].work_dir.exists()


def test_a_driver_error_names_the_history_and_step_it_happened_in() -> None:
    with pytest.raises(RuntimeError, match="driver crashed") as caught:
        replay_trace(
            WITNESS,
            CONTRACT,
            lambda context: Recording(fail_at=3),
            mutant="stale-read",
            reproduce="pytest x --quint-replay y",
        )
    # The driver's own exception propagates unchanged; the location is a note on it.
    assert type(caught.value) is RuntimeError and str(caught.value) == "driver crashed"
    note = "\n".join(caught.value.__notes__)
    assert "Quint contract 'lru-cache': the driver raised during this history" in note
    assert f"trace:  {WITNESS}" in note
    assert "state:  3, applying action fetch(key='a', value=0)" in note
    assert "mutant: stale-read" in note and "seed:   7" in note
    assert "reproduce: pytest x --quint-replay y" in note


def test_factory_must_return_a_driver() -> None:
    with pytest.raises(TypeError, match="lacks step"):
        replay_trace(SAMPLE, CONTRACT, lambda context: object())


def test_import_attestation_rejects_an_escaped_module(tmp_path: Path) -> None:
    attested = CONTRACT.__class__(**{**CONTRACT.__dict__, "attest": Attestation("lru_cache", tmp_path)})
    with pytest.raises(ImportEscape, match="outside the attested source"):
        replay_trace(SAMPLE, attested, factory())
    assert "lru_cache" in sys.modules
