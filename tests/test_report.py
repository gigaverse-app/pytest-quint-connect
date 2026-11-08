"""The PR comment and README block, rendered from real receipts.

The fixtures in data/receipts are results.json files the plugin wrote for the LRU example
(complete, a stale-read fault, a ``-k`` filtered run and a replay), with the checkout path
replaced by /repo (scripts/normalize_receipt.py).
"""

import hashlib
import json
from pathlib import Path

import pytest

from pytest_quint_connect.cli import main
from pytest_quint_connect.report import (
    COMMENT_BUDGET,
    END,
    START,
    ReportError,
    check_readme,
    embed,
    load,
    receipt_paths,
    render_comment,
    render_readme,
)

RECEIPTS = Path(__file__).parent / "data" / "receipts"
REPO = Path("/repo")


def evidence(*names: str):
    return load([RECEIPTS / f"lru-{name}.json" for name in names])


def readme_with_block(tmp_path: Path) -> Path:
    path = tmp_path / "README.md"
    path.write_text(f"# Project\n\nIntro.\n\n{START}\n{END}\n\n## After\n", encoding="utf-8")
    return path


def test_rows_keep_every_status_distinct() -> None:
    statuses = {name: sorted(row.status for row in evidence(name).rows) for name in ("passed", "failed", "partial")}
    assert statuses == {
        "passed": ["passed", "passed"],
        "failed": ["failed", "passed"],
        # -k "test_lru_cache and witness": the refactor's test was collected, but none of its cases ran.
        "partial": ["not run", "partial"],
    }
    assert sorted(row.status for row in evidence("replay").rows) == ["not run", "replay-only"]
    assert evidence("passed").complete
    assert not evidence("partial").complete
    assert evidence("failed", "passed").status == "failed"


def test_rows_count_histories_cases_and_mutants() -> None:
    row = next(row for row in evidence("passed").rows if row.test.endswith("test_lru_cache"))
    assert (row.witnesses, row.samples, row.seed) == (3, 12, 7)
    assert (row.passed, row.cases, row.mutants_detected, row.mutants) == (17, 17, 1, 1)
    assert row.covered is True and row.quint == "0.33.1"


def test_comment_explains_each_failure_with_a_reproduce_command() -> None:
    comment = render_comment(evidence("failed"), repository=REPO, head="abc1234", run_url="https://run.example")
    assert comment.startswith("### ❌ Quint contracts: 1 contract · 1 test failed, 1 passed\n")
    assert "observed state differs from the model at state 3, after `fetch(key='a', value=0)`" in comment
    assert "`entries[0].key: values differ; expected: 'b'; actual: 'a'`" in comment
    assert "and 1 more difference" in comment
    assert (
        "cd examples/lru_cache\npytest 'test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]'"
        in (comment)
    )
    assert "mismatch-test_lru_cache-witness-evictsLeastRecentlyUsedTest.json" in comment
    assert "[run](https://run.example)" in comment and "`abc1234`" in comment


def test_comment_says_filtered_and_replayed_runs_are_not_complete() -> None:
    partial = render_comment(evidence("partial"), repository=REPO)
    assert partial.startswith("### 🟡 Quint contracts: 1 contract · 1 test partial, 1 not run")
    assert "not complete evidence" in partial and "#### Not complete" in partial
    assert "none of its cases were selected" in partial
    replay = render_comment(evidence("replay"), repository=REPO)
    assert replay.startswith("### ➖ Quint contracts: 1 contract · 1 test not run, 1 replay-only")
    assert "never complete coverage" in replay


def test_comment_without_receipts_says_nothing_ran() -> None:
    assert render_comment(load([]), repository=REPO).startswith("### ➖ Quint contracts: no contract ran in this run")


def test_receipt_text_cannot_break_the_comment(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    receipt["contracts"][0]["name"] = "evil`|\n</sub><img src=x>"
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([path]), repository=REPO)
    assert "`evil'\\| </sub><img src=x>`" in comment
    assert all(line.count("|") - line.count("\\|") in (0, 8) for line in comment.splitlines() if line.startswith("|"))


def test_comment_stays_within_githubs_limit(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    receipt["incomplete_required"] = ["x" * 400] * 20
    tests = receipt["contracts"][0]["tests"]
    receipt["contracts"][0]["tests"] = tests * 200
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert len(render_comment(load([path]), repository=REPO)) <= COMMENT_BUDGET


def test_not_a_receipt_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text('{"tool": "something-else"}', encoding="utf-8")
    with pytest.raises(ReportError, match="not a pytest-quint-connect results.json"):
        load([path])


def test_readme_block_has_nothing_run_specific() -> None:
    block = render_readme(evidence("passed"))
    assert block == render_readme(evidence("passed"))
    assert "<!-- pytest-quint-connect:contracts lru-cache -->" in block
    assert "Quint_contracts-1_contract_passed_·_15_histories_·_2%2F2_mutants_caught-brightgreen" in block.replace(
        "%C2%B7", "·"
    )
    assert "0123456789abcdef" not in block and "/repo" not in block


def test_embed_touches_only_the_block() -> None:
    document = f"before\n{START}\nold\n{END}\nafter\n"
    assert embed(document, "new") == f"before\n{START}\nnew\n{END}\nafter\n"
    assert embed(embed(document, "new"), "new") == embed(document, "new")
    with pytest.raises(ReportError, match="once"):
        embed(document + START, "new")


def test_readme_check_needs_complete_evidence(tmp_path: Path) -> None:
    current = embed(readme_with_block(tmp_path).read_text(encoding="utf-8"), render_readme(evidence("passed")))
    assert check_readme(current, evidence("passed")).state == "current"
    assert check_readme(current.replace("17/17", "16/17"), evidence("passed")).state == "stale"
    filtered = check_readme(current.replace("17/17", "16/17"), evidence("partial"))
    assert filtered.state == "unverifiable" and "not every contract" in filtered.reason
    assert check_readme(current, load([])).state == "unverifiable"


def test_cli_writes_then_checks_the_readme(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readme = readme_with_block(tmp_path)
    receipt = str(RECEIPTS / "lru-passed.json")
    assert main(["report", receipt, "--readme", str(readme)]) == 0
    assert "| ✅ | `lru-cache`" in readme.read_text(encoding="utf-8")
    assert readme.read_text(encoding="utf-8").endswith(f"{END}\n\n## After\n")
    assert main(["report", receipt, "--readme", str(readme), "--check"]) == 0
    readme.write_text(readme.read_text(encoding="utf-8").replace("17/17", "16/17"), encoding="utf-8")
    capsys.readouterr()
    comment = tmp_path / "comment.md"
    assert main(["report", receipt, "--readme", str(readme), "--check", "--comment", str(comment)]) == 1
    captured = capsys.readouterr()
    assert "-| ✅ | `lru-cache`" in captured.out
    assert f"pytest-quint-connect report {receipt} --readme {readme}" in captured.err.replace("'", "")
    assert "The contract block in" in comment.read_text(encoding="utf-8")


def test_cli_without_markers_leaves_the_document_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# No block\n", encoding="utf-8")
    assert main(["report", str(RECEIPTS / "lru-passed.json"), "--readme", str(readme), "--check"]) == 0
    assert readme.read_text(encoding="utf-8") == "# No block\n"
    assert "nothing to keep current" in capsys.readouterr().out


def test_cli_collects_receipts_by_glob_and_keeps_their_directories(tmp_path: Path) -> None:
    session = tmp_path / "project" / ".artifacts" / "quint-connect"
    (session / "lru-cache").mkdir(parents=True)
    (session / "results.json").write_bytes((RECEIPTS / "lru-failed.json").read_bytes())
    (session / "lru-cache" / "mismatch-x.json").write_text("{}", encoding="utf-8")
    pattern = f"{tmp_path.as_posix()}/**/.artifacts/quint-connect/results.json"
    assert receipt_paths([pattern]) == [session / "results.json"]
    artifact, status, comment = tmp_path / "artifact", tmp_path / "status", tmp_path / "comment.md"
    assert (
        main(
            [
                "report",
                pattern,
                "--comment",
                str(comment),
                "--status-file",
                str(status),
                "--artifact-dir",
                str(artifact),
            ]
        )
        == 0
    )
    assert status.read_text(encoding="utf-8") == "failed"
    assert len(list(artifact.glob("*/lru-cache/mismatch-x.json"))) == 1
    assert receipt_paths([f"{tmp_path.as_posix()}/nothing/**/results.json"]) == []


def test_the_counter_sign_is_keyed_by_the_repository_id_and_survives_a_local_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytest_quint_connect.report import Sign

    sign = Sign.of("1449617982", "gigaverse-app/pytest-quint-connect")
    assert sign.key == hashlib.sha256(b"github-repository:1449617982").hexdigest()
    block = render_readme(evidence("passed"), sign)
    assert f"/v1/sign.svg?key={sign.key}&amp;name=gigaverse-app%2Fpytest-quint-connect&amp;theme=dark" in block
    assert Sign.found(block) == sign

    readme = readme_with_block(tmp_path)
    receipt = str(RECEIPTS / "lru-passed.json")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1449617982")
    monkeypatch.setenv("GITHUB_REPOSITORY", "gigaverse-app/pytest-quint-connect")
    assert main(["report", receipt, "--readme", str(readme), "--counter-sign"]) == 0
    assert sign.key in readme.read_text(encoding="utf-8")
    # A developer's machine has no repository id: the block keeps the sign it shows, and stays current.
    monkeypatch.delenv("GITHUB_REPOSITORY_ID")
    monkeypatch.delenv("GITHUB_REPOSITORY")
    assert main(["report", receipt, "--readme", str(readme), "--check"]) == 0
    assert main(["report", receipt, "--readme", str(readme)]) == 0
    assert sign.key in readme.read_text(encoding="utf-8")


def test_no_sign_without_the_flag_or_an_existing_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/shop")
    readme = readme_with_block(tmp_path)
    assert main(["report", str(RECEIPTS / "lru-passed.json"), "--readme", str(readme)]) == 0
    assert "/v1/sign.svg" not in readme.read_text(encoding="utf-8")
