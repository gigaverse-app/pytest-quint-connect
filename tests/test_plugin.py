"""End-to-end plugin behavior: real Quint generation, real pytest sessions, the LRU example.

Quint comes from ``QUINT``/``--quint`` or the plugin's locked npm install. These
tests never skip when Quint is missing: they fail, like a required check would.
"""

import json
import shlex
import shutil
from pathlib import Path
from xml.etree import ElementTree

import pytest
from lru_support import EXAMPLE


@pytest.fixture
def example(pytester: pytest.Pytester) -> Path:
    for name in (
        "lru.qnt",
        "quint-connect.json",
        "lru_cache.py",
        "lru_cache_driver.py",
        "conftest.py",
        "test_lru_contract.py",
        "pytest.ini",
    ):
        shutil.copyfile(EXAMPLE / name, pytester.path / name)
    return pytester.path


def assert_output(result: pytest.RunResult, *snippets: str) -> None:
    text = result.stdout.str() + result.stderr.str()
    for snippet in snippets:
        assert snippet in text, f"missing from pytest output: {snippet!r}"


def receipt(root: Path) -> dict:
    return json.loads((root / ".artifacts/quint-connect/results.json").read_text(encoding="utf-8"))


# Test data only: the example's stale-read cache was never shipped, so its committed manifest lists no bug found.
STALE_READ_FOUND = {"summary": "reads did not refresh recency", "fixed_in": "example-only", "mutant": "stale-read"}


def edit_manifest(root: Path, **changes: object) -> None:
    path = root / "quint-connect.json"
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_complete_run_passes_and_records_evidence(pytester: pytest.Pytester, example: Path) -> None:
    result = pytester.runpytest("--quint-require-complete", "--junitxml=junit.xml")
    result.assert_outcomes(passed=34)
    assert_output(result, "test_lru_cache: 17/17 cases passed; 15 fresh histories; 1/1 mutants detected")
    evidence = receipt(example)
    assert evidence["status"] == "passed" and evidence["mode"] == "generate-and-replay"
    (contract,) = evidence["contracts"]
    assert contract["quint"] == "0.33.1" and len(contract["traces"]) == 15
    assert all(test["complete"] for test in contract["tests"])
    mutation = contract["tests"][0]["mutations"]["stale-read"]
    assert mutation["differences"] and mutation["detected_by"]
    # An ordinary mutant stops at the first history that catches it.
    assert "diverged" not in mutation and "histories" not in mutation
    assert contract["mutants"] == ["stale-read"]
    assert contract["mutant_specs"] == [json.loads((EXAMPLE / "quint-connect.json").read_text())["mutants"][0]]
    assert contract["bugs_found"] == []
    assert set(contract["sha256"]) == {"quint-connect.json", "lru.qnt", "lru_cache.py"}
    cases = ElementTree.parse(example / "junit.xml").getroot().iter("testcase")
    properties = {p.get("name") for case in cases for p in case.iter("property")}
    assert {"quint_trace", "quint_seed", "quint_detected_by"} <= properties


def test_collection_lists_selectable_cases(pytester: pytest.Pytester, example: Path) -> None:
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]",
            "test_lru_contract.py::test_lru_cache[sample-11]",
            "test_lru_contract.py::test_lru_cache[mutant-stale-read]",
            "test_lru_contract.py::test_lru_cache[coverage]",
        ]
    )
    assert not (example / ".artifacts/quint-connect/lru-cache").exists(), "collection must not run Quint"


def test_behavioral_fault_fails_with_diff_reproduction_and_artifact(pytester: pytest.Pytester, example: Path) -> None:
    result = pytester.runpytest("--lru-implementation", "stale-read", "-k", "test_lru_cache")
    outcomes = result.parseoutcomes()
    assert outcomes["failed"] >= 1
    assert_output(
        result,
        "implementation state differs from the model",
        "state:  3 after action fetch(key='a', value=0)",
        "entries[0].key: values differ",
        "reproduce: pytest test_lru_contract.py::test_lru_cache --quint-replay",
    )
    saved = (
        example / ".artifacts/quint-connect/lru-cache/mismatch-test_lru_cache-witness-evictsLeastRecentlyUsedTest.json"
    )
    assert json.loads(saved.read_text())["state"] == 3
    evidence = receipt(example)
    assert evidence["status"] == "failed"
    assert evidence["root"] == example.as_posix()
    finding = evidence["contracts"][0]["tests"][0]["findings"]["witness-evictsLeastRecentlyUsedTest"]
    assert finding["state"] == 3 and finding["action"] == "fetch"
    assert finding["arguments"] == {"key": "a", "value": 0}
    assert finding["differences"][0] == "entries[0].key: values differ; expected: 'b'; actual: 'a'"
    assert finding["difference_count"] == 4
    assert finding["mismatch"] == (
        ".artifacts/quint-connect/lru-cache/mismatch-test_lru_cache-witness-evictsLeastRecentlyUsedTest.json"
    )


def test_reproduce_command_runs_as_printed_with_an_explicit_config_file(pytester: pytest.Pytester) -> None:
    # The layout of a repository whose contracts live apart from its main suite (docs/ci.md):
    # invoked from the repository root with -c, so rootdir is contracts/, not the invocation directory.
    nested = pytester.path / "contracts" / "lru"
    nested.mkdir(parents=True)
    for name in ("lru.qnt", "quint-connect.json", "lru_cache.py", "lru_cache_driver.py", "test_lru_contract.py"):
        shutil.copyfile(EXAMPLE / name, nested / name)
    shutil.copyfile(EXAMPLE / "conftest.py", pytester.path / "contracts" / "conftest.py")
    (pytester.path / "contracts" / "quint.ini").write_text("[pytest]\npythonpath = lru\n", encoding="utf-8")
    (pytester.path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    config = ["-c", "contracts/quint.ini", "--lru-implementation", "stale-read"]
    failed = pytester.runpytest(*config, "contracts", "-k", "test_lru_cache and evictsLeastRecentlyUsedTest")
    failed.assert_outcomes(failed=1, deselected=33)
    line = next(line for line in failed.outlines if line.strip().startswith("reproduce: "))
    words = shlex.split(line.strip().removeprefix("reproduce: "))
    trace = "contracts/.artifacts/quint-connect/lru-cache/witness-evictsLeastRecentlyUsedTest-0.itf.json"
    assert words == [
        "pytest",
        "-c",
        "contracts/quint.ini",
        "contracts/lru/test_lru_contract.py::test_lru_cache",
        "--quint-replay",
        trace,
    ]
    evidence = json.loads((pytester.path / "contracts" / ".artifacts/quint-connect/results.json").read_text())
    assert evidence["config_file"] == "quint.ini"
    # Exactly as printed, from the same directory: it selects the test and reaches the same mismatch.
    replayed = pytester.runpytest(*words[1:], "--lru-implementation", "stale-read")
    replayed.assert_outcomes(failed=1)
    assert_output(replayed, "state:  3 after action fetch(key='a', value=0)")


def test_saved_trace_replays_exactly_and_is_never_complete(pytester: pytest.Pytester, example: Path) -> None:
    pytester.runpytest("-k", "witness").assert_outcomes(passed=6)
    trace = example / ".artifacts/quint-connect/lru-cache/witness-evictsLeastRecentlyUsedTest-0.itf.json"
    kept = example / "kept.itf.json"
    shutil.copyfile(trace, kept)
    node = "test_lru_contract.py::test_lru_cache"
    replayed = pytester.runpytest(node, "--quint-replay", str(kept))
    replayed.assert_outcomes(passed=1)
    evidence = receipt(example)
    assert evidence["mode"] == "replay-only" and evidence["source_trace"].endswith("kept.itf.json")
    assert evidence["incomplete_required"] == [
        "replay-only session: replaying one saved trace is never complete coverage"
    ]
    assert pytester.runpytest(node, "--quint-replay", str(kept), "--quint-require-complete").ret == 1
    pytester.runpytest(node, "--quint-replay", str(kept), "--quint-mutant", "stale-read").assert_outcomes(failed=1)
    pytester.runpytest(node, "--quint-replay", str(kept), "--lru-implementation", "stale-read").assert_outcomes(
        failed=1
    )
    ambiguous = pytester.runpytest("--quint-replay", str(kept))
    assert ambiguous.ret == pytest.ExitCode.USAGE_ERROR
    assert_output(ambiguous, "needs exactly one selected contract test; 2 were collected")


def test_filtered_run_is_partial_and_fails_when_completeness_is_required(
    pytester: pytest.Pytester, example: Path
) -> None:
    filtered = pytester.runpytest("-k", "test_lru_cache and witness")
    filtered.assert_outcomes(passed=3)
    assert filtered.ret == 0
    assert receipt(example)["status"] == "partial"
    required = pytester.runpytest("-k", "test_lru_cache and witness", "--quint-require-complete")
    assert required.ret == pytest.ExitCode.TESTS_FAILED
    assert_output(required, "INCOMPLETE test_lru_contract.py::test_lru_cache: 14 case(s)")


def test_required_contract_that_is_not_collected_is_reported(pytester: pytest.Pytester, example: Path) -> None:
    (example / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\nquint_required_contracts =\n    lru-cache\n    report-merge\n", encoding="utf-8"
    )
    result = pytester.runpytest("--quint-require-complete")
    result.assert_outcomes(passed=34)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert receipt(example)["incomplete_required"] == ["report-merge: required contract was not collected"]


def test_missing_quint_is_a_setup_error_that_replaces_a_stale_success(pytester: pytest.Pytester, example: Path) -> None:
    pytester.runpytest("-k", "test_lru_cache and witness").assert_outcomes(passed=3)
    stale = example / ".artifacts/quint-connect/results.json"
    stale.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    result = pytester.runpytest("--quint", str(example / "missing-quint"))
    result.assert_outcomes(errors=34)
    assert_output(result, "configured Quint executable not found")
    evidence = receipt(example)
    assert evidence["status"] == "failed"
    assert "QuintError" in evidence["contracts"][0]["generation_failure"]


def test_required_witness_that_quint_does_not_produce_is_an_error(pytester: pytest.Pytester, example: Path) -> None:
    edit_manifest(example, witnesses=["evictsLeastRecentlyUsedTest", "noSuchWitnessTest"])
    result = pytester.runpytest("-k", "test_lru_cache")
    result.assert_outcomes(errors=16)
    assert_output(result, "Quint generated 0 traces for required witness 'noSuchWitnessTest'")


def test_failing_witness_reports_quints_assertion_and_line(pytester: pytest.Pytester, example: Path) -> None:
    model = example / "lru.qnt"
    wrong = '{ key: "a", value: 1 }, { key: "c", value: 9 }'
    model.write_text(model.read_text().replace('{ key: "a", value: 1 }, { key: "c", value: 0 }', wrong, 1))
    result = pytester.runpytest("-k", "test_lru_cache and witness-evicts")
    result.assert_outcomes(errors=1)
    # Quint's own explanation, not only "evictsLeastRecentlyUsedTest failed after 1 test(s)".
    assert_output(result, "Error [QNT508]: Assertion failed", "lru.qnt:53:", wrong)
    failure = receipt(example)["contracts"][0]["generation_failure"]
    assert "Assertion failed" in failure[:400], "the report shows the first 400 characters"


def test_changed_historical_model_is_an_error(pytester: pytest.Pytester, example: Path) -> None:
    (example / "audit.qnt").write_bytes(b"// historical evidence\n")
    edit_manifest(example, historical="audit.qnt", historical_sha256="0" * 64)
    result = pytester.runpytest("-k", "test_lru_cache")
    result.assert_outcomes(errors=17)
    assert_output(result, "historical model changed")


def test_unreached_declared_action_fails_coverage(pytester: pytest.Pytester, example: Path) -> None:
    data = json.loads((example / "quint-connect.json").read_text())
    data["actions"]["evict"] = "never emitted by the model"
    (example / "quint-connect.json").write_text(json.dumps(data), encoding="utf-8")
    result = pytester.runpytest("-k", "test_lru_cache and coverage")
    result.assert_outcomes(failed=1)
    assert_output(result, "declared actions never reached by any generated history: ['evict']")


@pytest.mark.parametrize("found", [[], [STALE_READ_FOUND]], ids=["ordinary", "reintroduces-found-bug"])
def test_surviving_mutant_fails(pytester: pytest.Pytester, example: Path, found: list) -> None:
    edit_manifest(example, bugs_found=found)
    pytester.makeconftest(
        (EXAMPLE / "conftest.py").read_text()
        + "\n\n@pytest.fixture\ndef lru_driver():\n    return lambda context: CacheDriver(IMPLEMENTATIONS['list'])\n"
    )
    result = pytester.runpytest("-k", "test_lru_cache and mutant")
    result.assert_outcomes(failed=1)
    assert_output(result, "mutant 'stale-read' survived all 15 generated histories")
    assert receipt(example)["contracts"][0]["tests"][0]["findings"] == {"mutant-stale-read": {"survived": 15}}


def test_mutant_that_reintroduces_a_found_bug_replays_every_history(pytester: pytest.Pytester, example: Path) -> None:
    edit_manifest(example, bugs_found=[STALE_READ_FOUND])
    result = pytester.runpytest("-k", "test_lru_cache and mutant")
    result.assert_outcomes(passed=1)
    (contract,) = receipt(example)["contracts"]
    assert contract["bugs_found"] == [STALE_READ_FOUND]
    mutation = contract["tests"][0]["mutations"]["stale-read"]
    # Every history ran: the diverging ones are exactly the cases the buggy cache fails when it is the
    # implementation under test, and the first of them is where the mutant was caught.
    pytester.runpytest("-k", "test_lru_cache and not mutant and not coverage", "--lru-implementation", "stale-read")
    failing = [
        case
        for case, outcome in receipt(example)["contracts"][0]["tests"][0]["outcomes"].items()
        if outcome == "failed"
    ]
    assert mutation["histories"] == 15
    assert sorted(mutation["diverged"]) == sorted(failing) and len(failing) == 4
    assert mutation["detected_by"] == mutation["diverged"][0] == "witness-evictsLeastRecentlyUsedTest"
    assert (mutation["state"], mutation["action"]) == (3, "fetch")


@pytest.mark.parametrize("found", [[], [STALE_READ_FOUND]], ids=["ordinary", "reintroduces-found-bug"])
def test_mutant_setup_error_is_not_detection(pytester: pytest.Pytester, example: Path, found: list) -> None:
    edit_manifest(example, bugs_found=found)
    pytester.makeconftest(
        (EXAMPLE / "conftest.py").read_text()
        + "\n\n@pytest.fixture\ndef lru_driver():\n"
        + "    def start(context):\n"
        + "        if context.mutant:\n"
        + "            raise RuntimeError('deliberate mutation setup failure')\n"
        + "        return CacheDriver(IMPLEMENTATIONS['list'])\n"
        + "    return start\n"
    )
    result = pytester.runpytest("-k", "test_lru_cache and mutant")
    result.assert_outcomes(failed=1)
    assert_output(result, "deliberate mutation setup failure")
    assert receipt(example)["contracts"][0]["tests"][0]["mutations"] == {}
    # A crash is not a finding: the report says "see the job log" rather than inventing a cause.
    assert receipt(example)["contracts"][0]["tests"][0]["findings"] == {}


def test_a_driver_error_reports_where_it_happened_and_how_to_replay_it(
    pytester: pytest.Pytester, example: Path
) -> None:
    pytester.makeconftest(
        (EXAMPLE / "conftest.py").read_text()
        + "\n\nclass Crashing(CacheDriver):\n"
        + "    def fetch(self, key):\n"
        + "        raise RuntimeError('implementation crashed')\n"
        + "\n\n@pytest.fixture\ndef lru_driver():\n"
        + "    return lambda context: Crashing(IMPLEMENTATIONS['list'])\n"
    )
    result = pytester.runpytest("-k", "test_lru_cache and witness-evictsLeastRecentlyUsedTest")
    result.assert_outcomes(failed=1)
    assert_output(
        result,
        "RuntimeError: implementation crashed",
        "Quint contract 'lru-cache': the driver raised during this history",
        "witness-evictsLeastRecentlyUsedTest-0.itf.json",
        "state:  3, applying action fetch(key='a', value=0)",
        "seed:   7",
        "reproduce: pytest",
        "--quint-replay",
    )


def test_driver_fixture_teardown_runs_when_a_history_fails(
    pytester: pytest.Pytester, example: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = example / "teardowns.txt"
    monkeypatch.setenv("TEARDOWN_LOG", str(marker))
    pytester.makeconftest(
        (EXAMPLE / "conftest.py").read_text()
        + "\n\n@pytest.fixture\ndef lru_driver():\n"
        + "    import os\n"
        + "    yield lambda context: CacheDriver(IMPLEMENTATIONS['stale-read'])\n"
        + "    with open(os.environ['TEARDOWN_LOG'], 'a') as handle:\n"
        + "        handle.write('down\\n')\n"
    )
    result = pytester.runpytest("-k", "test_lru_cache and witness")
    result.assert_outcomes(failed=1, passed=2)
    assert marker.read_text().count("down") == 3


def test_require_complete_fails_a_filtered_run_without_any_required_list(
    pytester: pytest.Pytester, example: Path
) -> None:
    (example / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    result = pytester.runpytest("-k", "test_lru_cache and witness", "--quint-require-complete")
    result.assert_outcomes(passed=3)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert receipt(example)["status"] == "partial"


def test_contract_cases_carry_a_deselectable_marker(pytester: pytest.Pytester, example: Path) -> None:
    pytester.makepyfile(test_plain="def test_plain():\n    pass\n")
    result = pytester.runpytest("-m", "not quint_contract")
    result.assert_outcomes(passed=1)


def test_contract_tests_refuse_xdist_workers(
    pytester: pytest.Pytester, example: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert_output(result, "do not run under pytest-xdist")
