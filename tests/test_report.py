"""The PR comment and README block, rendered from real receipts.

The fixtures in data/receipts are results.json files the plugin wrote for the LRU example
(complete, a stale-read fault, a ``-k`` filtered run and a replay), with the checkout path
replaced by /repo (scripts/normalize_receipt.py). ``lru-bug-found.json`` is a complete run with
the example's manifest listing a found bug that ``stale-read`` reintroduces (``fixed_in:
example-only``): the example cache was never shipped, so only this test data, not the committed
manifest, may claim it.
The failed, partial and replay fixtures predate ``mutant_specs`` and stand for older receipts. The
passed and bug-found fixtures also record the manifest's actions and invariant and the first witness
step by step (``illustrated``), which the report shows as what each contract claims.
``session-passed.json`` is this repository's own dogfood contract (``session-receipts``), complete,
with its four planted bugs and the real bug it found: the receipt this repository's sticky reports.
"""

import hashlib
import json
from pathlib import Path

import pytest

from pytest_quint_connect.cli import main
from pytest_quint_connect.ledger import Ledger
from pytest_quint_connect.report import (
    COMMENT_BUDGET,
    END,
    PLANTED,
    PLANTED_TIP,
    SPREAD,
    START,
    Evidence,
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


def dogfood() -> Evidence:
    return load([RECEIPTS / "session-passed.json"])


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
    assert comment.startswith(
        "### ❌ Quint contracts: 1 contract · 1 test failed, 1 passed · 30/34 cases passed · 2/2 planted bugs caught\n"
    )
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


def tables(text: str) -> list[list[str]]:
    """Every markdown table in ``text``: runs of lines that start with a pipe."""
    found: list[list[str]] = []
    previous = False
    for line in text.splitlines():
        row = line.startswith("|")
        if row and not previous:
            found.append([])
        if row:
            found[-1].append(line)
        previous = row
    return found


def assert_tables_keep_their_columns(text: str) -> None:
    """Every row of every table has as many cells as its header: nothing a receipt says can split a cell."""
    for table in tables(text):
        widths = {line.count("|") - line.count("\\|") for line in table}
        assert len(widths) == 1, table


def test_receipt_text_cannot_break_the_comment(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    receipt["contracts"][0]["name"] = "evil`|\n</sub><img src=x>"
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([path]), repository=REPO)
    assert "`evil'\\| </sub><img src=x>`" in comment
    assert_tables_keep_their_columns(comment)


def test_comment_stays_within_githubs_limit(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    receipt["incomplete_required"] = ["x" * 400] * 20
    tests = receipt["contracts"][0]["tests"]
    receipt["contracts"][0]["tests"] = tests * 200
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([path]), repository=REPO)
    assert len(comment) <= COMMENT_BUDGET
    # The cut fell inside the contract table's block: it is closed, so the note after it is not hidden.
    assert "truncated" in comment and comment.count("<details") == comment.count("</details>")


def test_not_a_receipt_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text('{"tool": "something-else"}', encoding="utf-8")
    with pytest.raises(ReportError, match="not a pytest-quint-connect results.json"):
        load([path])


def test_readme_block_has_nothing_run_specific() -> None:
    block = render_readme(evidence("passed"))
    assert block == render_readme(evidence("passed"))
    assert "<!-- pytest-quint-connect:contracts lru-cache -->" in block
    assert "Quint_contracts-1_contract_passed_·_15_histories_·_2%2F2_planted_bugs_caught-brightgreen" in block.replace(
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
    assert "has no <!-- pytest-quint-connect:start --> line" in capsys.readouterr().out


@pytest.mark.parametrize(("name", "found"), [("README.rst", "has no"), ("READMEE.md", "does not exist")])
def test_cli_warns_in_github_actions_when_the_readme_cannot_hold_the_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, name: str, found: str
) -> None:
    # The trial agent's README.rst: markers there would render as text, and a wrong path did nothing silently.
    (tmp_path / "README.rst").write_text("Project\n=======\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    readme = tmp_path / name
    assert main(["report", str(RECEIPTS / "lru-passed.json"), "--readme", str(readme), "--check"]) == 0
    out = capsys.readouterr().out
    assert f"::warning file={readme},title=pytest-quint-connect README block::{readme} {found}" in out
    assert "contracts/README.md" in out


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


def test_comment_reproduce_command_names_an_explicit_config_file(tmp_path: Path) -> None:
    # A session run as `pytest -c contracts/quint.ini contracts`: rootdir is contracts/, and from there
    # pytest finds the node only when told the same configuration file again.
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    receipt["root"], receipt["config_file"] = "/repo/contracts", "quint.ini"
    for contract in receipt["contracts"]:
        for test in contract["tests"]:
            test["test"] = "lru/" + test["test"]
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([path]), repository=REPO)
    assert (
        "cd contracts\npytest -c quint.ini 'lru/test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]'"
        in comment
    )


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


def block(text: str, summary: str) -> tuple[bool, list[str]]:
    """Whether the ``<details>`` whose summary starts with ``summary`` starts open, and its table's rows."""
    start = text.index(f"<summary>{summary}")
    opening = text[text.rindex("<details", 0, start) : start]
    body = text[start : text.index("</details>", start)]
    rows = [line for line in body.splitlines() if line.startswith("| ")][2:]
    return "open" in opening, rows


def headline(text: str) -> list[str]:
    """The heading line and the lines under it, up to the first blank line."""
    return text.split("\n\n", 1)[0].splitlines()


TERM = f'[Planted bug]({PLANTED} "{PLANTED_TIP}")'
CAUGHT = "`witness-evictsLeastRecentlyUsedTest` | state 3, after `fetch(key='a', value=0)`"
STALE_READ = "get() returns the value but does not refresh recency, so a just-read entry can be evicted next"


def test_this_repositorys_contract_reports_green_numbers_and_collapses_its_details() -> None:
    comment = render_comment(dogfood(), repository=REPO)
    # What matters now leads; the bug found earlier is credited apart, as history, not as a current failure.
    assert headline(comment) == [
        "### ✅ Quint contracts: 1 contract · all 13 cases passed · 4/4 planted bugs caught",
        "<sub>🏅 Earlier, this contract found 1 real bug, since fixed in #3.</sub>",
    ]
    expanded, rows = block(comment, "✅ 1 contract · 13/13 cases · 4/4 planted bugs caught · 8 histories")
    assert not expanded and len(rows) == 1 and rows[0].startswith("| ✅ | `session-receipts`")
    expanded, rows = block(comment, "🐞 1 real bug found by this contract")
    assert not expanded and rows == [
        "| `session-receipts` | Valid histories whose step calls one action without any { … } were rejected: Quint "
        "records mbt::actionTaken as step, and the event cross-check refused it<br><sub>found by the first run of "
        "this contract · no planted bug re-checks it</sub> | #3 | "
        "<sub>`tests/test_replay.py`<br>`::test_unbranched_step_is_recorded_under_the_step_name_and_still_replays`</sub> |"
    ]
    expanded, rows = block(comment, "🧪 4 planted bugs, all caught")
    assert not expanded and rows[0] == (
        "| `session-receipts` | `filtered-counts-as-complete` | a -k run that skipped cases is judged as if every "
        "case ran | `witness-filteredRunIsNeverPassedTest` | state 1, after `session(kind='filtered', strict=False)` |"
    )
    assert [row.split(" | ")[1] for row in rows] == [
        "`filtered-counts-as-complete`",
        "`replay-counts-as-complete`",
        "`failed-session-keeps-old-receipt`",
        "`require-complete-ignored`",
    ]
    # The README block is the same rendering.
    readme = render_readme(dogfood())
    assert "<!-- pytest-quint-connect:contracts session-receipts -->" in readme
    for summary in ("✅ 1 contract", "🐞 1 real bug", "🧪 4 planted bugs"):
        assert block(readme, summary) == block(comment, summary)


def test_every_table_spans_the_comment_through_its_widest_column() -> None:
    comment = render_comment(evidence("bug-found"), repository=REPO)
    headers = [table[0] for table in tables(comment)]
    assert headers[:3] == [
        f'| | {SPREAD}Contract | Cases passed | [Planted bugs caught]({PLANTED} "{PLANTED_TIP}") |',
        f"| Contract | {SPREAD}Bug | Fixed in | Regression test |",
        f"| Contract | {TERM} | {SPREAD}What it breaks | Caught by | At |",
    ]
    # One spacer per table, floated right, in the column whose text wraps: the numbers stay narrow, and on a
    # narrow screen the spacer shrinks with the comment instead of forcing a scrollbar.
    assert all(header.count(SPREAD) == 1 for header in headers[:3])
    assert 'align="right"' in SPREAD and 'height="1"' in SPREAD
    # Inline code never wraps, so a whole node id would set its column's minimum width past the comment's
    # edge: the regression test's file and test sit on separate lines.
    assert "| <sub>`test_lru_contract.py`<br>`::test_lru_cache[witness-evictsLeastRecentlyUsedTest]`</sub> |" in comment


def test_a_red_run_keeps_its_table_open_with_failing_rows_first(tmp_path: Path) -> None:
    def fail_the_refactor(contract: dict) -> None:
        # The refactor sorts after test_lru_cache: only failing-first ordering puts it on top.
        contract["tests"][1]["outcomes"]["sample-3"] = "failed"

    comment = render_comment(edited_passed_receipt(tmp_path, fail_the_refactor), repository=REPO)
    assert headline(comment) == [
        "### ❌ Quint contracts: 1 contract · 1 test failed, 1 passed · 33/34 cases passed · 2/2 planted bugs caught"
    ]
    expanded, rows = block(comment, "❌ 1 contract · 33/34 cases · 2/2 planted bugs caught · 15 histories")
    assert expanded
    assert [row.split(" | ")[:2] for row in rows] == [
        [
            "| ❌",
            "`lru-cache` `test_lru_contract.py::test_ordered_refactor`<br><sub>15 histories (3 witnesses, 12 "
            "samples, seed 7) · every action covered · Quint 0.33.1</sub>",
        ],
        [
            "| ✅",
            "`lru-cache` `test_lru_contract.py::test_lru_cache`<br><sub>15 histories (3 witnesses, 12 samples, "
            "seed 7) · every action covered · Quint 0.33.1</sub>",
        ],
    ]
    assert_tables_keep_their_columns(comment)


def test_an_open_disagreement_keeps_the_table_open() -> None:
    ledger = Ledger(caught={"k": {"found": 1, "kind": "mismatch", "state": "open"}})
    expanded, _ = block(render_comment(evidence("passed"), repository=REPO, ledger=ledger), "✅ 1 contract")
    assert expanded
    expanded, _ = block(render_comment(evidence("passed"), repository=REPO, ledger=Ledger()), "✅ 1 contract")
    assert not expanded


def test_planted_bugs_are_explained_on_hover_wherever_they_are_counted() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    assert f'| [Planted bugs caught]({PLANTED} "{PLANTED_TIP}") |\n' in comment
    assert f"| Contract | {TERM} |" in comment
    block_ = render_readme(evidence("passed"))
    assert f'<a href="https://github.com/gigaverse-app/pytest-quint-connect" title="{PLANTED_TIP}">' in block_
    assert "mutant" not in comment.lower().replace(PLANTED_TIP.lower(), "")


def test_comment_says_what_each_planted_bug_breaks_and_where_it_was_caught() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    assert headline(comment) == ["### ✅ Quint contracts: 1 contract · all 34 cases passed · 2/2 planted bugs caught"]
    # Both test functions caught it in the same place, so they share one row.
    assert block(comment, "🧪 1 planted bug, caught") == (
        False,
        [f"| `lru-cache` | `stale-read` | {STALE_READ} | {CAUGHT} |"],
    )
    assert comment.index("| ✅ | `lru-cache`") < comment.index("<summary>🧪")
    assert block(render_readme(evidence("passed")), "🧪") == block(comment, "🧪")
    assert "🐞" not in comment and "Earlier" not in comment


def test_found_bugs_are_credited_apart_and_say_how_many_histories_their_return_breaks() -> None:
    comment = render_comment(evidence("bug-found"), repository=REPO)
    assert headline(comment) == [
        "### ✅ Quint contracts: 1 contract · all 34 cases passed · 2/2 planted bugs caught",
        "<sub>🏅 Earlier, this contract found 1 real bug, since fixed in example-only.</sub>",
    ]
    expanded, (row,) = block(comment, "🐞 1 real bug found by this contract")
    assert not expanded and row == (
        "| `lru-cache` | reads did not refresh recency, so a just-read entry could be evicted next (test data: this "
        "cache was never shipped)<br><sub>found by witness evictsLeastRecentlyUsedTest · planted bug `stale-read` "
        "reintroduces it: 4 of 15 histories diverge</sub> | example-only | "
        "<sub>`test_lru_contract.py`<br>`::test_lru_cache[witness-evictsLeastRecentlyUsedTest]`</sub> |"
    )
    # The planted bug that reintroduces it is listed with the others, and says so.
    assert block(comment, "🧪 1 planted bug, caught")[1] == [
        f"| `lru-cache` | `stale-read` | {STALE_READ}<br><sub>reintroduces a real bug found earlier</sub> | {CAUGHT} |"
    ]
    assert row in render_readme(evidence("bug-found"))
    assert comment.index("| ✅ | `lru-cache`") < comment.index("<summary>🐞") < comment.index("<summary>🧪")


def test_receipts_without_mutant_specs_still_say_where_each_planted_bug_was_caught() -> None:
    # lru-failed.json was written before receipts carried descriptions or found bugs.
    assert block(render_comment(evidence("failed"), repository=REPO), "🧪") == (
        False,
        [f"| `lru-cache` | `stale-read` | — | {CAUGHT} |"],
    )
    assert block(render_comment(evidence("partial"), repository=REPO), "🧪 1 planted bug, 1 not caught") == (
        True,
        ["| `lru-cache` | `stale-read` | — | ➖ **not run** | not run in this session |"],
    )


def edited_bug_receipt(tmp_path: Path, edit) -> Evidence:
    receipt = json.loads((RECEIPTS / "lru-bug-found.json").read_text(encoding="utf-8"))
    edit(receipt["contracts"][0])
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return load([path])


def test_a_found_bug_claims_no_more_than_its_planted_bug_shows(tmp_path: Path) -> None:
    def survive_in_refactor(contract: dict) -> None:
        refactor = contract["tests"][1]
        refactor["mutations"] = {}
        refactor["outcomes"]["mutant-stale-read"] = "failed"
        refactor["findings"] = {"mutant-stale-read": {"survived": 15}}

    comment = render_comment(edited_bug_receipt(tmp_path, survive_in_refactor), repository=REPO)
    (row,) = block(comment, "🐞")[1]
    assert (
        "planted bug `stale-read` reintroduces it: 4 of 15 histories diverge in `test_lru_contract.py::test_lru_cache` · "
        "planted bug `stale-read` reintroduces it: **survived all 15 histories** in "
        "`test_lru_contract.py::test_ordered_refactor`</sub>"
    ) in row
    # The test that missed it comes first, and keeps the block open.
    expanded, rows = block(comment, "🧪 1 planted bug, 1 not caught")
    assert expanded and [cells.split(" | ")[-2:] for cells in rows] == [
        ["❌ **not caught**", "survived all 15 histories |"],
        ["`witness-evictsLeastRecentlyUsedTest`", "state 3, after `fetch(key='a', value=0)` |"],
    ]
    assert rows[0].startswith("| `lru-cache`<br><sub>`test_lru_contract.py::test_ordered_refactor`</sub> |")

    def without_mutant(contract: dict) -> None:
        del contract["bugs_found"][0]["mutant"]

    comment = render_comment(edited_bug_receipt(tmp_path, without_mutant), repository=REPO)
    (row,) = block(comment, "🐞")[1]
    assert "no planted bug re-checks it</sub>" in row and "histories diverge" not in row
    assert block(comment, "🧪")[1] == [f"| `lru-cache` | `stale-read` | {STALE_READ} | {CAUGHT} |"]


def test_manifest_text_cannot_inject_markup_mentions_or_cells(tmp_path: Path) -> None:
    def inject(contract: dict) -> None:
        contract["mutant_specs"][0]["description"] = "<img src=x> pings\n@octocat | extra cell"
        contract["bugs_found"][0]["fixed_in"] = "see [this](javascript:alert(1))"
        contract["bugs_found"][0]["summary"] = "**bold** <script> | split"
        contract["bugs_found"][0]["regression_test"] = "a|b"

    comment = render_comment(edited_bug_receipt(tmp_path, inject), repository=REPO)
    (row,) = block(comment, "🐞")[1]
    assert "&lt;script&gt; \\| split" in row and "| `see [this](javascript:alert(1))` |" in row and "`a\\|b`" in row
    assert "&lt;img src=x&gt; pings &#64;octocat \\| extra cell" in block(comment, "🧪")[1][0]
    assert_tables_keep_their_columns(comment)

    def link_with_a_pipe(contract: dict) -> None:
        contract["bugs_found"][0]["fixed_in"] = "https://example.com/a|b"

    comment = render_comment(edited_bug_receipt(tmp_path, link_with_a_pipe), repository=REPO)
    assert "| `https://example.com/a\\|b` |" in block(comment, "🐞")[1][0]
    assert_tables_keep_their_columns(comment)


def test_comment_warns_when_only_witnesses_catch_the_mutants(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-passed.json").read_text(encoding="utf-8"))
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(receipt), encoding="utf-8")
    # Receipts from before caught_by_sample existed say nothing either way.
    assert "caught only by a named witness" not in render_comment(load([plain]), repository=REPO)
    for test in receipt["contracts"][0]["tests"]:
        for mutation in test["mutations"].values():
            mutation["caught_by_sample"] = False
    weak = tmp_path / "results.json"
    weak.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([weak]), repository=REPO)
    assert "`test_lru_contract.py::test_lru_cache`: every mutant was caught only by a named witness" in comment
    assert "Raise bounds.n_traces or max_steps" in comment


CLAIMS_OPEN = "<details>\n<summary>What <code>lru-cache</code> says</summary>"


def claims_block(text: str) -> str:
    start = text.index(CLAIMS_OPEN)
    return text[start : text.index("\n</details>\n", start)]


def test_each_contract_says_what_it_claims_so_a_reviewer_can_judge_the_model() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    # One collapsed block per contract, although two test functions check it.
    assert comment.count(CLAIMS_OPEN) == 1
    block = claims_block(comment)
    assert (
        "- `init`: construct a fresh cache with capacity 2\n- `store`: cache.put(key, value)\n"
        "- `fetch`: cache.get(key), observed as Hit(value) or Miss\n- `inspect`: read cache.items() without changing it\n"
    ) in block
    assert "**Invariant** `withinCapacity`, checked while sampling histories." in block
    assert (
        "**Witnesses**, scenarios replayed exactly as written: `evictsLeastRecentlyUsedTest`, "
        "`overwriteRefreshesTest`, `missKeepsContentsTest`."
    ) in block
    assert "**Bounds**: seed `7` · max_samples `200` · max_steps `8` · n_traces `12`." in block
    # The first witness, read from its trace: each action and the observation the model expects after it.
    assert "**Witness `evictsLeastRecentlyUsedTest`, step by step**" in block
    rows = [line for line in block.splitlines() if line.startswith("| `")]
    assert rows[0] == "| `0` | `init(key='', value=0)` | `{ entries: [], lastRead: NoRead }` |"
    assert rows[3] == (
        "| `3` | `fetch(key='a', value=0)` | "
        '`{ entries: [{ key: "b", value: 2 }, { key: "a", value: 1 }], lastRead: Hit(1) }` |'
    )
    assert len(rows) == 6
    # Verdicts come first; the claims follow them.
    assert comment.index("<summary>🧪") < comment.index(CLAIMS_OPEN)
    assert claims_block(render_readme(evidence("passed"))) == block


def test_receipts_without_claims_render_no_claims() -> None:
    # lru-failed.json was written before receipts recorded the manifest's actions and invariant.
    assert "says</summary>" not in render_comment(evidence("failed"), repository=REPO)
    assert "says</summary>" not in render_readme(evidence("partial"))


def edited_passed_receipt(tmp_path: Path, edit) -> Evidence:
    receipt = json.loads((RECEIPTS / "lru-passed.json").read_text(encoding="utf-8"))
    edit(receipt["contracts"][0])
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return load([path])


def test_claims_from_the_manifest_cannot_inject_markup_or_break_the_table(tmp_path: Path) -> None:
    def inject(contract: dict) -> None:
        contract["actions"]["store"] = "<img src=x> pings @octocat"
        contract["invariant"] = "ok`|</details>"
        contract["illustrated"]["steps"][1]["observed"] = {"note": "a|b`</details>"}

    comment = render_comment(edited_passed_receipt(tmp_path, inject), repository=REPO)
    block = claims_block(comment)
    assert "- `store`: &lt;img src=x&gt; pings &#64;octocat" in block
    assert r"**Invariant** `ok'\|</details>`" in block
    assert r"""| `1` | `store(key='a', value=1)` | `{ note: "a\|b'</details>" }` |""" in block
    assert all(line.count("|") - line.count(r"\|") == 4 for line in block.splitlines() if line.startswith("| "))


def test_long_witnesses_are_cut_and_claims_give_way_to_the_budget(tmp_path: Path) -> None:
    def lengthen(contract: dict) -> None:
        steps = contract["illustrated"]["steps"]
        contract["illustrated"]["steps"] = [{**steps[1], "state": index} for index in range(25)]

    block = claims_block(render_comment(edited_passed_receipt(tmp_path, lengthen), repository=REPO))
    assert sum(line.startswith("| `") for line in block.splitlines()) == 20
    assert "…and 5 more states in the receipt." in block

    def overflow(contract: dict) -> None:
        contract["actions"] = {f"action{index}": "x" * 250 for index in range(300)}

    comment = render_comment(edited_passed_receipt(tmp_path, overflow), repository=REPO)
    assert len(comment) <= COMMENT_BUDGET
    # Leaving the claims out keeps every verdict and the footer, rather than truncating inside a collapsed block.
    assert "says</summary>" not in comment and "truncated" not in comment
    assert comment.rstrip().endswith("pytest-quint-connect</a></sub>")
