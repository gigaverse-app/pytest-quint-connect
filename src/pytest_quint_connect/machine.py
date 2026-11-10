"""The model's behaviour as a state machine, merged from every history Quint generated for a contract.

Reports draw it so a reviewer can judge what the model claims: which states it reaches and which actions
move it between them. A model names its own states with the variable its manifest gives as ``state_names``,
usually a sum type such as ``Empty | PartlyFilled | Full``; the machine has one state per name. A model that
names none is drawn by the shape of its observation instead: a list, set or map by its size, a sum-type value
by its tag, a record field by field. Either way the machine holds only what the generated histories visited,
which is evidence of what the model does, not a proof that no other state is reachable.
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .compare import plain, quint_text
from .contract import Contract
from .itf import load_trace
from .replay import TraceError, shown_arguments, steps

__all__ = ["machine", "shape"]

Scalar = str | int | bool


def _count(size: int, one: str, many: str) -> str:
    return "empty" if size == 0 else f"{size} {one if size == 1 else many}"


def shape(observed: object, path: str = "") -> list[tuple[str, Scalar, str]]:
    """``observed`` (as ``compare.plain`` writes it) as ``(field, matched value, shown text)`` per field.

    A list, set or map is its size; a sum-type value its tag; a record opens field by field, so a nested
    field is ``outer.inner``. An observation that is not a record is the single field ``observation``.
    """
    name = path or "observation"
    if isinstance(observed, list):
        return [(name, len(observed), _count(len(observed), "item", "items"))]
    if isinstance(observed, dict):
        if set(observed) == {"tag", "value"}:
            return [(name, str(observed["tag"]), str(observed["tag"]))]
        if set(observed) in ({"#set"}, {"#map"}):
            size = len(next(iter(observed.values())))
            return [(name, size, _count(size, "entry", "entries"))]
        if set(observed) in ({"#tup"}, {"#unserializable"}):
            text = quint_text(observed)
            return [(name, text, text)]
        fields: list[tuple[str, Scalar, str]] = []
        for key, item in observed.items():
            fields += shape(item, f"{path}.{key}" if path else str(key))
        return fields or [(name, "{}", "{}")]
    if isinstance(observed, (str, int, bool)):
        return [(name, observed, quint_text(observed))]
    return [(name, str(observed), str(observed))]


def _name(value: object) -> str:
    """A named-state or named-step value as a word: a sum type by its tag (``Passed``), a string as itself."""
    return value if isinstance(value, str) else quint_text(value)


def machine(
    contract: Contract,
    traces: Iterable[Path],
    parameters: Mapping[str, tuple[str, ...]] | None,
    docs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Every generated history of ``contract`` merged into states and the steps between them.

    States keep the order the histories first reach them, and histories the order Quint generated them, so
    the same seed gives the same machine. Each arrow lists its steps: the action, what the model says the step
    did (its ``step_names`` variable, when it has one), and the arguments every such call shared. ``docs``
    holds the model's doc comments on its variables, which title the diagram.
    """
    keys: dict[object, int] = {}
    states: list[dict[str, Any]] = []
    initial: list[int] = []
    edges: dict[tuple[int, int], dict[tuple[str, str | None], dict[str, Any]]] = {}
    histories = 0
    for path in traces:
        histories += 1
        trace = load_trace(path)
        for field, variable in (("state_names", contract.state_names), ("step_names", contract.step_names)):
            if variable is not None and variable not in trace.vars:
                raise TraceError(f"trace has no {field} variable {variable!r}: {path}")
        previous: int | None = None
        for step, expected in steps(trace, contract):
            if contract.state_names is not None:
                name = _name(plain(step.state[contract.state_names]))
                key: object = name
                entry: dict[str, Any] = {"name": name}
            else:
                fields = shape(plain(expected))
                key = tuple((field, matched) for field, matched, _ in fields)
                entry = {"fields": [[field, text] for field, _, text in fields]}
            node = keys.setdefault(key, len(keys))
            if node == len(states):
                states.append(entry)
            if previous is None:
                if node not in initial:
                    initial.append(node)
            else:
                did = _name(plain(step.state[contract.step_names])) if contract.step_names is not None else None
                arguments, event_fields = shown_arguments(step, parameters, contract.convention)
                calls = edges.setdefault((previous, node), {}).setdefault(
                    (step.action, did), {"action": step.action, **({"step": did} if did is not None else {})}
                )
                given = dict(plain(arguments))
                shared = calls.get("arguments")
                calls["arguments"] = given if shared is None else {k: v for k, v in shared.items() if given.get(k) == v}
                if event_fields:
                    calls["event_fields"] = True
            previous = node
    docs = docs or {}
    described = {
        "states": docs.get(contract.state_names) if contract.state_names else None,
        "steps": docs.get(contract.step_names) if contract.step_names else None,
    }
    return {
        "histories": histories,
        "named_by": contract.state_names,
        "steps_named_by": contract.step_names,
        # The model's own words for what its states and steps are, from its doc comments; absent when undocumented.
        "described": {key: text for key, text in described.items() if text},
        "states": states,
        "initial": initial,
        "transitions": [
            {"from": source, "to": target, "actions": list(actions.values())}
            for (source, target), actions in edges.items()
        ],
    }
