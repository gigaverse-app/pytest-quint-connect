"""The comment's scoreboard: colored digits composed from fixed glyphs, the way pytest-obligation's report does."""

import ast
import re
from pathlib import Path

from pytest_quint_connect.report import SCOREBOARD, _score, _scoreboard, load, render_comment

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = Path(__file__).parent / "data" / "receipts"
ASSETS = ROOT / "docs" / "assets"
BASE = "https://x.test/assets/score/"


def evidence(name: str):
    return load([RECEIPTS / f"lru-{name}.json"])


def test_colors_follow_the_rule_red_failure_amber_unrun_green_passed() -> None:
    assert _score(evidence("passed")) == [
        ("1", "neutral"),
        ("15", "neutral"),
        ("34", "good"),
        ("0", "good"),
        ("0", "good"),
        ("2/2", "good"),
    ]
    failed = dict(zip([label for _, label in SCOREBOARD], _score(evidence("failed")), strict=True))
    assert failed["cases passed"] == ("30", "good") and failed["cases failed"] == ("4", "bad")
    partial = dict(zip([label for _, label in SCOREBOARD], _score(evidence("partial")), strict=True))
    assert partial["cases failed"] == ("0", "good") and partial["not run"][1] == "warning"


def test_every_glyph_the_scoreboard_names_is_committed() -> None:
    for name in ("passed", "failed", "partial", "replay", "bug-found"):
        html = _scoreboard(evidence(name), BASE)
        for asset in set(re.findall(r"x\.test/assets/score/([\w-]+\.svg)", html)):
            assert (ASSETS / "score" / asset).is_file(), asset


def test_each_number_is_read_once_and_the_captions_carry_every_label() -> None:
    html = _scoreboard(evidence("failed"), BASE)
    alts = [alt for alt in re.findall(r'alt="([^"]*)"', html) if alt]
    assert alts[:6] == [
        "1 contracts",
        "15 histories",
        "30 cases passed",
        "4 cases failed",
        "0 not run",
        "2/2 mutants caught",
    ]
    assert alts[-1].startswith("1 contracts · 15 histories")


def test_columns_match_the_captions_the_brand_script_draws() -> None:
    script = ast.parse((ROOT / "scripts" / "render_brand.py").read_text(encoding="utf-8"))
    (columns,) = [
        ast.literal_eval(node.value)
        for node in script.body
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SCORE_COLUMNS" for t in node.targets)
    ]
    assert [(start, label.lower()) for start, label in columns] == list(SCOREBOARD)


def test_the_comment_shows_it_only_with_the_actions_assets() -> None:
    assert "/score/" in render_comment(evidence("passed"), repository=ROOT, assets="https://x.test/assets")
    assert "/score/" not in render_comment(evidence("passed"), repository=ROOT)
