"""Manifests are validated strictly when collected."""

import json
from pathlib import Path

import pytest
from lru_support import MANIFEST

from pytest_quint_connect.contract import Bug, ContractError, Mutant, load_contract


def write(tmp_path: Path, **changes: object) -> Path:
    data = json.loads(MANIFEST.read_text())
    data["sources"] = []
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    (tmp_path / "lru.qnt").write_text((MANIFEST.parent / "lru.qnt").read_text())
    path = tmp_path / "quint-connect.json"
    path.write_text(json.dumps(data))
    return path


def test_example_manifest_loads() -> None:
    contract = load_contract(MANIFEST)
    assert contract.name == "lru-cache"
    assert contract.case_ids()[:1] == ["witness-evictsLeastRecentlyUsedTest"]
    assert contract.case_ids()[-2:] == ["mutant-stale-read", "coverage"]
    assert len(contract.case_ids()) == 3 + 12 + 1 + 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"surprise": 1}, "unknown fields"),
        ({"model": None}, "lacks"),
        ({"version": 2}, "unsupported manifest version"),
        ({"model": "missing.qnt"}, "model not found"),
        ({"convention": "mbt"}, "cannot drive named witnesses"),
        ({"convention": "other"}, "convention"),
        ({"bounds": {"seed": 1, "max_samples": 1, "max_steps": 1, "n_traces": 1, "x": 1}}, "unknown bounds"),
        ({"bounds": {"seed": 1, "max_samples": 0, "max_steps": 1, "n_traces": 1}}, "max_samples"),
        ({"witnesses": ["a", "a"]}, "repeats"),
        ({"witnesses": [], "bounds": {"seed": 1, "max_samples": 1, "max_steps": 1, "n_traces": 0}}, "at least one"),
        ({"historical": "lru.qnt"}, "together"),
        ({"sources": ["nope.py"]}, "source missing"),
        ({"codegen_header": "LICENSE_HEADER"}, "codegen_header not found"),
        ({"codegen_header": ""}, "codegen_header"),
        ({"observe": []}, "observe"),
        ({"attest": {"package": "x"}}, "attest"),
        ({"mutants": "stale-read"}, "list of names or mutant objects"),
        ({"mutants": [""]}, "must be a name or an object"),
        ({"mutants": ["a", {"name": "a", "description": "again"}]}, "repeats"),
        ({"mutants": [{"name": "a"}]}, "must be an object with the fields"),
        ({"mutants": [{"name": "a", "description": "x", "severity": "high"}]}, "must be an object with the fields"),
        ({"mutants": [{"name": "a", "description": ""}]}, "non-empty string"),
        ({"bugs_found": {"summary": "s", "fixed_in": "abc"}}, "list of objects"),
        ({"bugs_found": [{"summary": "s"}]}, "must be an object with the fields"),
        (
            {"bugs_found": [{"summary": "s", "fixed_in": "abc", "severity": "high"}]},
            "must be an object with the fields",
        ),
        ({"bugs_found": [{"summary": "s", "fixed_in": "abc", "mutant": "nope"}]}, "undeclared mutants"),
        (
            {"bugs_found": [{"summary": "s", "fixed_in": "a", "mutant": "stale-read"}] * 2},
            "same mutant",
        ),
    ],
)
def test_invalid_manifests_are_rejected(tmp_path: Path, change: dict, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        load_contract(write(tmp_path, **change))


def test_mutants_are_names_or_described_objects(tmp_path: Path) -> None:
    contract = load_contract(
        write(tmp_path, mutants=["plain", {"name": "described", "description": "drops the newest"}])
    )
    assert contract.mutants == ("plain", "described")
    assert contract.case_ids()[-3:] == ["mutant-plain", "mutant-described", "coverage"]
    assert contract.mutant_specs == (Mutant("plain"), Mutant("described", "drops the newest"))
    assert [mutant.as_json() for mutant in contract.mutant_specs] == [
        {"name": "plain"},
        {"name": "described", "description": "drops the newest"},
    ]
    assert contract.bugs_found == () and contract.bug_reintroduced_by("plain") is None


def test_bugs_found_link_a_fixed_defect_to_the_mutant_that_reintroduces_it(tmp_path: Path) -> None:
    found = {
        "summary": "reads did not refresh recency",
        "fixed_in": "389c5bb",
        "found_by": "witness evictsLeastRecentlyUsedTest",
        "regression_test": "tests/test_cache.py::test_read_refreshes_recency",
        "mutant": "stale-read",
    }
    declared_only = {"summary": "a crash on empty input", "fixed_in": "#12"}
    contract = load_contract(write(tmp_path, bugs_found=[found, declared_only]))
    assert contract.bugs_found == (Bug(**found), Bug("a crash on empty input", "#12"))
    assert contract.bug_reintroduced_by("stale-read") == Bug(**found)
    assert [bug.as_json() for bug in contract.bugs_found] == [found, declared_only]


def test_mbt_convention_without_witnesses_is_allowed(tmp_path: Path) -> None:
    contract = load_contract(write(tmp_path, convention="mbt", witnesses=[]))
    assert contract.convention == "mbt" and contract.case_ids()[0] == "sample-0"


def test_historical_model_is_pinned_by_hash(tmp_path: Path) -> None:
    (tmp_path / "audit.qnt").write_bytes(b"// historical\n")
    import hashlib

    digest = hashlib.sha256(b"// historical\n").hexdigest()
    contract = load_contract(write(tmp_path, historical="audit.qnt", historical_sha256=digest))
    contract.check_historical()
    (tmp_path / "audit.qnt").write_bytes(b"// edited historical evidence\n")
    with pytest.raises(ContractError, match="historical model changed"):
        contract.check_historical()


def test_a_model_names_its_states_with_a_variable_the_driver_is_not_compared_on(tmp_path: Path) -> None:
    assert load_contract(MANIFEST).state_names == "fill"
    assert load_contract(write(tmp_path, state_names=None)).state_names is None
    # Naming states must never change what the code is checked against.
    with pytest.raises(ContractError, match="state_names 'observed' is also observed"):
        load_contract(write(tmp_path, state_names="observed"))
    with pytest.raises(ContractError, match="'state_names' must be a non-empty string"):
        load_contract(write(tmp_path, state_names=""))


def test_a_model_names_its_steps_with_another_variable_the_driver_is_not_compared_on(tmp_path: Path) -> None:
    assert load_contract(MANIFEST).step_names == "change"
    with pytest.raises(ContractError, match="step_names 'observed' is also observed"):
        load_contract(write(tmp_path, step_names="observed"))
    with pytest.raises(ContractError, match="must be different variables"):
        load_contract(write(tmp_path, step_names="fill"))
