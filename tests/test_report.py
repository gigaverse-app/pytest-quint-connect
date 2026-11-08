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
"""

import hashlib
import json
from pathlib import Path

import pytest

from pytest_quint_connect.cli import main
from pytest_quint_connect.report import (
    COMMENT_BUDGET,
    END,
    PLANTED,
    PLANTED_TIP,
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
    # Every table row keeps its own column count: 4 cells in the contract table, 3 in the tally.
    assert all(line.count("|") - line.count("\\|") in (4, 5) for line in comment.splitlines() if line.startswith("|"))


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


def section(text: str, heading: str) -> list[str]:
    """The list lines under a bold heading that starts with ``heading``."""
    start = text.index(f"**{heading}")
    _, _, rest = text[start:].partition("**\n\n")
    return rest.split("\n\n", 1)[0].splitlines()


def planted(text: str) -> list[str]:
    return section(text, f"{TERM}")


TERM = f'[Planted bugs]({PLANTED} "{PLANTED_TIP}")'


def test_planted_bugs_are_explained_on_hover_wherever_they_are_counted() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    assert f'| | Contract | Cases passed | [Planted bugs caught]({PLANTED} "{PLANTED_TIP}") |\n' in comment
    assert f"**{TERM} this contract catches**" in comment
    block = render_readme(evidence("passed"))
    assert f'<a href="https://github.com/gigaverse-app/pytest-quint-connect" title="{PLANTED_TIP}">' in block
    assert "mutant" not in comment.lower().replace(PLANTED_TIP.lower(), "")


def test_comment_says_what_each_planted_bug_breaks_and_where_it_was_caught() -> None:
    comment = render_comment(evidence("passed"), repository=REPO)
    # Both test functions caught it in the same place, so they share one line.
    assert planted(comment) == [
        "- `stale-read`: get() returns the value but does not refresh recency, so a just-read entry can be "
        "evicted next. Caught by `witness-evictsLeastRecentlyUsedTest` at state 3, after `fetch(key='a', value=0)`."
    ]
    assert comment.index("| ✅ | `lru-cache`") < comment.index(f"**{TERM}")
    assert planted(render_readme(evidence("passed"))) == planted(comment)
    assert "Bugs this contract found" not in comment and "bug found" not in comment


def test_found_bugs_lead_with_what_was_fixed_and_how_many_histories_its_return_breaks() -> None:
    comment = render_comment(evidence("bug-found"), repository=REPO)
    assert comment.startswith("### ✅ Quint contracts: 1 contract · 2 tests passed · 1 bug found\n")
    (line,) = section(comment, "Bugs this contract found")
    assert line == (
        "1. **reads did not refresh recency, so a just-read entry could be evicted next (test data: this cache was "
        "never shipped).** Fixed in example-only · found by witness evictsLeastRecentlyUsedTest · regression test "
        "`test_lru_contract.py::test_lru_cache[witness-evictsLeastRecentlyUsedTest]`. 4 of 15 histories diverge "
        f'when [planted bug]({PLANTED} "{PLANTED_TIP}") `stale-read` (get() returns the value but does not refresh '
        "recency, so a just-read entry can be evicted next) reintroduces it; first caught by "
        "`witness-evictsLeastRecentlyUsedTest` at state 3, after `fetch(key='a', value=0)`."
    )
    # The planted bug that reintroduces a found bug is reported with it, not again in the planted list.
    assert f"**{TERM}" not in comment
    assert line in render_readme(evidence("bug-found"))
    assert comment.index("| ✅ | `lru-cache`") < comment.index("**Bugs this contract found**")


def test_receipts_without_mutant_specs_still_say_where_each_planted_bug_was_caught() -> None:
    # lru-failed.json was written before receipts carried descriptions or found bugs.
    assert planted(render_comment(evidence("failed"), repository=REPO)) == [
        "- `stale-read`: Caught by `witness-evictsLeastRecentlyUsedTest` at state 3, after `fetch(key='a', value=0)`."
    ]
    assert planted(render_comment(evidence("partial"), repository=REPO)) == [
        "- `stale-read`: **Not caught**: not run in this session."
    ]


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
    header, caught, survived = section(comment, "Bugs this contract found")
    assert header.startswith("1. **reads did not refresh recency") and header.endswith(
        "witness-evictsLeastRecentlyUsedTest]`."
    )
    assert caught.startswith("   - 4 of 15 histories diverge") and caught.endswith(
        "<sub>`test_lru_contract.py::test_lru_cache`</sub>"
    )
    assert (
        "which reintroduces it: survived all 15 histories. <sub>`test_lru_contract.py::test_ordered_refactor`"
        in survived
    )

    def without_mutant(contract: dict) -> None:
        del contract["bugs_found"][0]["mutant"]

    comment = render_comment(edited_bug_receipt(tmp_path, without_mutant), repository=REPO)
    (line,) = section(comment, "Bugs this contract found")
    assert (
        line.endswith(f'No [planted bug]({PLANTED} "{PLANTED_TIP}") re-checks it.') and "histories diverge" not in line
    )
    assert planted(comment)[0].startswith("- `stale-read`: get() returns")


def test_manifest_text_cannot_inject_markup_or_mentions(tmp_path: Path) -> None:
    def inject(contract: dict) -> None:
        contract["mutant_specs"][0]["description"] = "<img src=x> pings\n@octocat"
        contract["bugs_found"][0]["fixed_in"] = "see [this](javascript:alert(1))"
        contract["bugs_found"][0]["summary"] = "**bold** <script>"

    (line,) = section(render_comment(edited_bug_receipt(tmp_path, inject), repository=REPO), "Bugs this contract found")
    assert "&lt;img src=x&gt; pings &#64;octocat" in line and "&lt;script&gt;" in line
    assert "Fixed in `see [this](javascript:alert(1))`" in line


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
    assert comment.index(f"**{TERM}") < comment.index(CLAIMS_OPEN)
    assert claims_block(render_readme(evidence("passed"))) == block


def test_receipts_without_claims_render_no_claims() -> None:
    # lru-failed.json was written before receipts recorded the manifest's actions and invariant.
    assert "<details>" not in render_comment(evidence("failed"), repository=REPO)
    assert "<details>" not in render_readme(evidence("partial"))


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
    assert "<details>" not in comment and "truncated" not in comment
    assert comment.rstrip().endswith("pytest-quint-connect</a></sub>")
