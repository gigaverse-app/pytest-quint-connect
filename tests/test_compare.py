"""Comparison is type-directed by the model and reports each differing path."""

import pytest

from pytest_quint_connect.compare import differences
from pytest_quint_connect.itf import decode


def paths(expected_json: object, actual: object) -> list[str]:
    return [difference.path for difference in differences(decode(expected_json), actual)]


def test_equal_nested_observation() -> None:
    expected = {"entries": [{"key": "a", "value": 1}], "last": {"tag": "Hit", "value": 1}, "n": {"#bigint": "1"}}
    actual = {"entries": [{"key": "a", "value": 1}], "last": {"tag": "Hit", "value": 1}, "n": 1}
    assert paths(expected, actual) == []


def test_bool_and_int_are_not_interchangeable() -> None:
    assert paths(1, True) == [""]
    assert paths(True, 1) == [""]
    assert paths(False, 0) == [""]


def test_record_fields_missing_and_extra() -> None:
    found = differences(decode({"a": 1, "b": 2}), {"a": 1, "c": 3})
    assert sorted((d.path, d.reason) for d in found) == [
        ("b", "field missing from observation"),
        ("c", "field not in the model"),
    ]


def test_map_keys_must_match_with_their_types() -> None:
    expected = {"#map": [[1, "x"]]}
    assert paths(expected, {1: "x"}) == []
    assert paths(expected, {"1": "x"}) == ["[1]", "['1']"]


def test_variant_forms() -> None:
    assert paths({"tag": "Miss", "value": {"#tup": []}}, {"tag": "Miss"}) == []
    assert paths({"tag": "Miss", "value": {"#tup": []}}, {"tag": "Miss", "value": None}) == []
    assert paths({"tag": "Hit", "value": 2}, {"tag": "Hit", "value": 3}) == ["Hit"]
    assert paths({"tag": "Hit", "value": 2}, {"tag": "Miss"}) == [""]
    assert paths({"tag": "Hit", "value": 2}, 2) == [""]


def test_sets_ignore_order_but_not_kind() -> None:
    assert paths({"#set": [1, 2]}, {2, 1}) == []
    assert paths({"#set": [1, 2]}, [1, 2]) == [""]
    assert paths({"#set": [1, 2]}, {1, 3}) == [""]


def test_sequences_compare_by_position() -> None:
    assert paths([1, 2], (1, 2)) == []
    assert paths({"#tup": [1, "a"]}, [1, "a"]) == []
    assert paths([1, 2], [2, 1]) == ["[0]", "[1]"]
    assert paths([1, 2], [1]) == [""]


def test_rendered_difference_names_both_values() -> None:
    (difference,) = differences(decode({"a": {"#bigint": "2"}}), {"a": 3})
    text = difference.render()
    assert "a: values differ" in text and "expected: 2" in text and "actual:   3" in text


def test_unserializable_expectation_never_matches() -> None:
    assert differences(decode({"#unserializable": "Int"}), "Int")


def test_non_itf_expectation_is_a_programming_error() -> None:
    with pytest.raises(TypeError):
        differences(object(), 1)
