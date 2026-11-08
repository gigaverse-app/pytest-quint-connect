"""Publish calls the CI workflow; GitHub refuses to start a called workflow whose jobs ask for more than the caller grants.

v1.2.0 never reached PyPI: a CI job gained `actions: read` and `checks: read`, the Publish call still granted only
`contents: read` and `pull-requests: write`, and the Publish run ended in `startup_failure` before any job ran.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
LEVEL = {"none": 0, "read": 1, "write": 2}


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def requested(called: dict) -> dict[str, str]:
    """The highest level each scope is requested at, across the workflow and every job."""
    wanted: dict[str, str] = {}
    blocks = [called.get("permissions") or {}] + [job.get("permissions") or {} for job in called["jobs"].values()]
    for block in blocks:
        for scope, level in block.items():
            if LEVEL[level] > LEVEL.get(wanted.get(scope, "none"), 0):
                wanted[scope] = level
    return wanted


class CalledWorkflowPermissionTests(unittest.TestCase):
    def test_publish_grants_every_permission_ci_requests(self) -> None:
        publish = workflow("release.yml")
        calls = {name: job for name, job in publish["jobs"].items() if job.get("uses") == "./.github/workflows/ci.yml"}
        self.assertTrue(calls, "Publish no longer calls ci.yml; update this test")
        wanted = requested(workflow("ci.yml"))
        for name, job in calls.items():
            granted = job.get("permissions") or {}
            missing = {
                scope: level for scope, level in wanted.items() if LEVEL[level] > LEVEL[granted.get(scope, "none")]
            }
            self.assertEqual(
                missing,
                {},
                f"release.yml job {name!r} must grant {missing} to ci.yml, or Publish fails at startup",
            )


if __name__ == "__main__":
    unittest.main()
