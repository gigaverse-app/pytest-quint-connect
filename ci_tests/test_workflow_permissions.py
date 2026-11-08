"""A workflow that calls a local reusable workflow grants every permission the called jobs ask for.

GitHub refuses to start such a run when a called job asks for more than the caller grants: the run ends
as a startup_failure with no jobs and no log. The v1.2.0 Publish run did exactly that after ci.yml's
select-tests job began reading earlier runs (actions, checks), so 1.2.0 never reached PyPI.

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


def load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path} is not a workflow"
    return document


def grants(permissions: object) -> dict[str, int]:
    """A permissions block as scope -> level. ``read-all``/``write-all`` grant every scope listed in a request."""
    if permissions in (None, {}):
        return {}
    if isinstance(permissions, str):
        return {"*": LEVEL["read" if permissions == "read-all" else "write"]}
    assert isinstance(permissions, dict), permissions
    return {scope: LEVEL[level] for scope, level in permissions.items()}


def needed(called: dict[str, Any]) -> dict[str, int]:
    """The most any job of the called workflow, or the workflow itself, asks for, per scope."""
    asks: dict[str, int] = {}
    blocks = [called.get("permissions")] + [job.get("permissions") for job in called.get("jobs", {}).values()]
    for block in blocks:
        for scope, level in grants(block).items():
            asks[scope] = max(asks.get(scope, 0), level)
    return asks


def missing(caller: dict[str, Any]) -> list[str]:
    """Each local reusable call whose granted permissions fall short of what the called workflow needs."""
    problems = []
    for name, job in caller.get("jobs", {}).items():
        uses = job.get("uses", "")
        if not uses.startswith("./.github/workflows/"):
            continue
        # A calling job without its own block passes on the caller workflow's.
        granted = grants(job["permissions"] if "permissions" in job else caller.get("permissions"))
        for scope, level in needed(load(ROOT / uses)).items():
            have = granted.get(scope, granted.get("*", 0))
            if have < level:
                want = next(word for word, value in LEVEL.items() if value == level)
                problems.append(f"job {name!r} calls {uses} but does not grant {scope}: {want}")
    return problems


class ReusableWorkflowPermissionTests(unittest.TestCase):
    def test_every_local_reusable_call_grants_what_the_called_jobs_need(self) -> None:
        callers = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(any("./.github/workflows/" in path.read_text(encoding="utf-8") for path in callers))
        for path in callers:
            with self.subTest(workflow=path.name):
                self.assertEqual(missing(load(path)), [])

    def test_the_check_reports_the_grant_that_stopped_v1_2_0(self) -> None:
        # Control: the Publish workflow as it was at v1.2.0, granting only contents and pull-requests.
        caller = load(WORKFLOWS / "release.yml")
        caller["jobs"]["checks"]["permissions"] = {"contents": "read", "pull-requests": "write"}
        self.assertEqual(
            missing(caller),
            [
                "job 'checks' calls ./.github/workflows/ci.yml but does not grant actions: read",
                "job 'checks' calls ./.github/workflows/ci.yml but does not grant checks: read",
            ],
        )


if __name__ == "__main__":
    unittest.main()
