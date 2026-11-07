"""One version everywhere: pyproject, the installed package, Release Please and uv.lock."""

import json
import tomllib
from pathlib import Path

import pytest_quint_connect

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    (locked,) = [package["version"] for package in lock["package"] if package["name"] == project["name"]]
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())["."]
    assert project["version"] == locked == manifest == pytest_quint_connect.__version__


def test_release_please_updates_the_lock() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text())["packages"]["."]
    assert config["package-name"] == "pytest-quint-connect"
    assert any(extra["path"] == "uv.lock" for extra in config["extra-files"])


def test_wheel_ships_the_quint_lock() -> None:
    toolchain = Path(pytest_quint_connect.__file__).parent / "toolchain"
    package = json.loads((toolchain / "package.json").read_text())
    lock = json.loads((toolchain / "package-lock.json").read_text())
    pinned = package["dependencies"]["@informalsystems/quint"]
    assert lock["packages"]["node_modules/@informalsystems/quint"]["version"] == pinned
    from pytest_quint_connect.contract import DEFAULT_QUINT_VERSION

    assert pinned == DEFAULT_QUINT_VERSION
