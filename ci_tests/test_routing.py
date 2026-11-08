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

    def change(self, *paths: str, delete: bool = False) -> str:
        """One commit on the base that appends a line to (or deletes) every path."""
        self.git("reset", "-q", "--hard", self.base)
        self.git("clean", "-qfd")
        for path in paths:
            target = self.repo / path
            if delete:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as output:
                    output.write("\n")
        return self.commit()

    def lanes(self, *paths: str, delete: bool = False) -> set[str]:
        result = plan(self.repo, self.config, self.base, self.change(*paths, delete=delete))
        self.assertNotIn("fallback", result.reason, result.model_dump_json())
        return {name for name, enabled in result.lanes.items() if enabled}

    def selected_files(self, *paths: str) -> tuple[str, list[str]]:
        head = self.change(*paths)
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
        for path in (
            "docs/limits.md",
            "CONTRIBUTING.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            "examples/lru_cache/README.md",
            "dogfood/README.md",
            "documentation/index.md",
            "mkdocs.yml",
            ".readthedocs.yaml",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), set())

    # Read the Docs dependencies match pytest-selective's built-in environment policy
    # (`*requirements*.txt`); the explicit graph.global_inputs keep them out of the test environment.
    DOCUMENTATION_ONLY = (
        "docs/limits.md",
        "CONTRIBUTING.md",
        "examples/lru_cache/README.md",
        "dogfood/README.md",
        "documentation/requirements.in",
        "documentation/requirements.txt",
    )

    def test_a_documentation_only_change_runs_nothing(self) -> None:
        self.assertEqual(self.lanes(*self.DOCUMENTATION_ONLY), set())
        self.assertEqual(self.selected_files(*self.DOCUMENTATION_ONLY), ("none", []))

    def test_the_root_readme_and_license_rebuild_only_the_package(self) -> None:
        # pyproject embeds both in the distribution; `twine check --strict` renders the README.
        for path in ("README.md", "LICENSE"):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), {"package"})
                self.assertEqual(self.selected_files(path), ("none", []))

    def test_release_metadata_runs_the_release_test_and_the_package_lane(self) -> None:
        for path in (".release-please-manifest.json", "release-please-config.json"):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), {"tests", "package"})
                self.assertEqual(self.selected_files(path), ("scoped", ["tests/test_release.py"]))

    def test_the_smoke_script_runs_only_the_package_lane(self) -> None:
        self.assertEqual(self.lanes("scripts/smoke_distribution.py"), {"package"})

    def test_routing_inputs_run_only_the_routing_lane(self) -> None:
        # The Publish workflow calls ci.yml; its grants are checked in ci_tests/test_workflow_permissions.py.
        for path in (
            "ci_tests/test_routing.py",
            "scripts/ci/selector_requirement.py",
            "ci_tests/test_workflow_permissions.py",
            ".github/workflows/release.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), {"routing"})
        # The read declarations steer both file selection and these regressions.
        self.assertEqual(self.lanes("tests/.gv-dependencies.yaml"), {"tests", "routing"})

    def test_environment_and_workflow_changes_run_every_lane(self) -> None:
        for path in ("uv.lock", "pyproject.toml", ".github/workflows/ci.yml", ".github/test-lanes.json"):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), set(self.config.lanes))

    def test_plugin_source_runs_every_lane(self) -> None:
        self.assertEqual(self.lanes("src/pytest_quint_connect/plugin.py"), set(self.config.lanes))

    def test_example_feeds_the_tests_dogfood_and_package_lanes(self) -> None:
        for path in ("examples/lru_cache/lru.qnt", "examples/lru_cache/quint-connect.json"):
            with self.subTest(path=path):
                self.assertEqual(self.lanes(path), {"tests", "dogfood", "package"})
        self.assertEqual(self.lanes("examples/lru_cache/lru_cache.py"), {"tests", "dogfood", "package"})

    def test_the_example_code_selects_only_the_tests_that_load_it(self) -> None:
        # The example conftest's pytest_addoption applies only when pytest runs in examples/lru_cache
        # (scoped_hooks), so the example reaches the tests that load it through lru_support, not test_itf.
        self.assertEqual(
            self.selected_files("examples/lru_cache/lru_cache_driver.py"),
            (
                "scoped",
                [
                    "dogfood/test_session_contract.py",
                    "tests/test_codegen.py",
                    "tests/test_contract.py",
                    "tests/test_plugin.py",
                    "tests/test_replay.py",
                ],
            ),
        )
        # An edit to the conftest itself no longer matches the reviewed pin, so its hooks count as global again.
        self.assertEqual(self.selected_files("examples/lru_cache/conftest.py")[0], "full")

    def test_scoped_hooks_pin_the_reviewed_conftest(self) -> None:
        for path, reviewed in self.files.scoped_hooks.items():
            with self.subTest(path=path):
                current = subprocess.check_output(["git", "hash-object", path], cwd=ROOT, text=True).strip()
                self.assertEqual(
                    current,
                    reviewed,
                    f"{path} changed. Check that its pytest_* hooks still apply only when pytest runs in its "
                    f"directory, then set scoped_hooks[{path!r}] in .github/test-lanes.json to `git hash-object {path}`.",
                )

    def test_report_code_stays_out_of_the_plugins_import_closure(self) -> None:
        # pytest loads the plugin in every session, so whatever it imports reaches every test file.
        for path in ("src/pytest_quint_connect/report.py", "src/pytest_quint_connect/ledger.py"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.selected_files(path),
                    ("scoped", ["tests/test_codegen.py", "tests/test_ledger.py", "tests/test_report.py"]),
                )

    def test_report_action_runs_the_job_that_uses_it(self) -> None:
        self.assertEqual(self.lanes("report/action.yml"), {"dogfood"})
        self.assertEqual(self.lanes("report/publish.cjs"), {"dogfood"})

    def test_dogfood_only_changes_keep_the_dogfood_lane(self) -> None:
        self.assertEqual(self.lanes("dogfood/plugin_mutants.py"), {"dogfood"})
        self.assertEqual(self.lanes("dogfood/test_session_contract.py", delete=True), {"dogfood"})
        # The dogfood model is also a unit-test fixture.
        self.assertEqual(self.lanes("dogfood/session.qnt"), {"tests", "dogfood"})

    def test_the_dogfood_conftest_runs_the_lanes_that_load_it(self) -> None:
        # Its fixtures start child pytest sessions; it registers nothing in the parent session. It also
        # reaches the unit tests because the graph resolves both top-level `conftest` modules (dogfood and
        # the example are module roots) as one name.
        self.assertEqual(self.lanes("dogfood/conftest.py"), {"tests", "dogfood"})

    def test_pytest_configuration_runs_every_lane(self) -> None:
        for path in ("dogfood/pytest.ini", "examples/lru_cache/pytest.ini"):
            with self.subTest(path=path):
                # Each is a reviewed pytest policy, so an edit also changes its fingerprint.
                result = plan(self.repo, self.config, self.base, self.change(path))
                self.assertTrue(all(result.lanes.values()), result.model_dump_json())

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
