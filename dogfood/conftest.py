"""Driver for the session-receipts contract: real pytest sessions of the LRU example project."""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from session_driver import Receipt, SessionReceiptsDriver

from pytest_quint_connect import HistoryContext

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parent / "examples" / "lru_cache"
WITNESS = "witness-evictsLeastRecentlyUsedTest-0.itf.json"

# The pytest options for each session kind in session.qnt.
OPTIONS = {
    "full": [],
    "filtered": ["-k", "test_lru_cache and witness"],
    "faulty": ["--lru-implementation", "stale-read"],
    "replay": ["test_lru_contract.py::test_lru_cache", "--quint-replay", "saved.itf.json"],
    "noQuint": ["--quint", "missing-quint-executable"],
}


class SessionDriver(SessionReceiptsDriver):
    """Runs the installed plugin in a child pytest process per step; reads what that session left behind."""

    def __init__(self, context: HistoryContext) -> None:
        self.project = context.work_dir / "project"
        self.mutant = context.mutant
        self.exit_code = -1

    def init(self) -> None:
        shutil.rmtree(self.project, ignore_errors=True)
        shutil.copytree(EXAMPLE, self.project, ignore=shutil.ignore_patterns(".artifacts", "__pycache__"))
        self.exit_code = -1

    def session(self, kind: str, strict: bool) -> None:
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *OPTIONS[kind]]
        if strict:
            command.append("--quint-require-complete")
        env = dict(os.environ)
        env.pop("PYTEST_ADDOPTS", None)
        if self.mutant:
            command[3:3] = ["-p", "plugin_mutants"]
            env["PQC_DOGFOOD_MUTANT"] = self.mutant
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(HERE), env.get("PYTHONPATH")]))
        result = subprocess.run(command, cwd=self.project, env=env, capture_output=True, text=True, timeout=600)
        self.exit_code = result.returncode
        generated = self.project / ".artifacts" / "quint-connect" / "lru-cache" / WITNESS
        if kind != "replay" and generated.is_file():
            # Keep a real witness trace for a later replay session in this history.
            shutil.copyfile(generated, self.project / "saved.itf.json")

    def inspect(self) -> None:
        pass

    def observe(self) -> Receipt:
        receipt = self.project / ".artifacts" / "quint-connect" / "results.json"
        if not receipt.is_file():
            return {"status": "none", "mode": "none", "exitCode": self.exit_code}
        data = json.loads(receipt.read_text(encoding="utf-8"))
        return {"status": data["status"], "mode": data["mode"], "exitCode": self.exit_code}


@pytest.fixture
def session_driver() -> Callable[[HistoryContext], SessionDriver]:
    return SessionDriver
