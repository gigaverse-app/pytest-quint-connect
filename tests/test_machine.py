"""A contract's state machine, merged from real Quint traces of the LRU example."""

from dataclasses import replace
from pathlib import Path

import pytest
from lru_support import MANIFEST

from pytest_quint_connect.contract import load_contract
from pytest_quint_connect.machine import machine, shape
from pytest_quint_connect.replay import TraceError

DATA = Path(__file__).parent / "data" / "lru"
# Real Quint output from before the model named its states: one witness and one sampled history.
TRACES = [DATA / "witness-evicts.itf.json", DATA / "sample-5.itf.json"]


def test_a_value_is_drawn_by_its_shape() -> None:
    observed = {
        "entries": [{"key": "a", "value": 1}],
        "lastRead": {"tag": "Hit", "value": 1},
        "seen": {"#set": ["a", "b"]},
        "owners": {"#map": []},
        "pair": {"#tup": ["x", True]},
        "nested": {"ok": False, "count": 3},
    }
    assert shape(observed) == [
        ("entries", 1, "1 item"),
        ("lastRead", "Hit", "Hit"),
        ("seen", 2, "2 entries"),
        ("owners", 0, "empty"),
        # Written the one way reports write model values (compare.quint_text): strings keep their quotes.
        ("pair", '("x", true)', '("x", true)'),
        ("nested.ok", False, "false"),
        ("nested.count", 3, "3"),
    ]
    assert shape([]) == [("observation", 0, "empty")]


def test_a_model_that_names_no_states_is_merged_by_the_shape_of_what_it_observes() -> None:
    contract = replace(load_contract(MANIFEST), state_names=None)
    merged = machine(contract, TRACES, None)
    assert merged["histories"] == 2 and merged["named_by"] is None and merged["initial"] == [0]
    assert merged["states"][0] == {"fields": [["entries", "empty"], ["lastRead", "NoRead"]]}
    assert {"fields": [["entries", "2 items"], ["lastRead", "Hit"]]} in merged["states"]
    # Same seed, same machine: the merge keeps the order histories first reach each state.
    assert machine(contract, TRACES, None) == merged


def test_an_arrow_keeps_only_the_arguments_every_call_on_it_shared() -> None:
    contract = replace(load_contract(MANIFEST), state_names=None)
    parameters = {"store": ("key", "value"), "fetch": ("key",)}

    def first_store(traces: list[Path]) -> dict:
        merged = machine(contract, traces, parameters)
        edge = next(edge for edge in merged["transitions"] if (edge["from"], edge["to"]) == (0, 1))
        return next(entry for entry in edge["actions"] if entry["action"] == "store")

    # One history: the witness's store(a, 1) is the only call from an empty cache, so all its arguments show.
    assert first_store(TRACES[:1]) == {"action": "store", "arguments": {"key": "a", "value": 1}}
    # The sampled history stores a different key first: the arrow keeps only what both calls agree on.
    assert first_store(TRACES)["arguments"] == {}


def test_a_named_state_variable_the_trace_lacks_is_an_error() -> None:
    # These traces predate the model's `fill` variable: naming states by it must fail, not fall back.
    with pytest.raises(TraceError, match="no state_names variable 'fill'"):
        machine(load_contract(MANIFEST), TRACES, None)
