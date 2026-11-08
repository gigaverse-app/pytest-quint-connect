"""Manifests are validated strictly when collected."""

import json
from pathlib import Path

import pytest
from lru_support import MANIFEST

from pytest_quint_connect.contract import ContractError, Mutant, Regression, load_contract


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
        ({"observe": []}, "observe"),
        ({"attest": {"package": "x"}}, "attest"),
        ({"mutants": "stale-read"}, "list of names or mutant objects"),
        ({"mutants": [""]}, "must be a name or an object"),
        ({"mutants": ["a", {"name": "a", "description": "again"}]}, "repeats"),
        ({"mutants": [{"name": "a"}]}, "exactly the fields"),
        ({"mutants": [{"name": "a", "description": "x", "severity": "high"}]}, "exactly the fields"),
        ({"mutants": [{"name": "a", "description": ""}]}, "non-empty strings"),
        ({"mutants": [{"name": "a", "description": "x", "regression": {"ref": "abc"}}]}, "regression must be"),
        (
            {"mutants": [{"name": "a", "description": "x", "regression": {"ref": "abc", "summary": "s", "url": "u"}}]},
            "regression must be",
        ),
    ],
)
def test_invalid_manifests_are_rejected(tmp_path: Path, change: dict, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        load_contract(write(tmp_path, **change))


def test_mutants_are_names_or_described_objects(tmp_path: Path) -> None:
    regression = {"ref": "389c5bb", "summary": "reads did not refresh recency"}
    contract = load_contract(
        write(
            tmp_path,
            mutants=[
                "plain",
                {"name": "described", "description": "drops the newest entry"},
                {"name": "stale-read", "description": "reads skip the recency update", "regression": regression},
            ],
        )
    )
    assert contract.mutants == ("plain", "described", "stale-read")
    assert contract.case_ids()[-4:] == ["mutant-plain", "mutant-described", "mutant-stale-read", "coverage"]
    assert contract.mutant_specs == (
        Mutant("plain"),
        Mutant("described", "drops the newest entry"),
        Mutant("stale-read", "reads skip the recency update", Regression("389c5bb", "reads did not refresh recency")),
    )
    assert [mutant.as_json() for mutant in contract.mutant_specs] == [
        {"name": "plain"},
        {"name": "described", "description": "drops the newest entry"},
        {"name": "stale-read", "description": "reads skip the recency update", "regression": regression},
    ]


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
