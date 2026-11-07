"""ITF decoding keeps every distinction Quint makes. Fixtures in tests/data are real Quint 0.33.1 output."""

import json
from pathlib import Path

import pytest

from pytest_quint_connect.itf import (
    ItfError,
    QList,
    QMap,
    QTuple,
    Record,
    Unserializable,
    Variant,
    decode,
    load_trace,
    parse_trace,
)

DATA = Path(__file__).parent / "data"


def test_scalars_and_big_integers() -> None:
    assert decode(True) is True
    assert decode("x") == "x"
    assert decode(7) == 7
    big = 2**200 + 1
    assert decode({"#bigint": str(big)}) == big
    assert decode({"#bigint": "-12"}) == -12


@pytest.mark.parametrize("bad", [{"#bigint": 3}, {"#bigint": "1.5"}, {"#bigint": "0x10"}])
def test_bigint_must_be_a_decimal_string(bad: object) -> None:
    with pytest.raises(ItfError):
        decode(bad)


def test_map_keys_keep_their_type() -> None:
    numeric = decode({"#map": [[1, "a"]]})
    textual = decode({"#map": [["1", "a"]]})
    assert numeric != textual
    assert numeric[1] == "a" and 1 not in textual and "1" in textual
    assert isinstance(numeric, QMap)


def test_map_keys_may_be_compound_and_distinct() -> None:
    value = decode(
        {
            "#map": [
                [{"#tup": [1, "x"]}, "tuple key"],
                [[1, "x"], "list key"],
                [{"#set": [1]}, "set key"],
                [{"a": 1}, "record key"],
            ]
        }
    )
    assert len(value) == 4
    assert value[QTuple((1, "x"))] == "tuple key"
    assert value[QList((1, "x"))] == "list key"
    assert value[frozenset({1})] == "set key"
    assert value[Record({"a": 1})] == "record key"


def test_duplicate_map_keys_are_malformed() -> None:
    with pytest.raises(ItfError, match="duplicate"):
        decode({"#map": [[1, "a"], [1, "b"]]})


def test_lists_tuples_and_records_never_collide() -> None:
    as_list = decode([1, 2])
    as_tuple = decode({"#tup": [1, 2]})
    as_record = decode({"a": 1, "b": 2})
    swapped = decode({"b": 1, "a": 2})
    assert as_list != as_tuple
    assert as_tuple != (1, 2) and as_list != (1, 2)
    assert as_record != swapped
    assert as_record != as_tuple
    assert len({as_list, as_tuple, as_record, swapped}) == 4


def test_record_field_order_is_irrelevant() -> None:
    assert decode({"a": 1, "b": 2}) == decode({"b": 2, "a": 1})


def test_sets_ignore_order_and_nest() -> None:
    assert decode({"#set": [3, 1, 2]}) == decode({"#set": [1, 2, 3]})
    nested = decode({"#set": [{"#set": [1]}, {"#set": []}]})
    assert nested == frozenset({frozenset({1}), frozenset()})


def test_variants_including_unit_variants() -> None:
    unit = decode({"tag": "None", "value": {"#tup": []}})
    some = decode({"tag": "Some", "value": {"#bigint": "4"}})
    assert unit == Variant("None") and unit.is_unit
    assert some == Variant("Some", 4) and not some.is_unit
    assert decode({"tag": "Hit", "value": {"k": 1}}) == Variant("Hit", Record({"k": 1}))


def test_record_with_non_identifier_fields() -> None:
    value = decode({"my-field": 1, "mbt::x": True})
    assert value["my-field"] == 1 and value["mbt::x"] is True


def test_unserializable_is_kept_but_marked() -> None:
    assert decode({"#unserializable": "Nat"}) == Unserializable("Nat")


@pytest.mark.parametrize(
    "bad",
    [1.5, None, {"#weird": 1}, {"#set": 1}, {"#map": [[1]]}, {"#tup": [], "x": 1}],
)
def test_malformed_values_are_rejected(bad: object) -> None:
    with pytest.raises(ItfError):
        decode(bad)


def test_decoded_values_are_immutable() -> None:
    record = decode({"a": 1})
    with pytest.raises(AttributeError):
        record.a = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["a"] = 2  # type: ignore[index]


def test_real_mbt_trace_from_quint_decodes() -> None:
    # itf-py 0.5.0 raises ValueError on this file: it builds a namedtuple from the
    # unit variant's {"#tup": []} value, and "#tup" is not an identifier.
    trace = load_trace(DATA / "mbt" / "probe-mbt.itf.json")
    assert trace.status == "ok"
    assert trace.vars[-2:] == ("mbt::actionTaken", "mbt::nondetPicks")
    first, second = trace.states[0], trace.states[1]
    assert first["mbt::actionTaken"] == "init"
    assert first["mbt::nondetPicks"]["k"] == Variant("None")
    assert second["mbt::nondetPicks"]["k"] == Variant("Some", "b")
    assert first["pair"] == QTuple((0, ""))
    assert first["index"] == QMap() and first["tags"] == frozenset()


def test_real_witness_trace_from_quint_decodes() -> None:
    trace = load_trace(DATA / "lru" / "witness-evicts.itf.json")
    assert trace.status == "passed"
    last = trace.states[-1]
    assert last["event"].op == "inspect"
    assert last["observed"]["entries"] == QList((Record({"key": "a", "value": 1}), Record({"key": "c", "value": 0})))


def test_trace_structure_is_validated(tmp_path: Path) -> None:
    good = json.loads((DATA / "lru" / "witness-evicts.itf.json").read_text())
    missing_var = json.loads(json.dumps(good))
    del missing_var["states"][1]["observed"]
    with pytest.raises(ItfError, match="variables differ"):
        parse_trace(missing_var)
    wrong_index = json.loads(json.dumps(good))
    wrong_index["states"][1]["#meta"]["index"] = 5
    with pytest.raises(ItfError, match="index"):
        parse_trace(wrong_index)
    with pytest.raises(ItfError):
        parse_trace({"states": []})
    not_json = tmp_path / "x.itf.json"
    not_json.write_text("{", encoding="utf-8")
    with pytest.raises(ItfError, match="not JSON"):
        load_trace(not_json)
