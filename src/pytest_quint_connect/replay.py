"""Replay a decoded Quint trace through a driver and compare every state.

A driver executes each step against the real implementation and then reads the
implementation's state back. The engine never computes an expected value itself:
expectations come only from the trace.
"""

import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .compare import Difference, differences, plain
from .contract import Contract
from .itf import QTuple, Record, State, Trace, Variant, load_trace
from .typed import ActionDriver, carried

__all__ = [
    "Driver",
    "DriverFactory",
    "HistoryContext",
    "ImportEscape",
    "ReplayMismatch",
    "ReplayResult",
    "Step",
    "TraceError",
    "call_text",
    "replay_trace",
    "shown_arguments",
    "steps",
]

MBT_ACTION = "mbt::actionTaken"
MBT_PICKS = "mbt::nondetPicks"


class TraceError(ValueError):
    """The trace cannot be replayed under this contract (empty, undeclared action, missing variable)."""


class ImportEscape(RuntimeError):
    """The implementation under test was imported from outside the attested source directory."""


@dataclass(frozen=True)
class Step:
    """One model step for the driver to execute.

    ``action`` is the model action name. ``args`` are its arguments: the event
    record without its action field (event convention), or the nondeterministic
    picks that were made (mbt convention). ``picks`` holds every ``mbt::nondetPicks``
    entry when the trace carries that metadata, with ``None`` for unpicked names.
    """

    index: int
    action: str
    args: Mapping[str, Any]
    picks: Mapping[str, Any] | None
    state: State


@runtime_checkable
class Driver(Protocol):
    """Executes model steps against the real implementation and reads its state back."""

    def step(self, step: Step) -> None: ...

    def observe(self) -> Any: ...


@dataclass(frozen=True)
class HistoryContext:
    """What a driver factory receives for one history: fresh per history."""

    contract: Contract
    trace: Path
    work_dir: Path
    mutant: str | None = None


DriverFactory = Callable[[HistoryContext], Driver]


@dataclass(eq=False)
class ReplayMismatch(AssertionError):
    """The implementation's observed state differs from the model's expected state."""

    trace: Path
    contract: str
    state: int
    action: str
    arguments: Mapping[str, Any]
    expected: Any
    actual: Any
    diff: list[Difference]
    mutant: str | None = None
    seed: int | None = None
    reproduce: str | None = None
    # True when ``arguments`` is the whole event record: the driver did not declare the action's parameters.
    event_fields: bool = False

    def __post_init__(self) -> None:
        AssertionError.__init__(self, self.render())

    def render(self) -> str:
        details = [f"  {len(self.diff)} difference(s):"]
        details.extend("  " + line for difference in self.diff for line in difference.render().splitlines())
        return _report(
            f"Quint contract {self.contract!r}: implementation state differs from the model",
            self.trace,
            f"{self.state} after action {call_text(self.action, self.arguments, self.event_fields)}",
            mutant=self.mutant,
            seed=self.seed,
            details=details,
            reproduce=self.reproduce,
        )

    def __str__(self) -> str:
        return self.render()

    def as_json(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "trace": str(self.trace),
            "state": self.state,
            "action": self.action,
            "arguments": plain(self.arguments),
            "event_fields": self.event_fields,
            "mutant": self.mutant,
            "seed": self.seed,
            "expected": plain(self.expected),
            "actual": plain(self.actual),
            "differences": [
                {"path": d.path, "reason": d.reason, "expected": _json(d.expected), "actual": _json(d.actual)}
                for d in self.diff
            ],
            "reproduce": self.reproduce,
        }


def _report(
    headline: str,
    trace: Path,
    state: str,
    *,
    mutant: str | None,
    seed: int | None,
    details: list[str],
    reproduce: str | None,
) -> str:
    """Where a history failed, in one layout for mismatches and driver errors."""
    lines = [headline, f"  trace:  {trace}", f"  state:  {state}"]
    if mutant:
        lines.append(f"  mutant: {mutant}")
    if seed is not None:
        lines.append(f"  seed:   {seed}")
    lines.extend(details)
    if reproduce:
        lines.append(f"  reproduce: {reproduce}")
    return "\n".join(lines)


@contextmanager
def _locating(
    contract: Contract, trace: Path, step: Step, called: str, mutant: str | None, reproduce: str | None
) -> Iterator[None]:
    """Name the history and step in any exception the driver raises while applying or observing it."""
    try:
        yield
    except Exception as error:
        error.add_note(
            _report(
                f"Quint contract {contract.name!r}: the driver raised during this history",
                trace,
                f"{step.index}, applying action {called}",
                mutant=mutant,
                seed=contract.bounds.seed,
                details=[],
                reproduce=reproduce,
            )
        )
        raise


def _json(value: Any) -> Any:
    rendered = plain(value)
    try:
        json.dumps(rendered)
        return rendered
    except TypeError:
        return repr(rendered)


def call_text(action: object, arguments: Mapping[str, Any], event_fields: bool = False) -> str:
    """``fetch(key='a')``; ``fetch(event: key='a', value=0)`` when the arguments are the whole event record."""
    shown = ", ".join(f"{key}={plain(value)!r}" for key, value in arguments.items())
    return f"{action}(event: {shown})" if event_fields else f"{action}({shown})"


def shown_arguments(
    step: Step, parameters: Mapping[str, tuple[str, ...]] | None, convention: str
) -> tuple[Record, bool]:
    """The arguments reports name for ``step``, and whether they are the whole event record.

    ``parameters`` maps each action to the event fields or picks that carry its own arguments, as a generated
    driver declares them. Without it, an event-convention step can only show its whole event record, which
    also holds fields this action does not take, so the report says so; an mbt step's picks are its arguments.
    """
    fields = parameters.get(step.action) if parameters is not None else None
    if fields is None:
        return Record(step.args.items()), convention == "event"
    shown = []
    for name in fields:
        try:
            shown.append((name, carried(step, name)))
        except KeyError:
            continue
    return Record(shown), False


@dataclass
class ReplayResult:
    trace: Path
    actions: list[str] = field(default_factory=list)
    imported: dict[str, str] = field(default_factory=dict)
    # Each action's own arguments, when the driver declares them (a generated base does); see shown_arguments.
    parameters: dict[str, tuple[str, ...]] | None = None


def _unwrap_pick(value: Any) -> Any:
    if isinstance(value, Variant) and value.tag == "Some":
        return value.value
    if isinstance(value, Variant) and value.tag == "None" and value.value == QTuple():
        return None
    raise TraceError(f"mbt::nondetPicks entry is not an Option value: {value!r}")


def steps(trace: Trace, contract: Contract) -> Iterator[tuple[Step, Any]]:
    """Yield each step with its expected observation, validating the trace against the contract."""
    if len(trace.states) < 2:
        raise TraceError(f"trace has {len(trace.states)} state(s); a history needs at least two: {trace.source}")
    has_mbt = MBT_ACTION in trace.vars
    if contract.convention == "mbt" and not has_mbt:
        raise TraceError(f"mbt contract trace lacks {MBT_ACTION} (was it generated with --mbt?): {trace.source}")
    if contract.convention == "event" and contract.event not in trace.vars:
        raise TraceError(f"trace has no event variable {contract.event!r}: {trace.source}")
    for name in contract.observe:
        if name not in trace.vars:
            raise TraceError(f"trace has no observed variable {name!r}: {trace.source}")
    for state in trace.states:
        picks = None
        if has_mbt:
            raw = state[MBT_PICKS] if MBT_PICKS in trace.vars else Record()
            if not isinstance(raw, Record):
                raise TraceError(f"{MBT_PICKS} must be a record in state {state.index}")
            picks = Record((key, _unwrap_pick(value)) for key, value in raw.items())
        if contract.convention == "event":
            event = state[contract.event]
            if not isinstance(event, Record) or contract.action_field not in event:
                raise TraceError(
                    f"state {state.index}: event variable {contract.event!r} must be a record "
                    f"with field {contract.action_field!r}"
                )
            action = event[contract.action_field]
            args: Mapping[str, Any] = event.without(contract.action_field)
            if has_mbt:
                taken = state[MBT_ACTION]
                # Quint records the action chosen inside `any { ... }`. A step that calls one action
                # directly is recorded under the init or step action's own name, which names no event.
                unbranched = taken == (contract.init if state.index == 0 else contract.step)
                if taken != action and not unbranched:
                    raise TraceError(
                        f"state {state.index}: event says {action!r} but Quint took action {taken!r}; "
                        "the model labels this step with the wrong event"
                    )
        else:
            action = state[MBT_ACTION]
            args = Record((key, value) for key, value in (picks or {}).items() if value is not None)
        if not isinstance(action, str):
            raise TraceError(f"state {state.index}: action name must be a string, got {action!r}")
        if action not in contract.actions:
            raise TraceError(f"undeclared action {action!r} in state {state.index} of {trace.source}")
        if contract.observe_single:
            expected = state[contract.observe[0]]
        else:
            expected = Record((name, state[name]) for name in contract.observe)
        yield Step(state.index, action, args, picks, state), expected


def imported_modules(contract: Contract) -> dict[str, str]:
    """Check the attestation: every imported module of the package comes from its source directory."""
    if contract.attest is None:
        return {}
    package, source = contract.attest.package, contract.attest.source
    found = {}
    for name, module in tuple(sys.modules.items()):
        if name == package or name.startswith(package + "."):
            location = getattr(module, "__file__", None)
            if location is None or not Path(location).resolve().is_relative_to(source):
                raise ImportEscape(_escaped(contract, name, location))
            found[name] = Path(location).resolve().relative_to(contract.root).as_posix()
    if not found:
        raise ImportEscape(
            f"the driver imported no module of the attested package {package!r}.\n"
            "Fix: make attest.package the name the driver imports (the top-level import name, not the "
            "distribution name), and import the code under test from the driver."
        )
    return found


def _escaped(contract: Contract, name: str, location: str | None) -> str:
    """Which module came from where, where it should have come from, and how to make Python find that copy."""
    assert contract.attest is not None, "only an attested contract checks where its modules came from"
    package, source = contract.attest.package, contract.attest.source
    where = f"from {location}" if location else "without a file (a namespace package?)"
    search = ", ".join(repr(entry) for entry in sys.path[:4])
    return "\n".join(
        [
            f"{name} was imported {where}, outside the attested source directory {source}.",
            f"Python found another copy of {package} first, usually an installed, non-editable one (tox and "
            "`pip install .` install one), so that copy ran instead of your checkout.",
            f"Fix: put {source} first on the import path. In the pytest configuration you run contracts with "
            "(the file passed with -c, such as contracts/quint.ini), set `pythonpath` to that directory, relative "
            "to the configuration file; or install the package editable (`pip install -e .`). If the code "
            "really lives elsewhere, change attest.source in the manifest.",
            f"sys.path starts with: {search}",
        ]
    )


@contextmanager
def scratch_directory(prefix: str, base: Path | None = None, *, keep_on_failure: bool = False) -> Iterator[Path]:
    """A fresh temporary directory, removed when the block ends.

    With ``keep_on_failure`` it survives a block that raises, so logs an error names can still be read.
    """
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    failed = True
    try:
        yield directory
        failed = False
    finally:
        if not (failed and keep_on_failure):
            shutil.rmtree(directory, ignore_errors=True)


def replay_trace(
    path: Path,
    contract: Contract,
    factory: DriverFactory,
    *,
    mutant: str | None = None,
    work_base: Path | None = None,
    reproduce: str | None = None,
) -> ReplayResult:
    """Replay one trace through a fresh driver; raise :class:`ReplayMismatch` on the first divergence.

    The driver is created for this history only and closed afterwards, even when
    the replay fails.
    """
    trace = load_trace(path)
    planned = list(steps(trace, contract))
    result = ReplayResult(path)
    with scratch_directory("quint-history-", work_base) as work:
        driver = factory(HistoryContext(contract, path, work, mutant))
        if not isinstance(driver, Driver):
            raise TypeError(f"driver factory returned {type(driver).__name__}, which lacks step() and observe()")
        try:
            if isinstance(driver, ActionDriver):
                # A generated base built from an older model or manifest must not drive this one.
                driver.check_contract(contract)
                result.parameters = driver.parameters()
            for step, expected in planned:
                arguments, event_fields = shown_arguments(step, result.parameters, contract.convention)
                called = call_text(step.action, arguments, event_fields)
                with _locating(contract, path, step, called, mutant, reproduce):
                    driver.step(step)
                    actual = driver.observe()
                try:
                    result.imported = imported_modules(contract)
                except ImportEscape as escape:
                    raise ImportEscape(
                        _report(
                            f"Quint contract {contract.name!r}: the code under test did not come from the attested "
                            "source",
                            path,
                            f"{step.index}, after action {called}",
                            mutant=mutant,
                            seed=contract.bounds.seed,
                            details=["  " + line for line in str(escape).splitlines()],
                            reproduce=reproduce,
                        )
                    ) from None
                result.actions.append(step.action)
                found = differences(expected, actual)
                if found:
                    raise ReplayMismatch(
                        trace=path,
                        contract=contract.name,
                        state=step.index,
                        action=step.action,
                        arguments=arguments,
                        expected=expected,
                        actual=actual,
                        diff=found,
                        mutant=mutant,
                        seed=contract.bounds.seed,
                        reproduce=reproduce,
                        event_fields=event_fields,
                    )
        finally:
            close = getattr(driver, "close", None)
            if callable(close):
                close()
    return result
