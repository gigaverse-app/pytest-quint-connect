"""Derive file selection inside the tests lane from the same graph CI-esta plans lanes with.

`ci-esta lanes --json` resolves .github/test-lanes.json over the workflow: the inferred graph with the
lane file's `graph` fields (module roots, scoped hooks, floor budget) and the reviewed pytest policy.
File selection adds what only it needs: the files the tests lane may run, and the files that decide
how they run, which select every test file when they change.
"""

import json
import subprocess
import sys
from pathlib import Path

from pytest_selective.impact_models import ImpactConfig

LANE_FILE = ".github/test-lanes.json"
# The tests lane runs tests/ and, when selected, the dogfood contract.
FILE_ROOTS = ["tests", "dogfood"]
# How the selected files are executed: a change to these runs every test file of the lane.
EXECUTION_INPUTS = [
    LANE_FILE,
    ".github/workflows/ci.yml",
    "scripts/run_ci_tests.py",
    "scripts/ci_file_config.py",
    "scripts/ci/selector_requirement.py",
]
PYTHON_ENVIRONMENT = "python-environment"


def file_config(resolved: dict) -> ImpactConfig:
    """`resolved` is the `ci-esta lanes --json` document."""
    graph = ImpactConfig.model_validate(resolved["config"]["graph"])
    # plan-work applies a replaced python-environment rule as the global inputs; do the same here.
    environment = [
        rule["when"]
        for rule in resolved["rules"]
        if rule["name"] == PYTHON_ENVIRONMENT and rule["select"] == ["all"] and rule.get("origin") != "built-in"
    ]
    if len(environment) != 1:
        raise ValueError(
            f"{LANE_FILE} must replace the {PYTHON_ENVIRONMENT} rule with this repository's test environment"
        )
    return graph.model_copy(
        update={"test_roots": FILE_ROOTS, "global_inputs": sorted(set(environment[0]) | set(EXECUTION_INPUTS))}
    )


def resolve(repo: Path) -> dict:
    output = subprocess.run(
        ["ci-esta", "lanes", "--repo", str(repo), "--config", str(repo / LANE_FILE), "--json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(output)


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(file_config(resolve(Path("."))).model_dump_json(), encoding="utf-8")
