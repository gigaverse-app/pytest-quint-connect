"""Generate a typed driver base class and observation types from a Quint model.

The generator reads ``quint compile --target json --flatten=false``: record and sum
typedefs become ``TypedDict`` classes, each declared action becomes an abstract
method with the action's own parameter names and types, and ``observe()`` returns
the type of the observed state. It never generates behavior: the subclass a person
writes still decides which real API each action calls and how state is read back.
"""

import json
import keyword
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contract import Contract
from .quint import QuintError, QuintTool, run
from .typed import codegen_fingerprint

__all__ = ["CodegenError", "compile_ir", "default_output", "generate_module"]

# Generated modules must not churn under whatever formatter and linter settings a consumer uses:
# `fmt: off` holds ruff and Black formatting at any line length, and the file-level noqa keeps lint quiet.
# The first line exempts the noqa itself: PGH004 (blanket noqa) under `--select ALL`, preview's RUF105
# (noqa instead of `ruff: file-ignore`, which has no blanket form) and RUF102 (a code this ruff does not know).
# Ruff older than RUF105 prints a warning about the unknown codes and still passes.
_TOOL_DIRECTIVES = ["# ruff: noqa: PGH004, RUF102, RUF105", "# ruff: noqa", "# fmt: off"]


class CodegenError(ValueError):
    """The model or manifest cannot be turned into a typed driver."""


def default_output(contract: Contract) -> Path:
    """Where ``codegen`` writes, and ``codegen --check`` compares, when no ``-o`` is given."""
    return contract.manifest.parent / f"{contract.module}_driver.py"


def compile_ir(contract: Contract, tool: QuintTool, logs: Path | None = None) -> dict[str, Any]:
    """Run ``quint compile`` and return its JSON intermediate representation."""
    with tempfile.TemporaryDirectory(prefix="quint-codegen-") as directory:
        out = Path(directory) / "ir.json"
        log = (logs or Path(directory)) / "compile.log"
        compile_command = [
            *tool.command,
            "compile",
            str(contract.model),
            "--main",
            contract.module,
            "--target",
            "json",
            "--flatten=false",
            "--verbosity",
            "0",
            "--out",
            str(out),
        ]
        try:
            run(compile_command, log, cwd=contract.directory)
        except QuintError:
            # `quint compile` exits 1 without printing why. When the model is at fault, typecheck
            # raises instead, with Quint's diagnostic; otherwise the compile failure stands.
            run(
                [*tool.command, "typecheck", str(contract.model)],
                log.with_name("typecheck.log"),
                cwd=contract.directory,
            )
            raise
        ir: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
        return ir


def _q(text: str) -> str:
    """A double-quoted Python string literal, as ruff format writes it."""
    return json.dumps(text)


def _pascal(text: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", text)
    name = "".join(part[:1].upper() + part[1:] for part in parts if part)
    if not name or name[0].isdigit():
        name = "Q" + name
    return name


def _identifier(text: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_]", "_", text)
    if not name or name[0].isdigit():
        name = "_" + name
    if keyword.iskeyword(name):
        name += "_"
    return name


def _python_name(text: str) -> str:
    """A Quint name as a Python method or parameter name: ``touchGlobal`` becomes ``touch_global``."""
    return _identifier(re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", text).lower())


def _strip_ids(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _strip_ids(value) for key, value in node.items() if key != "id"}
    if isinstance(node, list):
        return [_strip_ids(item) for item in node]
    return node


def _row(node: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    row = node["fields"]
    if row.get("other", {}).get("kind") not in (None, "empty"):
        raise CodegenError(f"open rows (row polymorphism) cannot be generated: {json.dumps(_strip_ids(node))}")
    return [(item["fieldName"], item["fieldType"]) for item in row["fields"]]


@dataclass
class _Emitter:
    typedefs: dict[str, dict[str, Any]]
    classes: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # structural key -> Python name
    taken: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)
    typing: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        for name, node in self.typedefs.items():
            if node["kind"] in ("rec", "sum"):
                self.names.setdefault(self._key(node), _pascal(name))
        self.taken.update(self.names.values())

    @staticmethod
    def _key(node: dict[str, Any]) -> str:
        return json.dumps(_strip_ids(node), sort_keys=True)

    def _fresh(self, hint: str) -> str:
        name = _pascal(hint)
        candidate, counter = name, 2
        while candidate in self.taken:
            candidate, counter = f"{name}{counter}", counter + 1
        self.taken.add(candidate)
        return candidate

    def annotation(self, node: dict[str, Any], hint: str) -> str:
        kind: str = node["kind"]
        if kind in ("int", "bool", "str"):
            return kind
        if kind == "const":
            target = self.typedefs.get(node["name"])
            if target is None:
                raise CodegenError(f"unknown type {node['name']!r}")
            return self.annotation(target, node["name"])
        if kind == "list":
            return f"list[{self.annotation(node['elem'], hint + 'Item')}]"
        if kind == "set":
            self.imports.add("from collections.abc import Set as AbstractSet")
            return f"AbstractSet[{self.annotation(node['elem'], hint + 'Item')}]"
        if kind == "fun":
            self.imports.add("from collections.abc import Mapping")
            key = self.annotation(node["arg"], hint + "Key")
            value = self.annotation(node["res"], hint + "Value")
            return f"Mapping[{key}, {value}]"
        if kind == "tup":
            items = [self.annotation(item, f"{hint}{index}") for index, (_, item) in enumerate(_row(node))]
            return f"tuple[{', '.join(items)}]" if items else "tuple[()]"
        if kind == "rec":
            return self._named(node, hint, self._record)
        if kind == "sum":
            return self._named(node, hint, self._sum)
        raise CodegenError(f"cannot generate a Python type for Quint type kind {kind!r} ({hint})")

    def _named(self, node: dict[str, Any], hint: str, build: Any) -> str:
        key = self._key(node)
        name = self.names.get(key)
        if name is None:
            name = self.names[key] = self._fresh(hint)
        if name not in self._built:
            self._built.add(name)
            build(node, name)
        return name

    _built: set[str] = field(default_factory=set)

    def _typed_dict(self, name: str, fields: list[tuple[str, str]]) -> None:
        self.typing.add("TypedDict")
        if all(key.isidentifier() and not keyword.iskeyword(key) for key, _ in fields):
            body = "\n".join(f"    {key}: {value}" for key, value in fields) or "    pass"
            self.classes.append(f"class {name}(TypedDict):\n{body}")
        else:
            entries = ", ".join(f"{_q(key)}: {value}" for key, value in fields)
            self.classes.append(f"{name} = TypedDict({_q(name)}, {{{entries}}})")

    def _record(self, node: dict[str, Any], name: str) -> None:
        fields = [(key, self.annotation(value, name + _pascal(key))) for key, value in _row(node)]
        self._typed_dict(name, fields)

    def _sum(self, node: dict[str, Any], name: str) -> None:
        self.typing.add("Literal")
        members = []
        for tag, payload in _row(node):
            member = self._fresh(name + _pascal(tag))
            unit = payload["kind"] == "tup" and not _row(payload)
            if unit:
                self.typing.add("NotRequired")
                fields = [("tag", f"Literal[{_q(tag)}]"), ("value", "NotRequired[tuple[()] | None]")]
            else:
                fields = [("tag", f"Literal[{_q(tag)}]"), ("value", self.annotation(payload, member + "Value"))]
            self._typed_dict(member, fields)
            members.append(member)
        self.classes.append(f"{name} = {' | '.join(members)}")


def _declarations(ir: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    typedefs: dict[str, dict[str, Any]] = {}
    for module in ir["modules"]:
        for declaration in module["declarations"]:
            if declaration["kind"] == "typedef" and "type" in declaration:
                typedefs.setdefault(declaration["name"], declaration["type"])
    main = next((module for module in ir["modules"] if module["name"] == ir.get("main")), None)
    if main is None:
        raise CodegenError(f"Quint reported no main module {ir.get('main')!r}")
    variables = {}
    actions = {}
    for declaration in main["declarations"]:
        if declaration["kind"] == "var":
            variables[declaration["name"]] = declaration["typeAnnotation"]
        elif declaration["kind"] == "def" and declaration.get("qualifier") == "action":
            actions[declaration["name"]] = declaration
    return typedefs, variables, actions


def _parameters(declaration: dict[str, Any], ir: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    expression = declaration["expr"]
    if expression["kind"] != "lambda":
        return []
    names = [parameter["name"] for parameter in expression["params"]]
    annotation = declaration.get("typeAnnotation")
    if annotation is None:
        inferred = ir.get("types", {}).get(str(declaration["id"]), {}).get("type")
        annotation = inferred
    if annotation is None or annotation.get("kind") != "oper":
        raise CodegenError(f"action {declaration['name']!r} has parameters but no operator type")
    return list(zip(names, annotation["args"], strict=True))


@dataclass(frozen=True)
class _Emission:
    """One ``event' = { op: LABEL, field: param, ... }`` assignment inside an action."""

    label: str
    fields: dict[str, str]  # event field -> action parameter it carries


def _emissions(declaration: dict[str, Any], event: str, action_field: str) -> list[_Emission]:
    """Find the event records an action assigns, and which of its parameters each field carries."""
    expression = declaration["expr"]
    parameters = {parameter["name"] for parameter in expression.get("params", [])}
    found = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("kind") == "app" and node.get("opcode") == "assign":
            target, value = node["args"]
            if target.get("name") == event and value.get("kind") == "app" and value.get("opcode") == "Rec":
                pairs = list(zip(value["args"][0::2], value["args"][1::2], strict=True))
                label = next(
                    (v.get("value") for k, v in pairs if k.get("value") == action_field and v.get("kind") == "str"),
                    None,
                )
                fields = {
                    k["value"]: v["name"]
                    for k, v in pairs
                    if v.get("kind") == "name" and v.get("name") in parameters and k.get("value") != action_field
                }
                if isinstance(label, str):
                    found.append(_Emission(label, fields))
        for item in node.values():
            visit(item)

    visit(expression)
    return found


def _by_label(
    quint_actions: dict[str, dict[str, Any]], contract: Contract
) -> dict[str, tuple[dict[str, Any], dict[str, str]]]:
    """Map each event label to the Quint action that emits it and that action's parameter -> event field."""
    labels: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
    for declaration in quint_actions.values():
        for emission in _emissions(declaration, contract.event, contract.action_field):
            sources = {parameter: field for field, parameter in emission.fields.items()}
            known = labels.get(emission.label)
            if known is not None and (known[0]["name"] != declaration["name"] or known[1] != sources):
                raise CodegenError(
                    f"event label {emission.label!r} is emitted by actions {known[0]['name']!r} and "
                    f"{declaration['name']!r} with different arguments; give each its own label"
                )
            labels[emission.label] = (declaration, sources)
    return labels


def generate_module(contract: Contract, ir: dict[str, Any], *, output: Path, class_name: str | None = None) -> str:
    """Return the source of the generated module for ``contract``."""
    typedefs, variables, quint_actions = _declarations(ir)
    emitter = _Emitter(typedefs)

    for name in contract.observe:
        if name not in variables:
            raise CodegenError(f"observed variable {name!r} is not a state variable of {contract.module}")
    if contract.observe_single:
        observed = emitter.annotation(variables[contract.observe[0]], contract.observe[0])
    else:
        fields = [(name, emitter.annotation(variables[name], name)) for name in contract.observe]
        observed = emitter._fresh("Observation")
        emitter._typed_dict(observed, fields)

    event_fields: set[str] | None = None
    if contract.convention == "event":
        if contract.event not in variables:
            raise CodegenError(f"event variable {contract.event!r} is not a state variable of {contract.module}")
        event_type = variables[contract.event]
        if event_type["kind"] == "const":
            event_type = typedefs[event_type["name"]]
        if event_type["kind"] != "rec":
            raise CodegenError(f"event variable {contract.event!r} must be a record")
        event_fields = {key for key, _ in _row(event_type)} - {contract.action_field}

    labels = _by_label(quint_actions, contract) if event_fields is not None else {}
    methods = []
    table: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {}
    used: set[str] = {"observe", "step", "close", "check_contract"}
    for action in contract.actions:
        method = _python_name(action)
        if method in used:
            raise CodegenError(f"action {action!r} maps to the reserved or duplicate method name {method!r}")
        used.add(method)
        # Event convention: the action whose event' assignment carries this label; otherwise the same-named action.
        declaration, sources = labels.get(action) or (quint_actions.get(action), {})
        parameters = _parameters(declaration, ir) if declaration is not None else []
        bindings = []
        for name, node in parameters:
            source = sources.get(name, name)
            if event_fields is not None and source not in event_fields:
                raise CodegenError(
                    f"action {declaration['name']}({', '.join(n for n, _ in parameters)}) labelled {action!r}: "
                    f"parameter {name!r} is not carried by any field of the event record {sorted(event_fields)}. "
                    "Assign it to an event field in the action's event' record."
                )
            python = _python_name(name)
            if python in (bound for bound, _, _ in bindings):
                raise CodegenError(f"action {action!r}: two parameters map to the Python name {python!r}")
            bindings.append((python, source, emitter.annotation(node, f"{action}_{name}")))
        table[action] = (method, tuple((name, source) for name, source, _ in bindings))
        signature = ", ".join(["self", *(f"{name}: {annotation}" for name, _, annotation in bindings)])
        boundary = contract.actions[action].replace("\\", "\\\\").replace('"', '\\"')
        if declaration is None:
            origin = "  (event label with no Quint action)"
        elif declaration["name"] != action:
            origin = f"  (Quint action {declaration['name']})"
        else:
            origin = ""
        methods.append(f'    @abstractmethod\n    def {method}({signature}) -> None:\n        """{boundary}{origin}"""')

    class_name = class_name or _pascal(contract.name) + "Driver"
    # Recorded relative to the generated file, so the module is identical wherever codegen runs.
    manifest = Path(os.path.relpath(contract.manifest, output.resolve().parent)).as_posix()
    words = ["pytest-quint-connect", "codegen", manifest]
    if output.resolve() != default_output(contract).resolve():
        words += ["-o", output.name]
    command = shlex.join(words)
    typing_names = sorted(emitter.typing)
    imports = ["from abc import abstractmethod", *sorted(emitter.imports)]
    if typing_names:
        imports.append(f"from typing import {', '.join(typing_names)}")
    lines = [
        *_TOOL_DIRECTIVES,
        f'"""Typed driver base for the Quint contract {_q(contract.name)} ({contract.model.name}).',
        "",
        "Generated by pytest-quint-connect. Do not edit. Regenerate from this directory with:",
        "",
        f"    {command}",
        '"""',
        "",
        *imports,
        "",
        "from pytest_quint_connect.typed import ActionDriver",
        "",
        "",
    ]
    for block in emitter.classes:
        lines += [block, "", ""]
    rows = []
    for action, (method, params) in table.items():
        pairs = [f"({_q(name)}, {_q(source)})" for name, source in params]
        names = pairs[0] + "," if len(pairs) == 1 else ", ".join(pairs)
        rows.append(f"        {_q(action)}: ({_q(method)}, ({names})),")
    lines += [
        f"class {class_name}(ActionDriver):",
        '    """Subclass this, implement each action against the real code, and read state back in observe()."""',
        "",
        f"    __quint_contract__ = {_q(contract.name)}",
        f"    __quint_fingerprint__ = {_q(codegen_fingerprint(contract))}",
        f"    __quint_codegen_command__ = {_q(command)}",
        "    __quint_actions__ = {",
        *rows,
        "    }",
        "",
        "\n\n".join(methods),
        "",
        "    @abstractmethod",
        f"    def observe(self) -> {observed}:",
        f'        """Read the implementation\'s state back as {observed}."""',
        "",
    ]
    return "\n".join(lines)
