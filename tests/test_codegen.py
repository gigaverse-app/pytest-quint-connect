"""Typed driver generation from real ``quint compile`` output, and dispatch through generated bases."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from lru_support import EXAMPLE, MANIFEST

from pytest_quint_connect import ContractError, Step, replay_trace
from pytest_quint_connect.cli import main
from pytest_quint_connect.codegen import CodegenError, compile_ir, generate_module
from pytest_quint_connect.contract import load_contract
from pytest_quint_connect.itf import Record, State
from pytest_quint_connect.quint import QuintError, resolve_quint, run
from pytest_quint_connect.typed import ActionDriver, codegen_fingerprint

DATA = Path(__file__).parent / "data"
DOGFOOD = Path(__file__).resolve().parents[1] / "dogfood"
SKILL = Path(__file__).resolve().parents[1] / "skills/pytest-quint-connect/SKILL.md"


@pytest.fixture(scope="module")
def quint(tmp_path_factory: pytest.TempPathFactory):
    import os

    contract = load_contract(MANIFEST)
    return resolve_quint(contract.quint_version, tmp_path_factory.mktemp("quint"), os.environ.get("QUINT"))


def copy_example(destination: Path) -> Path:
    for name in ("lru.qnt", "quint-connect.json", "lru_cache.py"):
        shutil.copyfile(EXAMPLE / name, destination / name)
    return destination / "quint-connect.json"


def load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"generated_{abs(hash(path))}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("manifest", "generated"),
    [
        (MANIFEST, EXAMPLE / "lru_cache_driver.py"),
        (DOGFOOD / "quint-connect.json", DOGFOOD / "session_driver.py"),
    ],
    ids=["example", "dogfood"],
)
def test_committed_generated_drivers_are_current(
    quint, capsys: pytest.CaptureFixture[str], manifest: Path, generated: Path
) -> None:
    arguments = [str(manifest), "-o", str(generated), "--quint", str(quint.executable)]
    assert main(["codegen", *arguments, "--check"]) == 0, capsys.readouterr().out


def test_check_reports_a_stale_module_and_write_refreshes_it(tmp_path: Path, quint) -> None:
    manifest = copy_example(tmp_path)
    output = tmp_path / "lru_cache_driver.py"
    flags = ["-o", str(output), "--quint", str(quint.executable)]
    assert main(["codegen", str(manifest), *flags, "--check"]) == 1
    assert main(["codegen", str(manifest), *flags]) == 0
    assert main(["codegen", str(manifest), *flags, "--check"]) == 0
    model = tmp_path / "lru.qnt"
    model.write_text(model.read_text().replace("pure val CAPACITY = 2", "pure val CAPACITY = 3"), encoding="utf-8")
    assert main(["codegen", str(manifest), *flags, "--check"]) == 1


def test_first_run_says_it_installs_the_bundled_quint_and_later_runs_say_they_reuse_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real npm ci into an empty cache: the line tells a user why the first run is slow, and where Quint lives.
    manifest = copy_example(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("PYTEST_QUINT_CONNECT_CACHE", str(cache))
    monkeypatch.delenv("QUINT", raising=False)
    assert main(["typecheck", str(manifest)]) == 0
    first = capsys.readouterr()
    assert f"installing the bundled Quint 0.33.1 into {cache}" in first.err
    assert "with npm ci (first use only)" in first.err
    assert "lru.qnt typechecks with Quint 0.33.1" in first.out
    assert main(["typecheck", str(manifest)]) == 0
    second = capsys.readouterr()
    assert f"using the bundled Quint 0.33.1 from {cache}" in second.err and "installing" not in second.err


def test_check_names_the_default_module_it_could_not_find(
    tmp_path: Path, quint, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = copy_example(tmp_path)
    assert main(["codegen", str(manifest), "--quint", str(quint.executable), "--check"]) == 1
    error = capsys.readouterr().err
    assert str(tmp_path / "lru_cache_driver.py") in error
    assert "does not exist, the default output for module lru_cache" in error
    assert "pass the -o it was generated with" in error


def test_header_command_regenerates_the_same_file(tmp_path: Path, quint) -> None:
    manifest = copy_example(tmp_path)
    contract = load_contract(manifest)
    ir = compile_ir(contract, quint)
    default = generate_module(contract, ir, output=tmp_path / "lru_cache_driver.py")
    assert "    pytest-quint-connect codegen quint-connect.json\n" in default
    elsewhere = generate_module(contract, ir, output=tmp_path / "drivers" / "cache_base.py")
    assert "    pytest-quint-connect codegen ../quint-connect.json -o cache_base.py\n" in elsewhere


def test_generated_module_is_stable_under_any_consumer_ruff_config(tmp_path: Path, quint) -> None:
    manifest = copy_example(tmp_path)
    output = tmp_path / "lru_cache_driver.py"
    contract = load_contract(manifest)
    output.write_text(generate_module(contract, compile_ir(contract, quint), output=output), encoding="utf-8")
    assert_left_alone_by_ruff(output, tmp_path)


MPL_HEADER = """# Copyright 2009-2026 Example Author. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""


def with_header(directory: Path, header: str) -> Path:
    """The LRU example whose manifest asks codegen for ``header`` (the fourth trial's repository wanted MPL notices)."""
    manifest = copy_example(directory)
    (directory / "LICENSE_HEADER").write_bytes(header.encode("utf-8"))
    document = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(document | {"codegen_header": "LICENSE_HEADER"}), encoding="utf-8")
    return manifest


def test_a_license_header_comes_first_and_leaves_the_fingerprint_alone(tmp_path: Path, quint) -> None:
    plain_contract = load_contract(copy_example(tmp_path))
    plain = generate_module(plain_contract, compile_ir(plain_contract, quint), output=tmp_path / "lru_cache_driver.py")
    # A header file with Windows line endings still yields LF output.
    manifest = with_header(tmp_path, MPL_HEADER.replace("\n", "\r\n"))
    output = tmp_path / "lru_cache_driver.py"
    flags = ["--quint", str(quint.executable)]
    assert main(["codegen", str(manifest), *flags]) == 0
    source = output.read_text(encoding="utf-8")
    assert source.startswith(MPL_HEADER + "# ruff: noqa: PGH004")
    # Only the header differs: the fingerprint (model and mapping) and everything after it are unchanged.
    assert source.removeprefix(MPL_HEADER) == plain
    assert main(["codegen", str(manifest), *flags, "--check"]) == 0
    assert_left_alone_by_ruff(output, tmp_path)
    module = load_module(output)
    assert module.LruCacheDriver.__quint_fingerprint__ == codegen_fingerprint(load_contract(MANIFEST))


def test_a_header_with_code_is_refused(tmp_path: Path, quint, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = with_header(tmp_path, "# License: MIT\nimport os\n")
    assert main(["codegen", str(manifest), "--quint", str(quint.executable)]) == 2
    error = capsys.readouterr().err
    assert "LICENSE_HEADER:2: codegen_header may hold only comment lines" in error and "'import os'" in error
    assert not (tmp_path / "lru_cache_driver.py").exists()


def assert_left_alone_by_ruff(module: Path, configs: Path) -> None:
    """Every rule, stable and preview (the fourth trial's `preview = true` flagged RUF105), at any line length."""
    ruff = [sys.executable, "-m", "ruff"]
    for line_length in (40, 79, 88, 200):
        for preview in ("false", "true"):
            for lint in ('select = ["ALL"]', 'extend-select = ["RUF", "PGH"]', ""):
                config = configs / f"ruff-{line_length}-{preview}-{len(lint)}.toml"
                config.write_text(
                    f"line-length = {line_length}\npreview = {preview}\n[lint]\n{lint}\n", encoding="utf-8"
                )
                for command in (["format", "--check"], ["check"]):
                    arguments = [*ruff, *command, "--no-cache", "--config", str(config), str(module)]
                    result = subprocess.run(arguments, capture_output=True)
                    assert result.returncode == 0, (config.read_text(), command, result.stdout, result.stderr)


def test_generated_module_is_typed_and_ruff_clean(tmp_path: Path, quint) -> None:
    manifest = copy_example(tmp_path)
    output = tmp_path / "lru_cache_driver.py"
    contract = load_contract(manifest)
    source = generate_module(contract, compile_ir(contract, quint), output=output)
    output.write_text(source, encoding="utf-8")
    assert "    def store(self, key: str, value: int) -> None:" in source
    assert "    def fetch(self, key: str) -> None:" in source
    assert "    def observe(self) -> View:" in source
    assert "Read = ReadHit | ReadMiss | ReadNoRead" in source
    ruff = [sys.executable, "-m", "ruff"]
    assert subprocess.run([*ruff, "format", "--check", str(output)], capture_output=True).returncode == 0
    assert subprocess.run([*ruff, "check", str(output)], capture_output=True).returncode == 0
    module = load_module(output)
    assert module.LruCacheDriver.__quint_actions__["store"] == ("store", (("key", "key"), ("value", "value")))


def test_sets_maps_tuples_and_observation_records(tmp_path: Path, quint) -> None:
    shutil.copyfile(DATA / "mbt" / "probe.qnt", tmp_path / "probe.qnt")
    manifest = tmp_path / "quint-connect.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "probe",
                "module": "lru_cache",
                "model": "probe.qnt",
                "invariant": "bounded",
                "convention": "mbt",
                "observe": ["entries", "lastRead", "tags", "index", "pair"],
                "actions": {"init": "", "store": "", "fetch": ""},
                "bounds": {"seed": 1, "max_samples": 1, "max_steps": 1, "n_traces": 1},
            }
        ),
        encoding="utf-8",
    )
    contract = load_contract(manifest)
    source = generate_module(contract, compile_ir(contract, quint), output=tmp_path / "probe_driver.py")
    assert "    tags: AbstractSet[str]" in source
    assert "    index: Mapping[str, int]" in source
    assert "    pair: tuple[int, str]" in source
    assert "    entries: list[Entry]" in source
    assert "    def observe(self) -> Observation:" in source
    compile(source, "probe_driver.py", "exec")


def rename_fetch_parameter(model: Path, carried: str) -> None:
    model.write_text(
        model.read_text()
        .replace("action fetch(key: str): bool = {", "action fetch(name: str): bool = {")
        .replace("e => e.key == key)", "e => e.key == name)")
        .replace("without(observed.entries, key).append", "without(observed.entries, name).append")
        .replace(
            'event\' = { op: "fetch", key: key, value: 0 }', f'event\' = {{ op: "fetch", key: {carried}, value: 0 }}'
        ),
        encoding="utf-8",
    )


def test_parameter_is_bound_to_the_event_field_the_action_assigns_it_to(tmp_path: Path, quint) -> None:
    manifest = copy_example(tmp_path)
    rename_fetch_parameter(tmp_path / "lru.qnt", carried="name")
    contract = load_contract(manifest)
    source = generate_module(contract, compile_ir(contract, quint), output=tmp_path / "x.py")
    assert "    def fetch(self, name: str) -> None:" in source
    assert '"fetch": ("fetch", (("name", "key"),)),' in source


def test_a_model_error_is_reported_with_quints_diagnostic_and_an_existing_log(
    tmp_path: Path, quint, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = copy_example(tmp_path)
    model = tmp_path / "lru.qnt"
    # `next` is a Quint built-in; `quint compile` alone exits 1 and prints nothing.
    model.write_text(model.read_text().replace("pure val CAPACITY = 2", "pure val next = 2\n  pure val CAPACITY = 2"))
    assert main(["codegen", str(manifest), "--quint", str(quint.executable)]) == 2
    error = capsys.readouterr().err
    assert "QNT101" in error and "Built-in name 'next' is redefined" in error
    assert error.index("QNT101") < error.index("command failed"), "Quint's diagnostic leads the error"
    log = Path(error.split("log: ", 1)[1].splitlines()[0])
    assert log.name == "typecheck.log" and "QNT101" in log.read_text(encoding="utf-8")
    assert not (tmp_path / "lru_cache_driver.py").exists()


def test_typecheck_uses_the_pinned_quint_and_reports_its_diagnostic(
    tmp_path: Path, quint, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = copy_example(tmp_path)
    flags = ["--quint", str(quint.executable)]
    assert main(["typecheck", str(manifest), *flags]) == 0
    assert "lru.qnt typechecks with Quint 0.33.1" in capsys.readouterr().out
    model = tmp_path / "lru.qnt"
    # A parameter named after a Quint built-in, the trial agent's first error.
    model.write_text(model.read_text().replace("action fetch(key: str)", "action fetch(next: str)"))
    assert main(["typecheck", str(manifest), *flags]) == 2
    assert "Built-in name 'next' is redefined" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["assign", "head"])
def test_a_reused_built_in_name_says_to_rename_it(tmp_path: Path, quint, capsys: pytest.CaptureFixture[str], name: str):
    # The fourth trial named a parameter `assign`; Quint's QNT101 says only that the name is redefined.
    manifest = copy_example(tmp_path)
    model = tmp_path / "lru.qnt"
    model.write_text(model.read_text().replace("action fetch(key: str)", f"action fetch({name}: str)"))
    assert main(["typecheck", str(manifest), "--quint", str(quint.executable)]) == 2
    error = capsys.readouterr().err
    hint = f"hint: rename this action, parameter or definition; '{name}' is reserved in Quint (a built-in name)."
    assert hint in error
    assert error.index(f"Built-in name '{name}' is redefined") < error.index(hint) < error.index("command failed")


def test_the_skill_lists_every_name_quint_reserves(tmp_path: Path) -> None:
    # The bundled Quint's own list, so a Quint upgrade that reserves a new name fails here until the skill says so.
    tool = resolve_quint(load_contract(MANIFEST).quint_version, tmp_path, None)
    names_module = tool.executable.parent.parent / "@informalsystems/quint/dist/src/names/base.js"
    script = "console.log(JSON.stringify(require(process.argv[1]).builtinNames))"
    output = subprocess.run(["node", "-e", script, str(names_module)], capture_output=True, text=True, check=True)
    # `q::test` and the like are not identifiers, so no model can reuse them.
    reserved = {name for name in json.loads(output.stdout) if name.isidentifier()}
    paragraph = SKILL.read_text(encoding="utf-8").split("Every built-in name Quint", 1)[1].split("\n\n", 1)[0]
    assert set(re.findall(r"`([A-Za-z_]\w*)`", paragraph)) == reserved
    # Each one really is refused as a definition name, in one typecheck of a model that reuses them all.
    model = tmp_path / "reserved.qnt"
    model.write_text("module m {\n" + "".join(f"  pure val {name} = 1\n" for name in sorted(reserved)) + "}\n")
    with pytest.raises(QuintError) as refused:
        run([*tool.command, "typecheck", str(model)], tmp_path / "typecheck.log", cwd=tmp_path)
    refused_names = set(re.findall(r"Built-in name '(\w+)' is redefined", (tmp_path / "typecheck.log").read_text()))
    assert refused_names == reserved, refused.value


def test_parameter_that_no_event_field_carries_is_rejected(tmp_path: Path, quint) -> None:
    manifest = copy_example(tmp_path)
    rename_fetch_parameter(tmp_path / "lru.qnt", carried='"constant"')
    contract = load_contract(manifest)
    with pytest.raises(CodegenError, match="parameter 'name' is not carried by any field of the event record"):
        generate_module(contract, compile_ir(contract, quint), output=tmp_path / "x.py")


def camel_case_fetch(directory: Path, *extra_actions: str) -> Path:
    """The LRU example with `fetch(key)` renamed to `fetchEntry(lookupKey)`; the event field stays `key`."""
    manifest = copy_example(directory)
    model = directory / "lru.qnt"
    model.write_text(
        model.read_text()
        .replace("action fetch(key: str): bool = {", "action fetchEntry(lookupKey: str): bool = {")
        .replace("e => e.key == key)", "e => e.key == lookupKey)")
        .replace("without(observed.entries, key).append", "without(observed.entries, lookupKey).append")
        .replace(
            'event\' = { op: "fetch", key: key, value: 0 }', 'event\' = { op: "fetchEntry", key: lookupKey, value: 0 }'
        )
        .replace("fetch(", "fetchEntry("),
        encoding="utf-8",
    )
    document = json.loads(manifest.read_text())
    document["actions"] = {
        ("fetchEntry" if name == "fetch" else name): boundary for name, boundary in document["actions"].items()
    } | dict.fromkeys(extra_actions, "")
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def test_camel_case_actions_and_parameters_get_python_names(tmp_path: Path, quint) -> None:
    contract = load_contract(camel_case_fetch(tmp_path))
    output = tmp_path / "camel_driver.py"
    source = generate_module(contract, compile_ir(contract, quint), output=output)
    assert "    def fetch_entry(self, lookup_key: str) -> None:" in source
    # The trace still names the Quint action and event field; only the Python side is renamed.
    assert '"fetchEntry": ("fetch_entry", (("lookup_key", "key"),)),' in source
    output.write_text(source, encoding="utf-8")
    calls = []

    class Driver(load_module(output).LruCacheDriver):
        def init(self) -> None: ...
        def store(self, key: str, value: int) -> None: ...
        def inspect(self) -> None: ...
        def observe(self) -> dict:
            return {}

        def fetch_entry(self, lookup_key: str) -> None:
            calls.append(lookup_key)

    Driver().step(step("fetchEntry", {"key": "a", "value": 0}, None))
    assert calls == ["a"]


def test_actions_whose_python_names_collide_are_rejected(tmp_path: Path, quint) -> None:
    contract = load_contract(camel_case_fetch(tmp_path, "fetch_entry"))
    with pytest.raises(CodegenError, match="'fetch_entry' maps to the reserved or duplicate method name 'fetch_entry'"):
        generate_module(contract, compile_ir(contract, quint), output=tmp_path / "x.py")


@pytest.fixture
def generated(tmp_path: Path, quint) -> tuple[ModuleType, Path]:
    manifest = copy_example(tmp_path)
    output = tmp_path / "lru_cache_driver.py"
    assert main(["codegen", str(manifest), "-o", str(output), "--quint", str(quint.executable)]) == 0
    return load_module(output), manifest


def cache_driver(base: type) -> type:
    sys.path.insert(0, str(EXAMPLE))
    from lru_cache import LRUCache

    class Driver(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.cache = LRUCache(2)
            self.last: dict = {"tag": "NoRead"}

        def init(self) -> None:
            self.cache = LRUCache(2)

        def store(self, key: str, value: int) -> None:
            self.cache.put(key, value)
            self.last = {"tag": "NoRead"}

        def fetch(self, key: str) -> None:
            value = self.cache.get(key)
            self.last = {"tag": "Miss"} if value is None else {"tag": "Hit", "value": value}

        def inspect(self) -> None:
            pass

        def observe(self) -> dict:
            return {"entries": [{"key": k, "value": v} for k, v in self.cache.items()], "lastRead": self.last}

    return Driver


def test_generated_base_drives_real_traces(generated: tuple[ModuleType, Path]) -> None:
    module, manifest = generated
    driver = cache_driver(module.LruCacheDriver)
    contract = load_contract(manifest)
    for trace in (DATA / "lru" / "sample-5.itf.json", DATA / "lru" / "witness-evicts.itf.json"):
        replay_trace(trace, contract, lambda context: driver())


def test_missing_action_method_cannot_be_instantiated(generated: tuple[ModuleType, Path]) -> None:
    module, _ = generated

    class Incomplete(module.LruCacheDriver):  # type: ignore[misc, name-defined]
        def observe(self) -> dict:
            return {}

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()


def test_stale_generated_base_fails_the_replay(generated: tuple[ModuleType, Path], tmp_path: Path) -> None:
    module, manifest = generated
    driver = cache_driver(module.LruCacheDriver)
    model = tmp_path / "lru.qnt"
    model.write_text(model.read_text() + "\n// a later edit to the model\n", encoding="utf-8")
    with pytest.raises(ContractError, match="is stale.*pytest-quint-connect codegen quint-connect.json"):
        replay_trace(DATA / "lru" / "witness-evicts.itf.json", load_contract(manifest), lambda context: driver())


def test_generated_base_for_another_contract_is_refused(generated: tuple[ModuleType, Path], tmp_path: Path) -> None:
    module, manifest = generated
    data = json.loads(manifest.read_text())
    data["name"] = "other"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ContractError, match="generated for contract 'lru-cache', not 'other'"):
        cache_driver(module.LruCacheDriver)().check_contract(load_contract(manifest))


def step(action: str, args: dict, picks: dict | None) -> Step:
    return Step(0, action, Record(args), Record(picks) if picks is not None else None, State(0, {}, {}))


def test_dispatch_takes_event_fields_then_picks_and_names_what_is_missing() -> None:
    calls = []

    class Driver(ActionDriver):
        __quint_contract__ = "c"
        __quint_fingerprint__ = ""
        __quint_codegen_command__ = ""
        __quint_actions__ = {"put": ("put", (("key", "key"), ("value", "value")))}

        def put(self, key: str, value: int) -> None:
            calls.append((key, value))

        def observe(self) -> dict:
            return {}

    Driver().step(step("put", {"key": "a", "value": 1}, None))
    Driver().step(step("put", {"key": "b"}, {"value": 2, "key": "ignored"}))
    assert calls == [("a", 1), ("b", 2)]
    with pytest.raises(ContractError, match="needs argument 'value'"):
        Driver().step(step("put", {"key": "c"}, {"value": None}))
    with pytest.raises(ContractError, match="no method for action 'get'"):
        Driver().step(step("get", {}, None))


MYPY_DRIVER = """
from lru_cache_driver import LruCacheDriver, View

class Driver(LruCacheDriver):
    def init(self) -> None: ...
    def store(self, key: str, value: {value_type}) -> None: ...
    def fetch(self, key: str) -> None: ...
    def inspect(self) -> None: ...
    def observe(self) -> View:
        return {{"entries": [{{"key": "a", "value": {entry_value}}}], "lastRead": {{"tag": "Miss"}}}}
"""


@pytest.mark.parametrize(
    ("value_type", "entry_value", "expected"),
    [
        ("int", "1", None),
        ("str", "1", "incompatible with supertype"),
        ("int", '"1"', 'TypedDict item "value" has type "int"'),
    ],
)
def test_type_checker_catches_a_driver_that_drifts_from_the_model(
    generated: tuple[ModuleType, Path], tmp_path: Path, value_type: str, entry_value: str, expected: str | None
) -> None:
    (tmp_path / "driver.py").write_text(
        MYPY_DRIVER.format(value_type=value_type, entry_value=entry_value), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", "driver.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    if expected is None:
        assert result.returncode == 0, result.stdout
    else:
        assert result.returncode == 1 and expected in result.stdout, result.stdout
