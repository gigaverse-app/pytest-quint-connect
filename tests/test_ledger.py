"""The pull request ledger: disagreements caught, fixed and reopened across pushes."""

import json
from pathlib import Path

import pytest

from pytest_quint_connect.cli import main
from pytest_quint_connect.ledger import MAX_CAUGHT, Ledger, advance, parse
from pytest_quint_connect.report import load, render_comment

RECEIPTS = Path(__file__).parent / "data" / "receipts"


def evidence(name: str):
    return load([RECEIPTS / f"lru-{name}.json"])


def test_a_disagreement_is_caught_once_and_fixed_by_a_later_push() -> None:
    first = advance(Ledger(), evidence("failed"), head="a")
    assert (len(first.caught), first.fixed, first.open) == (4, 0, 4)
    assert {entry["found"] for entry in first.caught.values()} == {1}
    second = advance(parse(first.comment()), evidence("passed"), head="b")
    assert (len(second.caught), second.fixed, second.open) == (4, 4, 0)
    assert [push["push"] for push in second.pushes] == [1, 2]


def test_a_failure_after_a_fix_reopens_without_counting_twice() -> None:
    ledger = advance(
        advance(advance(Ledger(), evidence("failed"), head="a"), evidence("passed"), head="b"),
        evidence("failed"),
        head="c",
    )
    assert (len(ledger.caught), ledger.open) == (4, 4)
    assert {entry["found"] for entry in ledger.caught.values()} == {1}


def test_a_rerun_of_the_same_head_replaces_its_push() -> None:
    ledger = advance(advance(Ledger(), evidence("failed"), head="a"), evidence("failed"), head="a")
    assert [push["push"] for push in ledger.pushes] == [1]


def test_cases_that_did_not_run_neither_fix_nor_reopen() -> None:
    # -k "test_lru_cache and witness": the samples that failed before did not run, so they stay open.
    ledger = advance(advance(Ledger(), evidence("failed"), head="a"), evidence("partial"), head="b")
    assert ledger.fixed == 1 and ledger.open == 3


def test_crashes_are_not_disagreements(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    for test in receipt["contracts"][0]["tests"]:
        test["findings"] = {}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert advance(Ledger(), load([path]), head="a").caught == {}


@pytest.mark.parametrize("text", ["", "not json", '{"schema": 99, "caught": {}}', "<!-- other -->"])
def test_a_missing_or_foreign_ledger_starts_afresh(text: str) -> None:
    assert parse(text) == Ledger()


def test_the_hidden_json_cannot_close_its_comment(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-failed.json").read_text(encoding="utf-8"))
    receipt["contracts"][0]["name"] = "x --> <img src=x>"
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    hidden = advance(Ledger(), load([path]), head="a").comment()
    assert hidden.count("-->") == 1 and "<img" not in hidden
    assert len(parse(hidden).caught) == 4


def test_the_ledger_stays_bounded() -> None:
    ledger = Ledger(caught={f"k{i}": {"found": 1, "kind": "mismatch", "state": "fixed"} for i in range(500)})
    assert len(advance(ledger, evidence("passed"), head="a").caught) == MAX_CAUGHT


def test_the_comment_spans_the_page_and_carries_the_ledger() -> None:
    ledger = advance(Ledger(), evidence("failed"), head="a")
    comment = render_comment(
        evidence("failed"), repository=Path("/repo"), assets="https://x.test/assets", ledger=ledger
    )
    assert comment.startswith('<picture>\n  <source media="(prefers-color-scheme: dark)"')
    assert 'src="https://x.test/assets/sticky-banner-failed-light.svg" width="100%"' in comment
    assert 'srcset="https://x.test/assets/sticky-banner-failed-dark.svg"' in comment
    # The header counts this pull request's disagreements, and spans the comment like every table.
    names = next(line for line in comment.splitlines() if "**disagreements in this PR**" in line)
    assert names.count('<img width="2000" height="1" align="right" alt="">') == 5
    assert 'alt="4 disagreements in this PR"' in comment
    assert "across this pull request's 1 push: 0 fixed, 4 still failing" in names
    assert parse(comment).caught == ledger.caught
    plain = render_comment(evidence("failed"), repository=Path("/repo"))
    assert "<picture>" not in plain and "pytest-quint-connect-ledger" not in plain


def test_cli_extends_the_previous_comment(tmp_path: Path) -> None:
    previous, comment = tmp_path / "previous.md", tmp_path / "comment.md"
    previous.write_text("", encoding="utf-8")
    failed, passed = str(RECEIPTS / "lru-failed.json"), str(RECEIPTS / "lru-passed.json")
    assert main(["report", failed, "--ledger", str(previous), "--head", "a", "--comment", str(comment)]) == 0
    previous.write_text(comment.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["report", passed, "--ledger", str(previous), "--head", "b", "--comment", str(comment)]) == 0
    assert parse(comment.read_text(encoding="utf-8")).fixed == 4
    assert main(["report", passed, "--ledger", str(tmp_path / "missing.md"), "--comment", str(comment)]) == 0


@pytest.mark.parametrize(
    "receipt, variant", [("passed", "-passed"), ("failed", "-failed"), ("partial", ""), ("replay", "")]
)
def test_quinty_matches_the_outcome(receipt: str, variant: str) -> None:
    comment = render_comment(evidence(receipt), repository=Path("/repo"), assets="https://x.test/assets")
    assert f'src="https://x.test/assets/sticky-banner{variant}-light.svg"' in comment


def test_every_banner_the_comment_can_name_exists() -> None:
    assets = Path(__file__).resolve().parents[1] / "docs" / "assets"
    for variant in ("", "-passed", "-failed"):
        for theme in ("light", "dark"):
            assert (assets / f"sticky-banner{variant}-{theme}.svg").is_file()


def test_the_comment_names_each_history_this_pull_request_fixed() -> None:
    ledger = advance(advance(Ledger(), evidence("failed"), head="a"), evidence("passed"), head="b")
    assert {entry["fixed"] for entry in ledger.caught.values()} == {2}
    comment = render_comment(evidence("passed"), repository=Path("/repo"), ledger=ledger)
    assert "**Fixed in this PR** (4 histories):" in comment
    assert (
        "- ✅ `lru-cache` `witness-evictsLeastRecentlyUsedTest` in `test_lru_contract.py::test_lru_cache`: "
        "the code left the model; caught in push 1, fixed in push 2." in comment
    )
    # A history that fails again is open again, and forgets when it was fixed.
    reopened = advance(ledger, evidence("failed"), head="c")
    assert not any("fixed" in entry for entry in reopened.caught.values())
    assert "**Fixed in this PR**" not in render_comment(evidence("failed"), repository=Path("/repo"), ledger=reopened)


def test_the_header_counts_disagreements_across_every_push() -> None:
    quiet = advance(Ledger(), evidence("passed"), head="a")
    first = render_comment(evidence("passed"), repository=Path("/repo"), ledger=quiet)
    assert "across this pull request's 1 push: 0 fixed, 0 still failing" in first
    later = advance(advance(quiet, evidence("failed"), head="b"), evidence("passed"), head="c")
    third = render_comment(evidence("passed"), repository=Path("/repo"), ledger=later)
    assert "across this pull request's 3 pushes: 4 fixed, 0 still failing" in third
    assert "**Fixed in this PR** (4 histories):" in third
