"""Runtime half of generated drivers: dispatch each model step to a typed method.

``pytest-quint-connect codegen`` writes a module with an abstract subclass of
:class:`ActionDriver` for one contract: a method per action, with the action's
parameters and their Python types, and ``observe()`` returning the generated type
of the observed state. A consumer subclasses it and fills in the real API calls.
"""

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from .contract import Contract, ContractError
from .itf import QList, QMap, QTuple, Record, Unserializable, Variant

__all__ = ["ActionDriver", "carried", "codegen_fingerprint", "to_python"]


def codegen_fingerprint(contract: Contract) -> str:
    """Identify everything a generated driver depends on: the model text and the manifest's step mapping."""
    digest = hashlib.sha256()
    digest.update(contract.model.read_bytes().replace(b"\r\n", b"\n"))
    mapping = {
        "module": contract.module,
        "actions": sorted(contract.actions),
        "convention": contract.convention,
        "event": contract.event,
        "action_field": contract.action_field,
        "observe": list(contract.observe),
        "observe_single": contract.observe_single,
    }
    digest.update(json.dumps(mapping, sort_keys=True).encode())
    return digest.hexdigest()


def to_python(value: Any) -> Any:
    """Convert a decoded ITF value into the plain Python shapes the generated types describe."""
    if isinstance(value, Variant):
        if value.is_unit:
            return {"tag": value.tag}
        return {"tag": value.tag, "value": to_python(value.value)}
    if isinstance(value, (Record, QMap)):
        return {to_python(key): to_python(item) for key, item in value.items()}
    if isinstance(value, QList):
        return [to_python(item) for item in value]
    if isinstance(value, QTuple):
        return tuple(to_python(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(to_python(item) for item in value)
    if isinstance(value, Unserializable):
        raise ValueError(f"the model passed an unserializable value as an action argument: {value.text}")
    return value


def carried(step: Any, source: str) -> Any:
    """The model value a step carries as ``source``: its event field, else its nondeterministic pick of that name.

    Raises ``KeyError`` when the step carries neither.
    """
    if source in step.args:
        return step.args[source]
    if step.picks is not None and step.picks.get(source) is not None:
        return step.picks[source]
    raise KeyError(source)


class ActionDriver(ABC):
    """Base of generated drivers. ``step`` calls the method named for the model action.

    Each argument comes from the event field that the model's action assigns it to,
    or from the ``mbt::nondetPicks`` entry of that name when the event lacks it.
    """

    __quint_contract__: ClassVar[str]
    __quint_fingerprint__: ClassVar[str]
    __quint_codegen_command__: ClassVar[str]
    # event label -> (Python method name, ((parameter, event field or pick that carries it), ...))
    __quint_actions__: ClassVar[Mapping[str, tuple[str, tuple[tuple[str, str], ...]]]]

    def check_contract(self, contract: Contract) -> None:
        """Fail when the generated base class was produced from a different model or manifest."""
        if self.__quint_contract__ != contract.name:
            raise ContractError(
                f"{type(self).__name__} was generated for contract {self.__quint_contract__!r}, not {contract.name!r}"
            )
        current = codegen_fingerprint(contract)
        if self.__quint_fingerprint__ != current:
            raise ContractError(
                f"{type(self).__name__} is stale: {contract.model.name} or the manifest's actions/observe changed "
                f"since it was generated. Regenerate it: {self.__quint_codegen_command__}"
            )

    def step(self, step: Any) -> None:
        try:
            method, parameters = self.__quint_actions__[step.action]
        except KeyError:
            raise ContractError(f"{type(self).__name__} has no method for action {step.action!r}") from None
        arguments = {}
        for name, source in parameters:
            try:
                arguments[name] = to_python(carried(step, source))
            except KeyError:
                raise ContractError(
                    f"state {step.index}: action {step.action!r} needs argument {name!r}, but the step carries "
                    f"neither an event field nor a nondeterministic pick named {source!r}"
                ) from None
        getattr(self, method)(**arguments)

    @classmethod
    def parameters(cls) -> dict[str, tuple[str, ...]]:
        """Each action's own arguments, as the event fields or picks that carry them: what reports name."""
        return {action: tuple(source for _, source in params) for action, (_, params) in cls.__quint_actions__.items()}

    @abstractmethod
    def observe(self) -> Any: ...

    def close(self) -> None:  # noqa: B027 - optional hook; most drivers hold nothing to release
        """Release resources when the history ends. Override when the driver holds any."""
