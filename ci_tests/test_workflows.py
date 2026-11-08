"""A workflow that calls another grants every permission the called jobs ask for.

GitHub refuses to start a run (``startup_failure``) when a job of a called workflow asks for a permission
its caller does not grant. Release 1.2.0's Publish never started for this reason: the CI it calls had begun
reading earlier runs' receipts (``actions: read``, ``checks: read``), so nothing was published and ``v1``
stayed on 1.1.0.

Run as CI does:
    uv run --no-project --python 3.14 --with "$(python scripts/ci/selector_requirement.py)" \
        python -m unittest discover -s ci_tests -v
"""

import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
LEVEL = {"none": 0, "read": 1, "write": 2}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _permissions(job: dict[str, Any], workflow: dict[str, Any]) -> dict[str, str]:
    """A job's permissions: its own block, else its workflow's, as a scope-to-level map."""
    permissions = job.get("permissions", workflow.get("permissions", {}))
    if not isinstance(permissions, dict):
        raise AssertionError(f"use an explicit permissions map, not {permissions!r}")
    return permissions


class CalledWorkflowPermissionTests(unittest.TestCase):
    def test_every_caller_grants_what_its_called_jobs_ask_for(self) -> None:
        calls = 0
        for path in sorted(WORKFLOWS.glob("*.yml")):
            workflow = _load(path)
            for name, job in workflow["jobs"].items():
                uses = job.get("uses", "")
                if not uses.startswith("./"):
                    continue
                calls += 1
                granted = _permissions(job, workflow)
                called = _load(ROOT / uses)
                for called_name, called_job in called["jobs"].items():
                    for scope, level in _permissions(called_job, called).items():
                        with self.subTest(caller=f"{path.name}::{name}", job=called_name, scope=scope):
                            self.assertGreaterEqual(
                                LEVEL[granted.get(scope, "none")],
                                LEVEL[level],
                                f"{uses} job {called_name!r} asks for {scope}: {level}; "
                                f"{path.name} job {name!r} grants {granted.get(scope, 'none')}",
                            )
        # Publish calls CI; if that ever stops, this test checks nothing and should be revisited.
        self.assertGreater(calls, 0)


if __name__ == "__main__":
    unittest.main()
