"""Typed driver generation from real ``quint compile`` output, and dispatch through generated bases."""

import importlib.util
import json
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
from pytest_quint_connect.quint import resolve_quint
from pytest_quint_connect.typed import ActionDriver

DATA = Path(__file__).parent / "data"
DOGFOOD = Path(__file__).resolve().parents[1] / "dogfood"


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
