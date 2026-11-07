"""This repository's lane and file selection, exercised on real Git snapshots of the working tree.

Run as CI does:
    uv run --no-project --python 3.14 --with "$(python scripts/ci/selector_requirement.py)" \
        python -m unittest discover -s ci_tests -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pytest_selective.ci import LaneConfig, plan
from pytest_selective.impact_models import ImpactConfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ci_file_config import file_config  # noqa: E402


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.temporary.name)
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-xf", "-", "-C", str(cls.repo)], input=archive, check=True)
        # Use the working configuration, so these tests reproduce a routing bug before its fix is committed.
        for path in (".github/test-lanes.json", "tests/.gv-dependencies.yaml"):
            (cls.repo / path).write_bytes((ROOT / path).read_bytes())
        cls.config = LaneConfig.model_validate_json((ROOT / ".github/test-lanes.json").read_text())
        cls.files: ImpactConfig = file_config(cls.config)
        cls.outside = tempfile.TemporaryDirectory()
        cls.impact = Path(cls.outside.name) / "impact.json"
        cls.impact.write_text(cls.files.model_dump_json())
        cls.git("init", "-q")
        cls.git("config", "user.email", "ci@example.invalid")
        cls.git("config", "user.name", "CI routing test")
        cls.git("config", "core.autocrlf", "false")
        cls.base = cls.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()
        cls.outside.cleanup()

    @classmethod
    def git(cls, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=cls.repo, text=True).strip()

    @classmethod
    def commit(cls) -> str:
        cls.git("add", "-A")
        cls.git("commit", "-qm", "routing scenario", "--allow-empty")
        return cls.git("rev-parse", "HEAD")

    def change(self, path: str, *, delete: bool = False) -> str:
        self.git("reset", "-q", "--hard", self.base)
        self.git("clean", "-qfd")
        target = self.repo / path
        if delete:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as output:
                output.write("\n")
        return self.commit()

    def lanes(self, path: str, *, delete: bool = False) -> set[str]:
        result = plan(self.repo, self.config, self.base, self.change(path, delete=delete))
        self.assertNotIn("fallback", result.reason, result.model_dump_json())
        return {name for name, enabled in result.lanes.items() if enabled}

    def selected_files(self, path: str) -> tuple[str, list[str]]:
        head = self.change(path)
        output = Path(self.outside.name) / "plan.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest_selective.impact_cli",
                "affected",
                "--repo",
                str(self.repo),
                "--config",
                str(self.impact),
                "--base",
                self.base,
                "--head",
                head,
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
        result = json.loads(output.read_text())
        return result["mode"], sorted(result["selected"])

    def test_prose_starts_no_lane(self) -> None:
        for path in ("README.md", "docs/limits.md", "CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md"):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), set())

    def test_environment_and_workflow_changes_run_every_lane(self) -> None:
        for path in ("uv.lock", "pyproject.toml", ".github/workflows/ci.yml", ".github/test-lanes.json"):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), set(self.config.lanes))

    def test_plugin_source_runs_every_lane(self) -> None:
        self.assertEqual(self.lanes("src/pytest_quint_connect/plugin.py"), {"tests", "dogfood"})

    def test_example_feeds_both_lanes(self) -> None:
        self.assertEqual(self.lanes("examples/lru_cache/lru.qnt"), {"tests", "dogfood"})

    def test_dogfood_only_changes_keep_the_dogfood_lane(self) -> None:
        self.assertEqual(self.lanes("dogfood/plugin_mutants.py"), {"dogfood"})
        self.assertEqual(self.lanes("dogfood/test_session_contract.py", delete=True), {"dogfood"})

    def test_unit_tests_and_fixtures_keep_the_tests_lane(self) -> None:
        self.assertEqual(self.lanes("tests/data/lru/sample-5.itf.json"), {"tests"})
        self.assertEqual(self.lanes("tests/test_compare.py", delete=True), {"tests"})

    def test_a_fixture_selects_only_its_readers(self) -> None:
        self.assertEqual(
            self.selected_files("tests/data/lru/sample-5.itf.json"),
            ("scoped", ["tests/test_codegen.py", "tests/test_itf.py", "tests/test_replay.py"]),
        )

    def test_the_quint_lock_selects_every_test_that_installs_quint(self) -> None:
        mode, selected = self.selected_files("src/pytest_quint_connect/toolchain/package-lock.json")
        self.assertEqual(mode, "scoped")
        self.assertTrue({"tests/test_plugin.py", "tests/test_codegen.py", "tests/test_release.py"} <= set(selected))

    def test_prose_selects_no_test_file(self) -> None:
        self.assertEqual(self.selected_files("docs/drivers.md"), ("none", []))


if __name__ == "__main__":
    unittest.main()
