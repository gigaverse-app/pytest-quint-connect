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
with its four mutants and the real bug it found: the receipt this repository's sticky reports.
"""

import base64
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from pytest_quint_connect.cli import main
from pytest_quint_connect.ledger import Ledger
from pytest_quint_connect.report import (
    COMMENT_BUDGET,
    END,
    MUTANTS,
    MUTANTS_TIP,
    SPREAD,
    START,
    Evidence,
    ReportError,
    check_readme,
    embed,
    evidence_fingerprint,
    load,
    receipt_paths,
    render_comment,
    render_readme,
    renderer_version,
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
    assert comment.startswith("### ❌ Quint contracts: 1 test failed, 1 passed\n")
    # The alert, first after the headline, names the clearest failure (the witness, the shortest path) and its command.
    alert = comment.split("\n\n", 2)[1]
    assert alert.startswith("> [!CAUTION]\n> **4 histories left the model in `lru-cache`.**")
    assert "> Start with `witness-evictsLeastRecentlyUsedTest`: the code left the model at state 3" in alert
    assert (
        "> cd examples/lru_cache\n> pytest 'test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]'"
        in alert
    )
    # That failure in full, with the model's expectation beside what the code observed.
    assert (
        "`witness-evictsLeastRecentlyUsedTest`: the code left the model at state 3, after `fetch(key='a', value=0)`."
        in comment
    )
    assert "| `entries[0].key` | `'b'` | `'a'` |" in comment
    assert "…and 1 more difference" in comment
    assert "mismatch-test_lru_cache-witness-evictsLeastRecentlyUsedTest.json" in comment
    # The other three fold into one block, each with its own command.
    assert "<summary>3 more failing histories in <code>test_lru_contract.py::test_lru_cache</code></summary>" in comment
    assert "  pytest 'test_lru_contract.py::test_lru_cache[sample-5]'" in comment
    # The failed cases are not listed again as "not complete".
    assert "#### Not complete" not in comment
    assert "<summary><strong>What do these words and numbers mean?</strong></summary>" in comment
    assert "[run](https://run.example)" in comment and "`abc1234`" in comment


def test_comment_says_filtered_and_replayed_runs_are_not_complete() -> None:
    partial = render_comment(evidence("partial"), repository=REPO)
    assert partial.startswith("### 🟡 Quint contracts: 1 test partial, 1 not run")
    alert = partial.split("\n\n", 2)[1]
    assert alert.startswith("> [!WARNING]\n> **This run is not complete evidence")
    assert "> 🟡 **partial**: some cases were filtered out" in alert
    assert "none of its cases were selected" in alert
    assert "did not run or did not pass" in alert  # the session's own reason, which no failure shows
    replay = render_comment(evidence("replay"), repository=REPO)
    assert replay.startswith("### ➖ Quint contracts: 1 test not run, 1 replay-only")
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


def older_renderer(block: str) -> str:
    """The block as another release would lay it out: its own version, different wording, the same evidence."""
    marker = f" renderer {renderer_version()} -->"
    assert block.count(marker) == 1
    return block.replace(marker, " renderer 1.0.0 -->").replace("Generated by", "Made by")


def test_readme_check_judges_another_releases_block_by_its_evidence(tmp_path: Path) -> None:
    document = readme_with_block(tmp_path).read_text(encoding="utf-8")
    passed = embed(document, older_renderer(render_readme(evidence("passed"))))
    relaid = check_readme(passed, evidence("passed"))
    assert relaid.state == "current" and relaid.refresh and "layout of pytest-quint-connect 1.0.0" in relaid.reason
    # A release that lays out different evidence is still stale.
    found = embed(document, older_renderer(render_readme(evidence("bug-found"))))
    assert check_readme(found, evidence("passed")).state == "stale"
    # Only the recorded version differs: nothing to refresh.
    marker = f" renderer {renderer_version()} -->"
    same = embed(document, render_readme(evidence("passed")).replace(marker, " renderer 1.0.0 -->"))
    assert check_readme(same, evidence("passed")) == check_readme(same.replace("1.0.0", "9.9.9"), evidence("passed"))
    assert check_readme(same, evidence("passed")).reason == ""


def test_readme_check_cannot_judge_a_block_without_its_evidence(tmp_path: Path) -> None:
    block = render_readme(evidence("passed")).split("\n", 2)
    assert block[1].startswith("<!-- pytest-quint-connect:evidence ")
    unrecorded = embed(readme_with_block(tmp_path).read_text(encoding="utf-8"), f"{block[0]}\n{block[2]}")
    old = check_readme(unrecorded.replace("Generated by", "Made by"), evidence("passed"))
    assert old.state == "unverifiable" and old.refresh and "refresh it once" in old.reason


def test_the_evidence_fingerprint_is_a_compatibility_contract() -> None:
    """Blocks in adopters' READMEs carry these. Changing what is hashed makes every committed block stale for
    every adopter on the next release, so a change here needs a migration note, not just new goldens."""
    assert {name: evidence_fingerprint(evidence(name)) for name in ("passed", "bug-found")} == {
        # Regenerated receipts carry the model's state machine, which the README block shows, so it is hashed.
        "passed": "0e41f94228c03d2c",
        "bug-found": "54f993ec482c1172",
    }
    assert evidence_fingerprint(dogfood()) == "c86dc4be41ef2cfe"
    moved = Evidence([replace(row, root="/elsewhere") for row in evidence("passed").rows], 1, [])
    assert evidence_fingerprint(moved) == evidence_fingerprint(evidence("passed"))


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


def test_cli_passes_another_releases_block_and_names_the_pinned_refresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    readme = readme_with_block(tmp_path)
    readme.write_text(
        embed(readme.read_text(encoding="utf-8"), older_renderer(render_readme(evidence("passed")))), encoding="utf-8"
    )
    comment = tmp_path / "comment.md"
    receipt = str(RECEIPTS / "lru-passed.json")
    assert main(["report", receipt, "--readme", str(readme), "--check", "--comment", str(comment)]) == 0
    out = capsys.readouterr().out
    assert "is current: it shows this run's evidence in the layout of pytest-quint-connect 1.0.0" in out
    assert f"uvx --from pytest-quint-connect=={renderer_version()} pytest-quint-connect report" in out
    assert "contract block is current" in comment.read_text(encoding="utf-8")


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
    # The alert carries the command for the failure it says to start with.
    assert (
        "> cd contracts\n> pytest -c quint.ini 'lru/test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]'"
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


def test_the_check_never_requires_the_counter_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The action passes --counter-sign while it shares totals; a block filled locally has no sign and is current.
    readme = readme_with_block(tmp_path)
    receipt = str(RECEIPTS / "lru-passed.json")
    assert main(["report", receipt, "--readme", str(readme)]) == 0
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/shop")
    capsys.readouterr()
    assert main(["report", receipt, "--readme", str(readme), "--check", "--counter-sign"]) == 0
    out = capsys.readouterr().out
    assert "the contract block is current" in out
    assert "shows no counter sign (optional)" in out and "--counter-sign" in out
    assert "/v1/sign.svg" not in readme.read_text(encoding="utf-8")
    # Writing the block is what adds the sign.
    assert main(["report", receipt, "--readme", str(readme), "--counter-sign"]) == 0
    assert "/v1/sign.svg" in readme.read_text(encoding="utf-8")
    assert main(["report", receipt, "--readme", str(readme), "--check", "--counter-sign"]) == 0


def test_readme_links_point_at_the_rendering_versions_docs_and_a_release_does_not_stale_the_block(
    tmp_path: Path,
) -> None:
    from pytest_quint_connect import __version__
    from pytest_quint_connect.report import HOME

    block_text = render_readme(evidence("passed"))
    assert f"{HOME}/blob/v{__version__}/docs/limits.md" in block_text
    assert f"{HOME}/blob/main/docs/" not in block_text
    # A block another release rendered links that release's docs; it is judged by its evidence, so still current.
    document = embed(f"# Project\n\n{START}\n{END}\n", block_text)
    marker = f" renderer {renderer_version()} -->"
    older = document.replace(f"/blob/v{__version__}/docs/", "/blob/v1.0.0/docs/").replace(marker, " renderer 1.0.0 -->")
    assert older != document
    relaid = check_readme(older, evidence("passed"))
    assert relaid.state == "current" and relaid.refresh
    # The evidence still decides.
    assert check_readme(document.replace("17/17", "16/17"), evidence("passed")).state == "stale"


def test_whole_event_records_are_labelled_wherever_an_action_is_named(tmp_path: Path) -> None:
    receipt = json.loads((RECEIPTS / "lru-passed.json").read_text(encoding="utf-8"))
    contract = receipt["contracts"][0]
    contract["illustrated"]["steps"][3].update(arguments={"key": "a", "value": 0}, event_fields=True)
    mutation = contract["tests"][0]["mutations"]["stale-read"]
    mutation.update(arguments={"key": "a"}, event_fields=False)
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([path]), repository=REPO)
    assert "`fetch(event: key='a', value=0)`" in comment
    assert "state 3, after `fetch(key='a')`" in comment


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


TERM = f'[Mutant]({MUTANTS} "{MUTANTS_TIP}")'
# Receipts regenerated with a generated driver name the action's own arguments and count sampled catches.
CAUGHT = "`witness-evictsLeastRecentlyUsedTest`<br><sub>samples: 3/12 diverge</sub> | state 3, after `fetch(key='a')`"
# Older receipts (lru-failed.json) name the whole event record and predate sampled catch counts.
CAUGHT_EVENT = "`witness-evictsLeastRecentlyUsedTest` | state 3, after `fetch(key='a', value=0)`"
STALE_READ = "get() returns the value but does not refresh recency, so a just-read entry can be evicted next"


def test_this_repositorys_contract_reports_green_numbers_and_collapses_its_details() -> None:
    comment = render_comment(dogfood(), repository=REPO)
    # What matters now leads; the bug found earlier is credited apart, as history, not as a current failure.
    assert headline(comment) == ["### ✅ Quint contracts: every contract passed"]
    # Credited prominently, right under the header of numbers, with what it was: history, not a failure.
    assert comment[comment.index("> [!TIP]") :].splitlines()[:3] == [
        "> [!TIP]",
        "> **🏅 What this contract found:**",
        "> - Valid histories whose step calls one action without any { … } were rejected: Quint records "
        "mbt::actionTaken as step, and the event cross-check refused it (fixed in #3)",
    ]
    # The header gives the numbers, so the sticky's table summary names what it holds instead.
    expanded, rows = block(comment, "✅ Each contract test: its status, cases, histories and coverage")
    assert not expanded and len(rows) == 1 and rows[0].startswith("| ✅ | `session-receipts`")
    expanded, rows = block(comment, "🐞 Real bugs")
    assert not expanded and rows == [
        "| `session-receipts` | Valid histories whose step calls one action without any { … } were rejected: Quint "
        "records mbt::actionTaken as step, and the event cross-check refused it<br><sub>found by the first run of "
        "this contract · no mutant re-checks it</sub> | #3 | "
        "<sub>`tests/test_replay.py`<br>`::test_unbranched_step_is_recorded_under_the_step_name_and_still_replays`</sub> |"
    ]
    expanded, rows = block(comment, "🧪 Mutants")
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
    assert block(readme, "✅ 1 contract · 13/13 cases")[1] == block(comment, "✅ Each contract test")[1]
    for counted, named in (("🐞 1 real bug", "🐞 Real bugs"), ("🧪 4 mutants", "🧪 Mutants")):
        assert block(readme, counted) == block(comment, named)


def test_every_table_spans_the_comment_through_its_widest_column() -> None:
    comment = render_comment(evidence("bug-found"), repository=REPO)
    header, *headers = [table[0] for table in tables(comment)]
    assert header.startswith("| **1** | **34/34** |")
    assert headers[:3] == [
        f'| | {SPREAD}Contract | Cases passed | [Mutants caught]({MUTANTS} "{MUTANTS_TIP}") |',
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


def test_a_red_run_lists_failing_rows_first(tmp_path: Path) -> None:
    def fail_the_refactor(contract: dict) -> None:
        # The refactor sorts after test_lru_cache: only failing-first ordering puts it on top.
        contract["tests"][1]["outcomes"]["sample-3"] = "failed"

    comment = render_comment(edited_passed_receipt(tmp_path, fail_the_refactor), repository=REPO)
    assert headline(comment) == ["### ❌ Quint contracts: 1 test failed, 1 passed"]
    expanded, rows = block(comment, "❌ Each contract test")
    # The sticky folds the table under the failures; the README block keeps a red table open.
    assert not expanded
    assert block(render_readme(edited_passed_receipt(tmp_path, fail_the_refactor)), "❌ 1 contract")[0]
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


def test_the_comment_folds_its_details_under_what_a_person_must_do() -> None:
    # The table is detail in the sticky, red or green; the alert and the failures carry what to do.
    ledger = Ledger(caught={"k": {"found": 1, "kind": "mismatch", "state": "open"}})
    for each in (ledger, Ledger()):
        expanded, _ = block(render_comment(evidence("passed"), repository=REPO, ledger=each), "✅ Each contract")
        assert not expanded
    comment = render_comment(evidence("failed"), repository=REPO)
    assert "<details open>" not in comment
    # The command the alert gives is not repeated under the failure it reruns.
    assert comment.count("pytest 'test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]'") == 1
    assert (
        comment.index("> [!CAUTION]") < comment.index("#### What failed") < comment.index("<summary>❌ Each contract")
    )
    assert comment.index("<summary>❌ Each contract") < comment.index("What do these words")
    passed = render_comment(evidence("passed"), repository=REPO)
    assert passed.index("<summary>✅ Each contract") < passed.index(CLAIMS) < passed.index("What do these words")


def test_mutants_are_explained_on_hover_wherever_they_are_counted() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    assert f'| [Mutants caught]({MUTANTS} "{MUTANTS_TIP}") |\n' in comment
    assert f"| Contract | {TERM} |" in comment
    block_ = render_readme(evidence("passed"))
    assert f'<a href="https://github.com/gigaverse-app/pytest-quint-connect" title="{MUTANTS_TIP}">' in block_
    # The 🧪 summary cannot carry a link, so it spells the meaning out.
    assert "<summary>🧪 1 mutant (planted fake bug), caught</summary>" in block_
    assert "<summary>🧪 Mutants: what each planted fake bug breaks, and which history caught it</summary>" in comment


def test_comment_says_what_each_mutant_breaks_and_where_it_was_caught() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    assert headline(comment) == ["### ✅ Quint contracts: every contract passed"]
    # Both test functions caught it in the same place, so they share one row.
    assert block(comment, "🧪 Mutants") == (
        False,
        [f"| `lru-cache` | `stale-read` | {STALE_READ} | {CAUGHT} |"],
    )
    assert comment.index("| ✅ | `lru-cache`") < comment.index("<summary>🧪")
    assert block(render_readme(evidence("passed")), "🧪") == block(comment, "🧪")
    assert "🐞" not in comment and "Earlier" not in comment


def test_found_bugs_are_credited_apart_and_say_how_many_histories_their_return_breaks() -> None:
    comment = render_comment(evidence("bug-found"), repository=REPO)
    assert headline(comment) == ["### ✅ Quint contracts: every contract passed"]
    assert "> **🏅 What this contract found:**" in comment
    expanded, (row,) = block(comment, "🐞 Real bugs")
    assert not expanded and row == (
        "| `lru-cache` | reads did not refresh recency, so a just-read entry could be evicted next (test data: this "
        "cache was never shipped)<br><sub>found by witness evictsLeastRecentlyUsedTest · mutant `stale-read` "
        "reintroduces it: 4 of 15 histories diverge</sub> | example-only | "
        "<sub>`test_lru_contract.py`<br>`::test_lru_cache[witness-evictsLeastRecentlyUsedTest]`</sub> |"
    )
    # The mutant that reintroduces it is listed with the others, and says so.
    assert block(comment, "🧪 Mutants")[1] == [
        f"| `lru-cache` | `stale-read` | {STALE_READ}<br><sub>reintroduces a real bug this contract found</sub> | {CAUGHT} |"
    ]
    assert row in render_readme(evidence("bug-found"))
    assert comment.index("| ✅ | `lru-cache`") < comment.index("<summary>🐞") < comment.index("<summary>🧪")


@pytest.mark.parametrize("fixed_in", ["#157", "157", "https://github.com/o/r/pull/157"])
def test_a_bug_this_pull_request_fixes_is_credited_to_it_and_kept_apart_from_the_tally(
    tmp_path: Path, fixed_in: str
) -> None:
    receipt = json.loads((RECEIPTS / "lru-bug-found.json").read_text(encoding="utf-8"))
    receipt["contracts"][0]["bugs_found"][0]["fixed_in"] = fixed_in
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    ledger = Ledger(pushes=[{"push": 1, "head": "a", "status": "passed"}, {"push": 2, "head": "b", "status": "passed"}])
    comment = render_comment(load([path]), repository=REPO, ledger=ledger, pull="157")
    # Not "since fixed" in what reads as an earlier pull request, beside a tally of 0 fixed in this one.
    assert "> **🏅 This pull request fixes what this contract found:**" in comment
    assert "counted apart from disagreements, which are what this pull request's own pushes caught." in comment
    # The bug's own line does not link back to this pull request.
    tip = comment.split("> [!TIP]", 1)[1].split("\n\n", 1)[0]
    assert "(fixed in" not in tip
    assert "found by a contract and fixed in this pull request" in comment
    other = render_comment(load([path]), repository=REPO, ledger=ledger, pull="158")
    assert "🏅 What this contract found:" in other and "counted apart from disagreements" not in other


def test_receipts_without_mutant_specs_still_say_where_each_mutant_was_caught() -> None:
    # lru-failed.json was written before receipts carried descriptions or found bugs.
    assert block(render_comment(evidence("failed"), repository=REPO), "🧪") == (
        False,
        [f"| `lru-cache` | `stale-read` | — | {CAUGHT_EVENT} |"],
    )
    assert block(render_comment(evidence("partial"), repository=REPO), "🧪 Mutants") == (
        True,
        ["| `lru-cache` | `stale-read` | — | ➖ **not run** | not run in this session |"],
    )


def edited_bug_receipt(tmp_path: Path, edit) -> Evidence:
    receipt = json.loads((RECEIPTS / "lru-bug-found.json").read_text(encoding="utf-8"))
    edit(receipt["contracts"][0])
    path = tmp_path / "results.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return load([path])


def test_a_found_bug_claims_no_more_than_its_mutant_shows(tmp_path: Path) -> None:
    def survive_in_refactor(contract: dict) -> None:
        refactor = contract["tests"][1]
        refactor["mutations"] = {}
        refactor["outcomes"]["mutant-stale-read"] = "failed"
        refactor["findings"] = {"mutant-stale-read": {"survived": 15}}

    comment = render_comment(edited_bug_receipt(tmp_path, survive_in_refactor), repository=REPO)
    (row,) = block(comment, "🐞")[1]
    assert (
        "mutant `stale-read` reintroduces it: 4 of 15 histories diverge in `test_lru_contract.py::test_lru_cache` · "
        "mutant `stale-read` reintroduces it: **survived all 15 histories** in "
        "`test_lru_contract.py::test_ordered_refactor`</sub>"
    ) in row
    # The test that missed it comes first, and keeps the block open.
    expanded, rows = block(comment, "🧪 Mutants")
    assert expanded and [cells.split(" | ")[-2:] for cells in rows] == [
        ["❌ **not caught**", "survived all 15 histories |"],
        [
            "`witness-evictsLeastRecentlyUsedTest`<br><sub>samples: 3/12 diverge</sub>",
            "state 3, after `fetch(key='a')` |",
        ],
    ]
    assert rows[0].startswith("| `lru-cache`<br><sub>`test_lru_contract.py::test_ordered_refactor`</sub> |")

    def without_mutant(contract: dict) -> None:
        del contract["bugs_found"][0]["mutant"]

    comment = render_comment(edited_bug_receipt(tmp_path, without_mutant), repository=REPO)
    (row,) = block(comment, "🐞")[1]
    assert "no mutant re-checks it</sub>" in row and "histories diverge" not in row
    assert block(comment, "🧪")[1] == [f"| `lru-cache` | `stale-read` | {STALE_READ} | {CAUGHT} |"]

    def without_divergence_count(contract: dict) -> None:
        for test in contract["tests"]:
            for key in ("histories", "diverged"):
                del test["mutations"]["stale-read"][key]

    comment = render_comment(edited_bug_receipt(tmp_path, without_divergence_count), repository=REPO)
    (row,) = block(comment, "🐞")[1]
    # Without a count in the receipt, the row says where it was caught and invents no "0 of 0".
    assert "histories diverge" not in row
    assert "mutant `stale-read` reintroduces it: caught by `witness-evictsLeastRecentlyUsedTest`</sub>" in row


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
    for test in receipt["contracts"][0]["tests"]:
        for mutation in test["mutations"].values():
            for key in ("caught_by_sample", "samples_diverged", "samples"):
                mutation.pop(key, None)
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(receipt), encoding="utf-8")
    # Receipts from before caught_by_sample existed say nothing either way.
    quiet = render_comment(load([plain]), repository=REPO)
    assert "caught only by witnesses" not in quiet and "samples:" not in quiet
    # The warning is per mutant: one test's samples catch stale-read (5 of 12), the other's catch none.
    lru, ordered = receipt["contracts"][0]["tests"]
    lru["mutations"]["stale-read"].update(caught_by_sample=False, samples_diverged=0, samples=12)
    ordered["mutations"]["stale-read"].update(caught_by_sample=True, samples_diverged=5, samples=12)
    weak = tmp_path / "results.json"
    weak.write_text(json.dumps(receipt), encoding="utf-8")
    comment = render_comment(load([weak]), repository=REPO)
    assert "`test_lru_contract.py::test_lru_cache`: mutant `stale-read` caught only by witnesses" in comment
    assert "`test_lru_contract.py::test_ordered_refactor`: mutant" not in comment
    assert "Raise bounds.n_traces or max_steps" in comment
    # The mutant table says how many sampled histories catch each mutant, per test.
    assert "<sub>samples: 0/12 diverge</sub>" in comment and "<sub>samples: 5/12 diverge</sub>" in comment


CLAIMS = "<summary>What <code>lru-cache</code> says</summary>"


def claims_block(text: str) -> str:
    start = text.index(CLAIMS)
    return text[start : text.index("\n</details>\n", start)]


def test_each_contract_says_what_it_claims_so_a_reviewer_can_judge_the_model() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    # One block per contract, although two test functions check it: folded in the sticky, open in the README.
    assert comment.count(CLAIMS) == 1 and f"<details>\n{CLAIMS}" in comment
    assert f"<details open>\n{CLAIMS}" in render_readme(evidence("passed"))
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
    # The first witness, read from its trace: the observation it starts from, then what each action changes.
    assert "**Witness `evictsLeastRecentlyUsedTest`, step by step**" in block
    rows = [line for line in block.splitlines() if line.startswith("| `")]
    assert rows[0] == "| `0` | `init()` | `{ entries: [], lastRead: NoRead }` |"
    assert rows[3] == (
        "| `3` | `fetch(key='a')` | "
        '`entries: [{ key: "a", value: 1 }, { key: "b", value: 2 }] → [{ key: "b", value: 2 }, { key: "a", value: 1 }]`'
        "<br>`lastRead: NoRead → Hit(1)` |"
    )
    assert rows[5] == "| `5` | `inspect()` | unchanged |"
    assert len(rows) == 6
    # Verdicts come first; the claims follow them.
    assert comment.index("<summary>🧪") < comment.index(CLAIMS)
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
        contract["illustrated"]["steps"][0]["observed"] = {"note": "a|b`</details>"}
        contract["illustrated"]["steps"][1]["observed"] = {"note": "c|d`<img src=x>"}

    comment = render_comment(edited_passed_receipt(tmp_path, inject), repository=REPO)
    block = claims_block(comment)
    assert "- `store`: &lt;img src=x&gt; pings &#64;octocat" in block
    assert r"**Invariant** `ok'\|</details>`" in block
    assert r"""| `0` | `init()` | `{ note: "a\|b'</details>" }` |""" in block
    assert r"""| `1` | `store(key='a', value=1)` | `note: "a\|b'</details>" → "c\|d'<img src=x>"` |""" in block
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


def test_a_wide_observation_shows_only_the_fields_each_action_changes(tmp_path: Path) -> None:
    # The shape that made every row look alike in a real contract: nested records whose unchanged prefix
    # filled the cell, so the field an action changed was never visible.
    shard = {"bad": False, "call": 0, "collected": False, "durationTicks": 0, "failureCount": 0, "selected": False}
    start = {"extraExists": False, "extraFirst": shard, "extraSecond": shard, "verified": True, "rejections": 0}
    rejected = {**start, "verified": False, "rejections": 1, "extraFirst": {**shard, "bad": True}}
    many = {**rejected, "extraExists": True, "extraSecond": {key: 1 for key in shard}}

    def widen(contract: dict) -> None:
        observations = [start, rejected, rejected, many]
        steps = contract["illustrated"]["steps"][: len(observations)]
        contract["illustrated"]["steps"] = [
            {**step, "observed": seen} for step, seen in zip(steps, observations, strict=True)
        ]

    block = claims_block(render_comment(edited_passed_receipt(tmp_path, widen), repository=REPO))
    rows = [line for line in block.splitlines() if line.startswith("| `")]
    assert rows[1].endswith("| `extraFirst.bad: false → true`<br>`verified: true → false`<br>`rejections: 0 → 1` |")
    assert rows[2].endswith("| unchanged |")
    # Seven fields change: six are shown, and the cell says how many it leaves out.
    assert rows[3].count("<br>") == 6 and rows[3].endswith("and 1 more field |")


def test_a_field_one_observation_lacks_is_shown_as_absent(tmp_path: Path) -> None:
    def drop(contract: dict) -> None:
        steps = contract["illustrated"]["steps"]
        steps[1]["observed"] = {"entries": [], "lastRead": steps[0]["observed"]["lastRead"], "extra": 1}

    block = claims_block(render_comment(edited_passed_receipt(tmp_path, drop), repository=REPO))
    assert "| `1` | `store(key='a', value=1)` | `extra: absent → 1` |" in block


def mermaid(text: str) -> str:
    """The first Mermaid diagram in ``text``."""
    return text.split("```mermaid\n", 1)[1].split("\n```", 1)[0]


def test_the_model_is_drawn_as_the_state_machine_it_names_with_a_full_size_link() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    block = claims_block(comment)
    # The key is in the model's own words: the doc comments on the variables that name states and steps.
    key = (
        "**How `lru-cache` behaves**. **Boxes:** how full the cache is after each step (variable `fill`). "
        "**Arrows:** what each step did to the cache (variable `change`), in bold, then the action that did it. "
        "Merged from the 15 histories Quint generated; a state or step none of them reached is not drawn."
    )
    assert key in block and block.index(key) < block.index("**Actions**")
    diagram = mermaid(block)
    # The diagram carries its own title, and the arrows say what each step did: a full cache evicts.
    assert diagram.splitlines() == [
        "---",
        'title: "lru-cache: how full the cache is after each step"',
        "---",
        "stateDiagram-v2",
        '  state "<b>Empty</b>" as s0',
        '  state "<b>Partly filled</b>" as s1',
        '  state "<b>Full</b>" as s2',
        "  [*] --> s0",
        "  s0 --> s1 : <b>Added entry</b> #183; store",
        "  s1 --> s2 : <b>Added entry</b> #183; store",
        "  s2 --> s2 : <b>Evicted least recently used</b> #183; store<br><b>Replaced value</b> #183; store"
        "<br><b>Read hit moved to most recent</b> #183; fetch<br><b>Read missed</b> #183; fetch"
        "<br><b>Nothing changed</b> #183; inspect",
        "  s1 --> s1 : <b>Replaced value</b> #183; store<br><b>Read missed</b> #183; fetch"
        "<br><b>Read hit moved to most recent</b> #183; fetch<br><b>Nothing changed</b> #183; inspect",
        "  s0 --> s0 : <b>Read missed</b> #183; fetch",
    ]
    # The link carries this very diagram to mermaid.live in its fragment, which never reaches a server.
    link = re.search(r"\[Open full size ↗\]\((https://mermaid\.live/view#base64:([A-Za-z0-9_=-]+))\)", block)
    assert link is not None
    state = json.loads(base64.urlsafe_b64decode(link.group(2)))
    assert state["code"] == diagram and json.loads(state["mermaid"]) == {"theme": "default"}
    assert mermaid(render_readme(evidence("passed"))) == diagram
    # The witness stays as a table, folded under its scenario's name.
    assert "<summary>Scenario: Evicts least recently used, step by step</summary>" in block


def test_an_arrow_names_the_arguments_every_call_on_it_shared(tmp_path: Path) -> None:
    def share(contract: dict) -> None:
        contract["machine"]["transitions"][0]["actions"] = [
            {"action": "session", "arguments": {"kind": "full"}},
            {"action": "session", "arguments": {"kind": "replay", "strict": True}, "event_fields": True},
        ]

    lines = mermaid(render_comment(edited_passed_receipt(tmp_path, share), repository=REPO)).splitlines()
    assert "  s0 --> s1 : session(kind='full')<br>session(event: kind='replay', strict=True)" in lines


def test_a_model_that_names_no_states_is_drawn_by_its_values_and_says_how_to_name_them(tmp_path: Path) -> None:
    def unnamed(contract: dict) -> None:
        contract["machine"]["named_by"] = None
        contract["machine"]["described"] = {}
        contract["machine"]["states"] = [
            {"fields": [["entries", "empty"], ["lastRead", "NoRead"]]},
            {"fields": [["entries", "1 item"], ["lastRead", "NoRead"]]},
            {"fields": [["entries", "2 items"], ["lastRead", "NoRead"]]},
        ]

    block = claims_block(render_comment(edited_passed_receipt(tmp_path, unnamed), repository=REPO))
    assert '  state "entries: empty<br>lastRead: NoRead" as s0' in mermaid(block)
    assert 'title: "lru-cache: its states, by the values it expects"' in mermaid(block)
    assert "The model does not name its states: give it a variable that does and set the manifest's `state_names`" in (
        block
    )


def test_a_machine_too_large_to_draw_is_described_instead(tmp_path: Path) -> None:
    def explode(contract: dict) -> None:
        contract["machine"]["states"] = [{"name": f"S{index}"} for index in range(31)]

    block = claims_block(render_comment(edited_passed_receipt(tmp_path, explode), repository=REPO))
    assert "its histories reached 31 distinct states, too many to draw" in block and "```mermaid" not in block


def test_receipt_text_cannot_escape_the_diagram(tmp_path: Path) -> None:
    hostile = 'x"] --> evil["`</details>```\n<img src=x>|#;:'

    def inject(contract: dict) -> None:
        machine = contract["machine"]
        machine["states"][0] = {"name": hostile}
        machine["states"][1] = {"fields": [[hostile, hostile]]}
        machine["transitions"][0]["actions"] = [{"action": hostile, "arguments": {hostile: hostile}}]

    comment = render_comment(edited_passed_receipt(tmp_path, inject), repository=REPO)
    # One fence opens the diagram and one closes it: an injected fence cannot end it early.
    assert comment.count("```mermaid") == 1
    for line in mermaid(comment).splitlines()[4:]:
        label = line.split('"')[1] if '"' in line else line.partition(" : ")[2]
        # What remains once our own markup and Mermaid's entity codes are taken out is plain text.
        bare = re.sub(r"</?b>|<br>|#\d+;", "", label)
        assert not set(bare) & set('"`<>|\n;#'), line


@pytest.mark.parametrize(
    ("witness", "sentence"),
    [
        ("evictsLeastRecentlyUsedTest", "Evicts least recently used"),
        ("staleSuccessIsReplacedTest", "Stale success is replaced"),
        ("replayIsNeverCompleteTest", "Replay is never complete"),
        ("reapedOwnerCannotWriteTest", "Reaped owner cannot write"),
        ("noQuintTTLTest", "No quint TTL"),
        ("plain_snake_case", "Plain snake case"),
    ],
)
def test_a_witness_name_reads_as_a_scenario(tmp_path: Path, witness: str, sentence: str) -> None:
    def rename(contract: dict) -> None:
        contract["illustrated"]["witness"] = witness

    block = claims_block(render_comment(edited_passed_receipt(tmp_path, rename), repository=REPO))
    assert f"<summary>Scenario: {sentence}, step by step</summary>" in block


def test_a_diagram_title_keeps_only_plain_text(tmp_path: Path) -> None:
    def document(contract: dict) -> None:
        contract["machine"]["described"] = {"states": 'How "full" <b>`it`</b> is\n---\ntitle: x'}

    lines = mermaid(render_comment(edited_passed_receipt(tmp_path, document), repository=REPO)).splitlines()
    # Mermaid does not decode entity codes in a title, so anything but plain text is dropped, never escaped.
    assert lines[:3] == ["---", 'title: "lru-cache: how full b it /b is --- title: x"', "---"]
