"""Faithful decoding of Informal Trace Format (ITF) values and traces.

ITF is specified by Apalache ADR-015 and emitted by ``quint run --out-itf`` and
``quint test --out-itf``. Each Quint type decodes to a distinct immutable Python
type, so values that Quint distinguishes never compare equal after decoding:

======================  ==========================================  =====================
ITF JSON                Quint                                       Python
======================  ==========================================  =====================
``true``/``false``      ``bool``                                    ``bool``
number, ``#bigint``     ``int``                                     ``int`` (unbounded)
string                  ``str``                                     ``str``
JSON array              ``List[T]``                                 :class:`QList`
``{"#tup": [...]}``     tuple, including the unit value ``()``      :class:`QTuple`
``{"#set": [...]}``     ``Set[T]``                                  ``frozenset``
``{"#map": [[k, v]]}``  ``a -> b`` with keys of any type            :class:`QMap`
``{"tag", "value"}``    sum-type variant                            :class:`Variant`
other object            record                                      :class:`Record`
``#unserializable``     a value Quint could not serialize           :class:`Unserializable`
======================  ==========================================  =====================

Map keys keep their type: ``Map(1 -> "a")`` and ``Map("1" -> "a")`` stay distinct.
ITF encodes a variant as an object with exactly the fields ``tag`` and ``value``,
which is the same shape as a record with those two fields. The format cannot tell
them apart, so such objects always decode as :class:`Variant`.
"""

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ItfError",
    "QList",
    "QMap",
    "QTuple",
    "Record",
    "State",
    "Trace",
    "Unserializable",
    "Variant",
    "decode",
    "load_trace",
    "parse_trace",
]


class ItfError(ValueError):
    """The JSON is not a well-formed ITF value or trace."""


class QList(tuple):
    """A Quint ``List``. Equal only to another :class:`QList` with equal items."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return type(other) is QList and tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return hash(("QList", tuple(self)))

    def __repr__(self) -> str:
        return "QList(" + repr(list(self)) + ")"


class QTuple(tuple):
    """A Quint tuple. Equal only to another :class:`QTuple`; ``QTuple()`` is Quint's unit value."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return type(other) is QTuple and tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return hash(("QTuple", tuple(self)))

    def __repr__(self) -> str:
        return "QTuple(" + ", ".join(repr(item) for item in self) + ")"


class _Frozen(Mapping[Any, Any]):
    __slots__ = ("_items", "_hash")
    _items: dict[Any, Any]
    _hash: int | None

    def __init__(self, items: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = ()) -> None:
        object.__setattr__(self, "_items", dict(items))
        object.__setattr__(self, "_hash", None)

    def __getitem__(self, key: Any) -> Any:
        return self._items[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and self._items == other._items  # type: ignore[attr-defined]

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        if self._hash is None:
            object.__setattr__(self, "_hash", hash((type(self).__name__, frozenset(self._items.items()))))
        return self._hash  # type: ignore[return-value]

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")


class Record(_Frozen):
    """A Quint record. Field names are strings; fields are also readable as attributes."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._items[name]
        except KeyError:
            raise AttributeError(name) from None

    def without(self, *names: str) -> "Record":
        return Record((key, value) for key, value in self._items.items() if key not in names)

    def __repr__(self) -> str:
        return "Record(" + ", ".join(f"{key}={value!r}" for key, value in self._items.items()) + ")"


class QMap(_Frozen):
    """A Quint map. Keys keep their decoded type, so ``1`` and ``"1"`` never collide."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "QMap({" + ", ".join(f"{key!r}: {value!r}" for key, value in self._items.items()) + "})"


@dataclass(frozen=True)
class Variant:
    """A value of a Quint sum type, such as ``Some(3)`` or the unit variant ``None``."""

    tag: str
    value: Any = field(default_factory=QTuple)

    @property
    def is_unit(self) -> bool:
        return self.value == QTuple()


@dataclass(frozen=True)
class Unserializable:
    """A value Quint emitted as ``#unserializable``; it cannot be compared with runtime state."""

    text: str


def decode(value: Any) -> Any:
    """Decode one ITF JSON value (already parsed by :mod:`json`) into immutable Python values."""
    if isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ItfError(f"ITF has no floating-point values: {value!r}")
    if isinstance(value, list):
        return QList(decode(item) for item in value)
    if isinstance(value, dict):
        special = [key for key in value if key.startswith("#")]
        if special:
            if len(value) != 1:
                raise ItfError(f"ITF special form must be the only key: {value!r}")
            key = special[0]
            body = value[key]
            if key == "#bigint":
                if not isinstance(body, str):
                    raise ItfError(f"#bigint must be a decimal string: {value!r}")
                try:
                    return int(body, 10)
                except ValueError:
                    raise ItfError(f"#bigint is not a decimal integer: {value!r}") from None
            if key == "#tup":
                return QTuple(decode(item) for item in _array(key, body))
            if key == "#set":
                return frozenset(decode(item) for item in _array(key, body))
            if key == "#map":
                entries = []
                for pair in _array(key, body):
                    if not isinstance(pair, list) or len(pair) != 2:
                        raise ItfError(f"#map entries must be [key, value] pairs: {pair!r}")
                    entries.append((decode(pair[0]), decode(pair[1])))
                result = QMap(entries)
                if len(result) != len(entries):
                    raise ItfError(f"#map has duplicate keys: {value!r}")
                return result
            if key == "#unserializable":
                return Unserializable(str(body))
            raise ItfError(f"unsupported ITF form {key!r}: {value!r}")
        if set(value) == {"tag", "value"} and isinstance(value["tag"], str):
            return Variant(value["tag"], decode(value["value"]))
        return Record((name, decode(item)) for name, item in value.items())
    if value is None:
        raise ItfError("ITF has no null value")
    raise ItfError(f"not an ITF value: {value!r}")


def _array(key: str, body: Any) -> list[Any]:
    if not isinstance(body, list):
        raise ItfError(f"{key} must hold a JSON array, got {body!r}")
    return body


@dataclass(frozen=True)
class State:
    """One decoded trace state: its index and its variables."""

    index: int
    values: Mapping[str, Any]
    meta: Mapping[str, Any]

    def __getitem__(self, name: str) -> Any:
        return self.values[name]


@dataclass(frozen=True)
class Trace:
    """A decoded ITF trace."""

    vars: tuple[str, ...]
    states: tuple[State, ...]
    meta: Mapping[str, Any]
    params: tuple[str, ...] = ()
    loop: int | None = None
    source: Path | None = None

    @property
    def status(self) -> str | None:
        status = self.meta.get("status")
        return status if isinstance(status, str) else None


def parse_trace(document: Any, *, source: Path | None = None) -> Trace:
    """Decode a parsed ITF trace document, validating its structure."""
    where = f" in {source}" if source else ""
    if not isinstance(document, dict):
        raise ItfError(f"an ITF trace is a JSON object{where}")
    variables = document.get("vars")
    states = document.get("states")
    if not isinstance(variables, list) or not all(isinstance(name, str) for name in variables):
        raise ItfError(f"ITF trace has no list of variable names{where}")
    if not isinstance(states, list):
        raise ItfError(f"ITF trace has no list of states{where}")
    meta = document.get("#meta", {})
    if not isinstance(meta, dict):
        raise ItfError(f"ITF #meta must be an object{where}")
    decoded = []
    for position, raw in enumerate(states):
        if not isinstance(raw, dict):
            raise ItfError(f"state {position} is not an object{where}")
        state_meta = raw.get("#meta", {})
        names = set(raw) - {"#meta"}
        if names != set(variables):
            missing = sorted(set(variables) - names)
            extra = sorted(names - set(variables))
            raise ItfError(f"state {position} variables differ from vars{where}: missing {missing}, extra {extra}")
        index = state_meta.get("index", position) if isinstance(state_meta, dict) else position
        if index != position:
            raise ItfError(f"state {position} declares index {index}{where}")
        values = {name: decode(raw[name]) for name in variables}
        decoded.append(State(position, values, state_meta if isinstance(state_meta, dict) else {}))
    loop = document.get("loop")
    params = document.get("params", [])
    return Trace(tuple(variables), tuple(decoded), meta, tuple(params), loop if isinstance(loop, int) else None, source)


def load_trace(path: Path) -> Trace:
    """Read and decode one ``.itf.json`` file."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ItfError(f"{path} is not JSON: {error}") from None
    return parse_trace(document, source=path)
