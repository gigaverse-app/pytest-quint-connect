"""The comment's header: each key number once, colored digits over its name and what it counts."""

import re
from pathlib import Path

from pytest_quint_connect.ledger import Ledger
from pytest_quint_connect.report import SCOREBOARD, _scoreboard, _scores, load, render_comment

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = Path(__file__).parent / "data" / "receipts"
ASSETS = ROOT / "docs" / "assets"
BASE = "https://x.test/assets/score/"


def evidence(name: str):
    return load([RECEIPTS / f"lru-{name}.json"])


def numbers(name: str, **kwargs: object) -> dict[str, tuple[str, str]]:
    return {score.label: (score.text, score.color) for score in _scores(evidence(name), **kwargs)}


def test_colors_follow_the_rule_red_failure_amber_unrun_green_passed() -> None:
    assert numbers("passed") == {
        "contracts": ("1", "neutral"),
        "cases passed": ("34/34", "good"),
        "mutants caught": ("2/2", "good"),
        "real bugs found": ("0", "neutral"),
        "disagreements in this PR": ("0", "neutral"),
    }
    assert numbers("failed")["cases passed"] == ("30/34", "bad")
    assert numbers("partial")["cases passed"][1] == "warning"
    assert numbers("bug-found")["real bugs found"] == ("1", "good")


def test_disagreements_come_from_the_pull_requests_ledger() -> None:
    caught = {"a": {"state": "fixed", "found": 1, "fixed": 2}, "b": {"state": "open", "found": 2}}
    ledger = Ledger(pushes=[{}, {}], caught=caught)
    score = next(s for s in _scores(evidence("passed"), ledger) if s.label == "disagreements in this PR")
    assert (score.text, score.color) == ("2", "bad")
    assert score.meaning.endswith("across this pull request's 2 pushes: 1 fixed, 1 still failing")


def test_a_bug_fixed_by_this_pull_request_says_so_and_names_no_other() -> None:
    (bug,) = evidence("bug-found").rows[0].bugs_found
    pull = str(bug["fixed_in"]).rstrip("/").rsplit("/", 1)[-1].lstrip("#")
    here = next(s for s in _scores(evidence("bug-found"), pull=pull) if s.label == "real bugs found")
    elsewhere = next(s for s in _scores(evidence("bug-found"), pull="999999") if s.label == "real bugs found")
    assert "fixed in this pull request" in here.meaning
    assert "this pull request" not in elsewhere.meaning


def test_every_glyph_the_header_names_is_committed() -> None:
    for name in ("passed", "failed", "partial", "replay", "bug-found"):
        html = "\n".join(_scoreboard(evidence(name), BASE))
        for asset in set(re.findall(r"x\.test/assets/score/([\w-]+\.svg)", html)):
            assert (ASSETS / "score" / asset).is_file(), asset


def test_each_number_is_read_once_with_its_name() -> None:
    html = "\n".join(_scoreboard(evidence("failed"), BASE))
    alts = [alt for alt in re.findall(r'alt="([^"]*)"', html) if alt]
    assert alts == [
        "1 contracts",
        "30/34 cases passed",
        "2/2 mutants caught",
        "0 real bugs found",
        "4 disagreements in this PR",
    ]
    assert all(f"**{label}**" in html for label in SCOREBOARD)


def test_the_comment_states_each_count_once() -> None:
    comment = render_comment(evidence("passed"), repository=ROOT, assets="https://x.test/assets")
    assert "/score/" in comment and "Disagreements caught" not in comment
    assert comment.count("34/34") == 1
    # Without the action's assets the header is still there, in bold text.
    plain = render_comment(evidence("passed"), repository=ROOT)
    assert "/score/" not in plain and "| **1** | **34/34** |" in plain
