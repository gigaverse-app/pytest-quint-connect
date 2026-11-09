"""Compare a decoded model expectation with an observation read from runtime state.

The expectation fixes the type. A driver returns plain Python data; each Quint type
accepts only the Python shapes listed here, so a mismatch in kind is reported rather
than coerced:

* ``int`` accepts ``int`` but not ``bool``; ``bool`` accepts only ``bool``.
* records accept a mapping with exactly the same string keys.
* maps accept a mapping whose keys match the expected keys by these same rules.
* sets accept ``set`` or ``frozenset``; lists and tuples accept ``list`` or ``tuple``.
* variants accept a :class:`~pytest_quint_connect.itf.Variant` or a mapping with
  ``tag`` and ``value``; a unit variant may omit ``value`` or give ``None``.
"""

import json
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any

from .itf import QList, QMap, QTuple, Record, Unserializable, Variant

__all__ = ["Difference", "differences", "plain", "quint_text"]


@dataclass(frozen=True)
class Difference:
    """One place where the observation differs from the expectation."""

    path: str
    expected: Any
    actual: Any
    reason: str

    def render(self) -> str:
        where = self.path or "<observation>"
        return f"{where}: {self.reason}\n    expected: {_show(self.expected)}\n    actual:   {_show(self.actual)}"


_MISSING = object()


def _show(value: Any) -> str:
    if value is _MISSING:
        return "<missing>"
    return repr(plain(value))


def plain(value: Any) -> Any:
    """Render a decoded value as plain JSON-like Python, for reports and artifacts."""
    if isinstance(value, Variant):
        return {"tag": value.tag, "value": plain(value.value)}
    if isinstance(value, Record):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, QMap):
        return {"#map": [[plain(key), plain(item)] for key, item in value.items()]}
    if isinstance(value, QTuple):
        return {"#tup": [plain(item) for item in value]}
    if isinstance(value, QList):
        return [plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [plain(item) for item in value]
        return {"#set": sorted(items, key=lambda item: repr(item))}
    if isinstance(value, Unserializable):
        return {"#unserializable": value.text}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def quint_text(value: object) -> str:
    """A value as :func:`plain` writes it, in Quint's syntax: ``Hit(1)``, ``NoRead``, ``{ key: "a" }``.

    Reports and the receipt's state machine show model values to people in this one form.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(map(quint_text, value)) + "]"
    if not isinstance(value, dict):
        return str(value)
    # ITF, and so plain(), writes a variant as exactly {tag, value}; a unit variant's value is the empty tuple.
    if set(value) == {"tag", "value"}:
        unit = value["value"] == {"#tup": []}
        return str(value["tag"]) if unit else f"{value['tag']}({quint_text(value['value'])})"
    if set(value) == {"#tup"}:
        return "(" + ", ".join(map(quint_text, value["#tup"])) + ")"
    if set(value) == {"#set"}:
        return "Set(" + ", ".join(map(quint_text, value["#set"])) + ")"
    if set(value) == {"#map"}:
        return "Map(" + ", ".join(f"{quint_text(key)} -> {quint_text(item)}" for key, item in value["#map"]) + ")"
    if set(value) == {"#unserializable"}:
        return str(value["#unserializable"])
    if not value:
        return "{}"
    return "{ " + ", ".join(f"{key}: {quint_text(item)}" for key, item in value.items()) + " }"


def differences(expected: Any, actual: Any, path: str = "") -> list[Difference]:
    """Return every difference between ``expected`` (decoded ITF) and ``actual`` (runtime data)."""
    if isinstance(expected, Unserializable):
        return [Difference(path, expected, actual, "the model emitted an unserializable value")]
    if isinstance(expected, bool):
        if isinstance(actual, bool) and actual == expected:
            return []
        return [Difference(path, expected, actual, _kind("bool", actual))]
    if isinstance(expected, int):
        if isinstance(actual, int) and not isinstance(actual, bool) and actual == expected:
            return []
        return [Difference(path, expected, actual, _kind("int", actual))]
    if isinstance(expected, str):
        if isinstance(actual, str) and actual == expected:
            return []
        return [Difference(path, expected, actual, _kind("str", actual))]
    if isinstance(expected, Variant):
        return _variant(expected, actual, path)
    if isinstance(expected, Record):
        return _record(expected, actual, path)
    if isinstance(expected, QMap):
        return _map(expected, actual, path)
    if isinstance(expected, frozenset):
        return _set(expected, actual, path)
    if isinstance(expected, (QList, QTuple)):
        return _sequence(expected, actual, path)
    raise TypeError(f"not a decoded ITF value at {path or '<observation>'}: {expected!r}")


def _kind(name: str, actual: Any) -> str:
    return f"expected {name}, got {type(actual).__name__}" if not _same_kind(name, actual) else "values differ"


def _same_kind(name: str, actual: Any) -> bool:
    if name == "bool":
        return isinstance(actual, bool)
    if name == "int":
        return isinstance(actual, int) and not isinstance(actual, bool)
    return isinstance(actual, str)


def _equal(expected: Any, actual: Any) -> bool:
    return not differences(expected, actual)


def _variant(expected: Variant, actual: Any, path: str) -> list[Difference]:
    if isinstance(actual, Variant):
        tag, value, has_value = actual.tag, actual.value, True
    elif isinstance(actual, Mapping) and "tag" in actual and set(actual) <= {"tag", "value"}:
        tag, value, has_value = actual["tag"], actual.get("value"), "value" in actual
    else:
        return [Difference(path, expected, actual, f"expected variant {expected.tag}, got {type(actual).__name__}")]
    if tag != expected.tag:
        return [Difference(path, expected, actual, f"variant {tag!r} instead of {expected.tag!r}")]
    if expected.is_unit and (not has_value or value is None or value == () or value == QTuple()):
        return []
    return differences(expected.value, value, f"{path}.{expected.tag}" if path else expected.tag)


def _record(expected: Record, actual: Any, path: str) -> list[Difference]:
    if not isinstance(actual, Mapping):
        return [Difference(path, expected, actual, f"expected record, got {type(actual).__name__}")]
    found: list[Difference] = []
    for key in expected:
        child = f"{path}.{key}" if path else key
        if key not in actual:
            found.append(Difference(child, expected[key], _MISSING, "field missing from observation"))
        else:
            found.extend(differences(expected[key], actual[key], child))
    for key in actual:
        if key not in expected:
            child = f"{path}.{key}" if path else str(key)
            found.append(Difference(child, _MISSING, actual[key], "field not in the model"))
    return found


def _map(expected: QMap, actual: Any, path: str) -> list[Difference]:
    if not isinstance(actual, Mapping):
        return [Difference(path, expected, actual, f"expected map, got {type(actual).__name__}")]
    found: list[Difference] = []
    unmatched = list(actual)
    for key, value in expected.items():
        match = next((candidate for candidate in unmatched if _equal(key, candidate)), _MISSING)
        child = f"{path}[{plain(key)!r}]"
        if match is _MISSING:
            found.append(Difference(child, value, _MISSING, "key missing from observation"))
            continue
        unmatched.remove(match)
        found.extend(differences(value, actual[match], child))
    for key in unmatched:
        found.append(Difference(f"{path}[{key!r}]", _MISSING, actual[key], "key not in the model"))
    return found


def _set(expected: frozenset[Any], actual: Any, path: str) -> list[Difference]:
    if not isinstance(actual, Set):
        return [Difference(path, expected, actual, f"expected set, got {type(actual).__name__}")]
    unmatched = list(actual)
    missing = []
    for item in expected:
        match = next((index for index, candidate in enumerate(unmatched) if _equal(item, candidate)), None)
        if match is None:
            missing.append(item)
        else:
            unmatched.pop(match)
    if missing or unmatched:
        reason = f"set differs: {len(missing)} expected element(s) missing, {len(unmatched)} unexpected"
        return [Difference(path, expected, actual, reason)]
    return []


def _sequence(expected: QList | QTuple, actual: Any, path: str) -> list[Difference]:
    name = "list" if isinstance(expected, QList) else "tuple"
    if (
        not isinstance(actual, (list, tuple))
        or isinstance(actual, (QList, QTuple))
        and type(actual) is not type(expected)
    ):
        return [Difference(path, expected, actual, f"expected {name}, got {type(actual).__name__}")]
    if len(actual) != len(expected):
        return [Difference(path, expected, actual, f"{name} length {len(actual)} instead of {len(expected)}")]
    found: list[Difference] = []
    for index, (want, got) in enumerate(zip(expected, actual, strict=True)):
        found.extend(differences(want, got, f"{path}[{index}]"))
    return found
