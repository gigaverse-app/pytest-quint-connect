"""Render pytest-quint-connect receipts as a pull-request comment and a README block.

Both views are built from the same rows, read only from ``results.json`` receipts, so a report
never claims more than the sessions that wrote them. The README block leaves out the run, the
commit and every failure detail, so it changes only when the evidence changes: a committed block
can be checked against a fresh run the way ``codegen --check`` checks a generated driver.
"""

import ast
import difflib
import glob
import hashlib
import json
import re
import shlex
import urllib.parse
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .contract import display_path
from .plugin import WITNESSES_ONLY, witnesses_only

if TYPE_CHECKING:
    from .ledger import Ledger

# Whatever one test function shows about a bug: a line of text, or the cells of a mutant's row.
_Shown = TypeVar("_Shown")

START = "<!-- pytest-quint-connect:start -->"
END = "<!-- pytest-quint-connect:end -->"
_LISTED = re.compile(r"<!-- pytest-quint-connect:contracts (.*?) -->")
HOME = "https://github.com/gigaverse-app/pytest-quint-connect"
LIMITS = f"{HOME}/blob/main/docs/limits.md"
# "Mutant" is the industry term but jargon: every mention links to this explanation, shown on hover.
MUTANTS = f"{HOME}/blob/main/docs/why.md#proof-that-the-tests-can-fail"
MUTANTS_TIP = (
    "Mutants are planted fake bugs: deliberately broken copies of the code, used only during this test run. "
    "Each must make some history diverge, proving the tests are meaningful: they would notice that kind of bug"
)


def _term(text: str) -> str:
    """``text`` linked to the explanation of mutants, which shows on hover."""
    return f'[{text}]({MUTANTS} "{MUTANTS_TIP}")'


# GitHub refuses comments over 65,536 characters; leave room for the publisher's trailer.
COMMENT_BUDGET = 60_000
MAX_FAILURES = 20
MAX_STEPS = 20
# Changed fields shown per step; a step that changes more says how many it leaves out.
MAX_CHANGES = 6

# Worst first: the headline names the worst status any contract reached.
SEVERITY = ("failed", "interrupted", "partial", "not run", "replay-only", "passed")
ICON = {"failed": "❌", "interrupted": "⏳", "partial": "🟡", "not run": "➖", "replay-only": "🔁", "passed": "✅"}
MEANING = {
    "failed": "a case failed, a mutant survived, or Quint could not generate histories",
    "interrupted": "the session ended before writing a final receipt",
    "partial": "some cases were filtered out or did not run; not complete evidence",
    "not run": "the contract test was collected but none of its cases were selected",
    "replay-only": "one saved trace was replayed; never complete coverage",
    "passed": "every case of the contract ran and passed",
}


class ReportError(Exception):
    """A receipt or document cannot be read as this tool's output."""


@dataclass(frozen=True)
class Failure:
    case: str
    outcome: str
    finding: dict[str, Any]


@dataclass(frozen=True)
class Catch:
    """One mutant of one contract test: what the manifest says it is, and what the receipt shows it did."""

    mutant: str
    description: str | None
    outcome: str | None
    mutation: dict[str, Any] | None
    survived: int | None


@dataclass(frozen=True)
class Claims:
    """What a contract's manifest says, as its receipt records it: the model a reviewer judges.

    ``illustrated`` is one witness step by step, read from its generated trace: each action and the
    observation the model expects after it.
    """

    actions: dict[str, str]
    invariant: str
    witnesses: list[str]
    bounds: dict[str, Any]
    illustrated: dict[str, Any] | None


@dataclass(frozen=True)
class Row:
    """One contract test function in one session."""

    contract: str
    test: str
    root: str | None
    status: str
    witnesses: int
    samples: int
    seed: int | None
    passed: int
    cases: int
    mutants_detected: int
    mutants: int
    covered: bool | None
    quint: str | None
    generation_failure: str | None = None
    config_file: str | None = None
    failures: list[Failure] = field(default_factory=list)
    catches: list[Catch] = field(default_factory=list)
    # The manifest's bugs_found, as recorded in the receipt: declared facts, shared by the contract's tests.
    bugs_found: list[dict[str, str]] = field(default_factory=list)
    passed_cases: frozenset[str] = frozenset()
    witnesses_only: bool = False
    # None for receipts written before they recorded the manifest's actions and invariant.
    claims: Claims | None = None

    @property
    def histories(self) -> int:
        return self.witnesses + self.samples


@dataclass(frozen=True)
class Evidence:
    """Every row of every receipt, and why any session is incomplete."""

    rows: list[Row]
    receipts: int
    incomplete: list[str]

    @property
    def status(self) -> str | None:
        statuses = {row.status for row in self.rows}
        return next((status for status in SEVERITY if status in statuses), None)

    @property
    def complete(self) -> bool:
        return bool(self.rows) and not self.incomplete and all(row.status == "passed" for row in self.rows)


def receipt_paths(patterns: list[str]) -> list[Path]:
    """Expand files and recursive globs; a pattern that matches nothing contributes nothing."""
    found: set[Path] = set()
    for pattern in patterns:
        magic = any(character in pattern for character in "*?[")
        matches = glob.glob(pattern, recursive=True, include_hidden=True) if magic else [pattern]
        # _work holds the sessions a driver runs inside one history (the dogfood contract runs whole pytest
        # sessions there); their receipts describe the driver's subject, not this run's contracts.
        found.update(Path(match) for match in matches if Path(match).is_file() and "_work" not in Path(match).parts)
    return sorted(found)


def load(paths: list[Path]) -> Evidence:
    rows: list[Row] = []
    incomplete: list[str] = []
    for path in paths:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ReportError(f"{path}: not a readable receipt: {error}") from error
        if not isinstance(receipt, dict) or receipt.get("tool") != "pytest-quint-connect":
            raise ReportError(f"{path}: not a pytest-quint-connect results.json")
        rows += _rows(receipt)
        incomplete += [str(line) for line in receipt.get("incomplete_required", [])]
    return Evidence(sorted(rows, key=lambda row: (row.contract, row.test)), len(paths), incomplete)


def _rows(receipt: dict[str, Any]) -> list[Row]:
    rows = []
    for contract in receipt.get("contracts", []):
        bounds = contract.get("bounds", {})
        witnesses, mutants = len(contract.get("witnesses", [])), len(contract.get("mutants", []))
        samples = int(bounds.get("n_traces", 0))
        failure = contract.get("generation_failure")
        # Receipts written before mutant_specs existed carry bare names only.
        specs = {spec["name"]: spec for spec in contract.get("mutant_specs", [])}
        claims = (
            Claims(
                actions=dict(contract["actions"]),
                invariant=str(contract.get("invariant")),
                witnesses=list(contract.get("witnesses", [])),
                bounds=dict(bounds),
                illustrated=contract.get("illustrated"),
            )
            if "actions" in contract
            else None
        )
        for test in contract.get("tests", []):
            outcomes: dict[str, str] = test.get("outcomes", {})
            failed = [case for case, outcome in outcomes.items() if outcome == "failed"]
            if failure or failed:
                status = "failed"
            elif receipt.get("status") == "running":
                status = "interrupted"
            elif not outcomes:
                status = "not run"
            elif receipt.get("mode") == "replay-only":
                status = "replay-only"
            else:
                status = "passed" if test.get("complete") else "partial"
            findings = test.get("findings", {})
            rows.append(
                Row(
                    contract=str(contract.get("name")),
                    test=str(test.get("test")),
                    root=receipt.get("root"),
                    status=status,
                    witnesses=witnesses,
                    samples=samples,
                    seed=bounds.get("seed"),
                    passed=sum(1 for outcome in outcomes.values() if outcome == "passed"),
                    cases=witnesses + samples + mutants + 1,
                    mutants_detected=len(test.get("mutations", {})),
                    mutants=mutants,
                    covered={"passed": True, "failed": False}.get(outcomes.get("coverage", "")),
                    quint=contract.get("quint"),
                    generation_failure=failure,
                    config_file=receipt.get("config_file"),
                    failures=[Failure(case, outcomes[case], findings.get(case, {})) for case in sorted(failed)],
                    catches=[
                        Catch(
                            mutant=name,
                            description=specs.get(name, {}).get("description"),
                            outcome=outcomes.get(f"mutant-{name}"),
                            mutation=test.get("mutations", {}).get(name),
                            survived=findings.get(f"mutant-{name}", {}).get("survived"),
                        )
                        for name in contract.get("mutants", [])
                    ],
                    bugs_found=list(contract.get("bugs_found", [])),
                    passed_cases=frozenset(case for case, outcome in outcomes.items() if outcome == "passed"),
                    witnesses_only=witnesses_only(test.get("mutations", {}), samples),
                    claims=claims,
                )
            )
    return rows


def _code(text: object, limit: int = 200) -> str:
    """Inline code safe inside a table cell, whatever the receipt contains."""
    value = " ".join(str(text).split()).replace("`", "'").replace("|", "\\|")
    return f"`{value if len(value) <= limit else value[: limit - 1] + '…'}`"


def _text(text: object, limit: int = 300) -> str:
    """Prose from a receipt, safe inside a list item or a table cell: one line, no HTML, no @-mentions."""
    value = " ".join(str(text).split())
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("@", "&#64;")
    value = value if len(value) <= limit else value[: limit - 1] + "…"
    return value.replace("|", "\\|")


def _ref(ref: object) -> str:
    """A commit, PR or URL as plain text GitHub links; anything else as inline code."""
    text = str(ref)
    return text if re.fullmatch(r"https://[^\s<>()\[\]|]+|[\w.#/-]{1,80}", text) else _code(text, 80)


def _call(action: object, arguments: object) -> str:
    """``fetch(key='a', value=0)`` as inline code."""
    shown = ", ".join(f"{key}={value!r}" for key, value in dict(arguments or {}).items())
    return _code(f"{action}({shown})")


def _value(value: object) -> str:
    """A value as the receipt records it (``compare.plain``), written in Quint's syntax: ``Hit(1)``, ``NoRead``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(map(_value, value)) + "]"
    if not isinstance(value, dict):
        return str(value)
    # ITF, and so plain(), writes a variant as exactly {tag, value}; a unit variant's value is the empty tuple.
    if set(value) == {"tag", "value"}:
        unit = value["value"] == {"#tup": []}
        return str(value["tag"]) if unit else f"{value['tag']}({_value(value['value'])})"
    if set(value) == {"#tup"}:
        return "(" + ", ".join(map(_value, value["#tup"])) + ")"
    if set(value) == {"#set"}:
        return "Set(" + ", ".join(map(_value, value["#set"])) + ")"
    if set(value) == {"#map"}:
        return "Map(" + ", ".join(f"{_value(key)} -> {_value(item)}" for key, item in value["#map"]) + ")"
    if set(value) == {"#unserializable"}:
        return str(value["#unserializable"])
    if not value:
        return "{}"
    return "{ " + ", ".join(f"{key}: {_value(item)}" for key, item in value.items()) + " }"


def _plural(count: int, word: str, plural: str = "") -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


@dataclass(frozen=True)
class Totals:
    """What the headline, the table's summary and the README badge count, summed once over the rows.

    Cases and mutants count per test function: each runs against its own driver. Several test
    functions can replay one contract's histories (an implementation and its refactor), so histories
    count once per contract.
    """

    contracts: int
    passed: int
    cases: int
    detected: int
    mutants: int
    histories: int

    @classmethod
    def of(cls, rows: list[Row]) -> "Totals":
        return cls(
            contracts=len({row.contract for row in rows}),
            passed=sum(row.passed for row in rows),
            cases=sum(row.cases for row in rows),
            detected=sum(row.mutants_detected for row in rows),
            mutants=sum(row.mutants for row in rows),
            histories=sum({row.contract: row.histories for row in rows}.values()),
        )

    def mutants_caught(self) -> str:
        return f"{self.detected}/{self.mutants} mutants caught"


def _green(evidence: Evidence, ledger: "Ledger | None" = None) -> bool:
    """Nothing to look at: every contract ran every case and passed, every mutant was caught, and no
    disagreement this pull request caught is still open. Only then does the README start its table
    collapsed and the sticky lead with what the contracts have found."""
    totals = Totals.of(evidence.rows)
    return evidence.complete and totals.detected == totals.mutants and (ledger is None or ledger.open == 0)


def _headline(evidence: Evidence) -> str:
    """What matters now: ``1 contract · all 13 cases passed · 4/4 mutants caught``.

    When anything is not passed, contract tests per status, worst first: ``1 test failed, 1 passed``.
    Bugs the contracts found earlier are history, not the state of this run, so they are credited apart.
    """
    if not evidence.rows:
        return "no contract ran in this run"
    totals = Totals.of(evidence.rows)
    counts = [(status, count) for status in SEVERITY if (count := sum(row.status == status for row in evidence.rows))]
    if [status for status, _ in counts] == ["passed"]:
        parts = [f"all {totals.cases} cases passed"]
    else:
        # Only the first count names its unit: "1 test failed, 1 passed".
        tests = [f"{_plural(counts[0][1], 'test')} {counts[0][0]}"] + [f"{n} {status}" for status, n in counts[1:]]
        parts = [", ".join(tests), f"{totals.passed}/{totals.cases} cases passed"]
    if totals.mutants:
        parts.append(totals.mutants_caught())
    return " · ".join([_plural(totals.contracts, "contract"), *parts])


def _verdict(evidence: Evidence) -> str:
    """The sticky's headline: ``every contract passed``, or contract tests per status, worst first."""
    if not evidence.rows:
        return "no contract ran in this run"
    counts = [(status, count) for status in SEVERITY if (count := sum(row.status == status for row in evidence.rows))]
    if [status for status, _ in counts] == ["passed"]:
        return "every contract passed"
    return ", ".join([f"{_plural(counts[0][1], 'test')} {counts[0][0]}"] + [f"{n} {s}" for s, n in counts[1:]])


def _found_bugs(rows: list[Row]) -> list[tuple[str, dict[str, str]]]:
    """Each bug a contract's manifest says it found, once per contract, in row order."""
    return [(contract, bug) for contract, members in _by_contract(rows).items() for bug in members[0].bugs_found]


def _fixed_here(bug: dict[str, str], pull: str) -> bool:
    """Whether ``bug`` names this pull request (``#N``, ``N`` or its URL) as its fix."""
    fixed = str(bug.get("fixed_in") or "").strip()
    return bool(pull) and (fixed.lstrip("#") == pull or fixed.rstrip("/").endswith(f"/pull/{pull}"))


def _credit(rows: list[Row], pull: str = "") -> list[str]:
    """Bugs the contracts found, credited apart from the headline so nobody reads them as open.

    A bug fixed by this very pull request is said to be, with why the disagreement tally does not count it:
    the tally holds only what this pull request's pushes caught, while a found bug is pinned by a mutant.
    """
    found = _found_bugs(rows)
    if not found:
        return []
    these = "these contracts" if len(_by_contract(rows)) > 1 else "this contract"
    here = sum(1 for _, bug in found if _fixed_here(bug, pull))
    title = f"🏅 This pull request fixes what {these} found" if here == len(found) else f"🏅 What {these} found"
    lines = ["> [!TIP]", f"> **{title}:**"]
    if here:
        lines += [
            ">",
            "> A mutant puts each one back, and every run must catch it. They are counted apart from"
            " disagreements, which are what this pull request's own pushes caught.",
            ">",
        ]
    shown = found[:5]
    for _, bug in shown:
        # A bug this pull request fixes needs no link back to the page the reader is on.
        fixed = "" if _fixed_here(bug, pull) else f" (fixed in {_ref(bug.get('fixed_in'))})"
        lines.append(f"> - {_text(bug.get('summary'), 160).rstrip('.')}{fixed}")
    if len(found) > len(shown):
        lines.append(f"> - …and {len(found) - len(shown)} more, in the table below")
    return [*lines, ""]


def _collapsible(summary: str, body: list[str], *, expanded: bool = False) -> list[str]:
    """``body`` under a ``<details>`` whose ``summary`` is plain text: markdown does not render in a summary.

    The blank line after the summary lets GitHub render a table or list as markdown inside the block.
    """
    return [f"<details{' open' if expanded else ''}>", f"<summary>{summary}</summary>", "", *body, "</details>", ""]


def _table(rows: list[Row]) -> list[str]:
    """Four columns, so the table fits a pull-request comment at any width; details sit under each contract.

    Failing rows come first. The spacer in the Contract header stretches the table across the comment and
    gives that column the slack, so the numbers stay narrow.
    """
    lines = [
        f"| | {SPREAD}Contract | Cases passed | {_term('Mutants caught')} |",
        "| :-: | :-- | --: | --: |",
    ]
    for row in sorted(rows, key=lambda row: SEVERITY.index(row.status)):
        seed = f", seed {row.seed}" if row.seed is not None else ""
        covered = {True: "every action covered", False: "**an action never reached**", None: "coverage not run"}
        details = (
            f"{_plural(row.histories, 'history', 'histories')} ({_plural(row.witnesses, 'witness', 'witnesses')}, "
            f"{_plural(row.samples, 'sample')}{seed}) · {covered[row.covered]} · Quint {row.quint or 'not run'}"
        )
        lines.append(
            f"| {ICON[row.status]} | {_code(row.contract)} {_code(row.test)}<br><sub>{details}</sub> "
            f"| {row.passed}/{row.cases} | {row.mutants_detected}/{row.mutants} |"
        )
    return lines


def _directory(row: Row, repository: Path) -> str | None:
    """The receipt's rootdir relative to the repository, when the session ran inside it."""
    if not row.root:
        return None
    shown = display_path(Path(row.root), repository)
    return None if Path(shown).is_absolute() else shown


def _reproduce(row: Row, failure: Failure, repository: Path) -> str:
    """The command that reruns exactly this case; it regenerates the same histories from the manifest's seed."""
    # Run from the session's rootdir; a configuration chosen with -c must be named again.
    config = f"-c {shlex.quote(row.config_file)} " if row.config_file else ""
    command = f"pytest {config}" + shlex.quote(f"{row.test}[{failure.case}]")
    directory = _directory(row, repository)
    if directory and directory != ".":
        command = f"cd {shlex.quote(directory)}\n{command}"
    return command.replace("```", "'''")


def _clearest(failures: list[Failure]) -> Failure:
    """The failure to read first: a named witness before a random sample, then the shortest path to it."""

    def rank(failure: Failure) -> tuple[int, int, str]:
        state = failure.finding.get("state")
        return (
            0 if failure.case.startswith("witness-") else 1,
            state if isinstance(state, int) else 10**9,
            failure.case,
        )

    return min(failures, key=rank)


_DIFFERENCE = re.compile(r"^(?P<field>.*?): (?P<reason>[^;]*); expected: (?P<expected>.*?); actual: (?P<actual>.*)$")


def _differences(finding: dict[str, Any]) -> list[str]:
    """What the code observed against what the model expects, one row per differing field."""
    rows, loose = [], []
    for line in finding.get("differences", []):
        match = _DIFFERENCE.match(str(line))
        if match:
            rows.append(
                f"| {_code(match['field'] or '(observation)')} | {_code(match['expected'])} | {_code(match['actual'])} |"
            )
        else:
            loose.append(f"- {_code(line)}")
    lines = [f"| {SPREAD}Field | Model expects | Code observed |", "| :-- | :-- | :-- |", *rows, ""] if rows else []
    lines += loose
    extra = int(finding.get("difference_count", 0)) - len(rows) - len(loose)
    if extra > 0:
        lines.append(f"<sub>…and {_plural(extra, 'more difference')}.</sub>")
    if finding.get("mismatch"):
        lines.append(f"<sub>Full mismatch in the run's artifact: {_code(finding['mismatch'])}</sub>")
    return [*lines, ""]


def _what(failure: Failure) -> str:
    """One line: where this history left the model."""
    finding = failure.finding
    if "survived" in finding:
        return f"the mutant (a planted fake bug) survived all {finding['survived']} histories; no observation differed from the model"
    if "state" in finding:
        action = _call(finding.get("action"), finding.get("arguments"))
        return f"the code left the model at state {finding['state']}, after {action}"
    return "failed; see the job log"


def _failure(row: Row, failure: Failure, repository: Path, *, command: bool = True) -> list[str]:
    """One failing history in full: where it diverged, each differing field, and the command that reruns it
    unless the alert above already gave it."""
    lines = [f"**{_code(row.contract)}** · {_code(failure.case)}: {_what(failure)}.", ""]
    if "state" in failure.finding:
        lines += _differences(failure.finding)
    if command:
        lines += ["```bash", _reproduce(row, failure, repository), "```", ""]
    return lines


def _alerted(rows: list[Row]) -> Failure | None:
    """The failure the alert tells a person to start with: the first failing test's clearest one."""
    failing = [row for row in rows if row.failures]
    return _clearest(failing[0].failures) if failing else None


def _failures(rows: list[Row], repository: Path) -> list[str]:
    """Per contract test: the clearest failure open, the rest folded to one line and a command each."""
    alerted = _alerted(rows)
    lines: list[str] = []
    shown = 0
    for row in rows:
        if not row.failures or shown >= MAX_FAILURES:
            continue
        first = _clearest(row.failures)
        lines += _failure(row, first, repository, command=first is not alerted)
        others = [failure for failure in row.failures if failure is not first][: MAX_FAILURES - shown - 1]
        shown += 1 + len(others)
        if others:
            body = []
            for failure in others:
                body += [f"- {_code(failure.case)}: {_what(failure)}.", "", "  ```bash"]
                body += [f"  {line}" for line in _reproduce(row, failure, repository).splitlines()] + ["  ```", ""]
            summary = (
                f"{_plural(len(others), 'more failing history', 'more failing histories')} in {_code_html(row.test)}"
            )
            lines += _collapsible(summary, body)
    total = sum(len(row.failures) for row in rows)
    if total > shown:
        lines += [f"…and {total - shown} more; every mismatch is in the run's artifact.", ""]
    return lines


def _code_html(text: object) -> str:
    """Inline code inside HTML, such as a ``<summary>``, where markdown backticks do not render."""
    return f"<code>{escape(' '.join(str(text).split()))}</code>"


_RESTATED = re.compile(r"^(?P<test>.+?): \d+ case\(s\) did not run or did not pass: (?P<cases>\[.*\])$")


def _new_reasons(evidence: Evidence) -> list[str]:
    """Why the run is incomplete, without the lines that only restate cases already shown as failed."""
    failed = {(row.test, failure.case) for row in evidence.rows for failure in row.failures}
    reasons = []
    for line in evidence.incomplete:
        match = _RESTATED.match(line)
        if match:
            try:
                cases = ast.literal_eval(match["cases"])
            except (ValueError, SyntaxError):
                cases = None
            if isinstance(cases, list) and all((match["test"], case) in failed for case in cases):
                continue
        reasons.append(line)
    return reasons


def _alert(evidence: Evidence, repository: Path) -> list[str]:
    """One GitHub alert, before anything else, when the run needs a person: what is wrong, and what to run.

    A green run gets none. A red run gets a caution with the clearest failure's command; an incomplete one
    a warning saying why it is not evidence.
    """
    status = evidence.status
    if status is None or (status == "passed" and evidence.complete):
        return []
    if status == "failed":
        lines = ["> [!CAUTION]"]
        broken = [row for row in evidence.rows if row.generation_failure]
        failing = [row for row in evidence.rows if row.failures]
        if failing:
            diverged = sum(len(row.failures) for row in failing)
            contracts = ", ".join(dict.fromkeys(_code(row.contract) for row in failing))
            lines.append(f"> **{_plural(diverged, 'history', 'histories')} left the model in {contracts}.**")
            row = failing[0]
            first = _clearest(row.failures)
            lines += [">", f"> Start with {_code(first.case)}: {_what(first)}.", ">", "> ```bash"]
            lines += [f"> {line}" for line in _reproduce(row, first, repository).splitlines()] + ["> ```"]
        if broken:
            names = ", ".join(dict.fromkeys(_code(row.contract) for row in broken))
            lines += [*([">"] if failing else []), f"> **Quint could not generate histories for {names}.**"]
        if not failing and not broken:
            lines.append(f"> **{MEANING['failed'][0].upper()}{MEANING['failed'][1:]}.**")
        return [*lines, ""]
    lines = ["> [!WARNING]", "> **This run is not complete evidence: nothing failed, but not every case ran.**"]
    present = [state for state in SEVERITY if state != "passed" and any(row.status == state for row in evidence.rows)]
    for state in present:
        lines += [">", f"> {ICON[state]} **{state}**: {MEANING[state]}."]
    for reason in _new_reasons(evidence)[:5]:
        lines += [">", f"> {_code(reason, 300)}"]
    return [*lines, ""]


def _glossary() -> list[str]:
    """What the comment's words and numbers mean, folded at the bottom for whoever needs it."""
    body = [
        "- **History**: one run of the Quint model, replayed step by step against the code. *Witnesses* are "
        "named scenarios replayed exactly as written; *samples* are generated from the manifest's seed.",
        "- **Case**: one history, one mutant check, or the coverage check of a contract test. A contract passes "
        "only when every case ran and passed.",
        f"- **{_term('Mutant')}**: a planted fake bug. Each must make some history diverge, which proves the "
        "histories would notice that kind of bug.",
        "- **Real bug**: a bug in the code under test that a contract found, recorded in its manifest's "
        "`bugs_found` with the pull request that fixed it. A mutant puts each back, so every run re-checks it.",
        "- **Disagreement**: a history where the code reached a different state than the model, or a mutant no "
        "history caught. Crashes and setup errors are not counted. *Fixed* means a later push ran the case and "
        "it passed.",
        *(f"- {ICON[state]} **{state}**: {MEANING[state]}." for state in SEVERITY),
        "",
        f"Reproduce commands regenerate the same histories from the manifest's seed. [What a passing run proves]({LIMITS}).",
        "",
    ]
    return _collapsible("<strong>What do these words and numbers mean?</strong>", body)


def _missed(catch: Catch) -> str:
    """Why the receipt shows no history caught the mutant."""
    if catch.survived is not None:
        return f"survived all {_plural(catch.survived, 'history', 'histories')}"
    return "its case failed; see the job log" if catch.outcome == "failed" else "not run in this session"


def _mutant_cells(catch: Catch) -> tuple[bool, str, str]:
    """Whether it was caught, then the Caught by and At cells: the case and the step that first caught it,
    or that it was not caught and why."""
    mutation = catch.mutation
    if mutation is None:
        verdict = "➖ **not run**" if catch.outcome is None else "❌ **not caught**"
        return False, verdict, _missed(catch)
    call = _call(mutation.get("action"), mutation.get("arguments"))
    return True, _code(mutation.get("detected_by")), f"state {mutation.get('state')}, after {call}"


def _rechecked(catch: Catch | None) -> str:
    """What this run shows about a found bug: only what the receipt of the mutant that reintroduces it proves."""
    if catch is None:
        return "no mutant re-checks it"
    named = f"mutant {_code(catch.mutant)} reintroduces it"
    mutation = catch.mutation
    if mutation is None:
        return f"{named}: **{_missed(catch)}**"
    if "diverged" not in mutation:
        # A receipt from before the mutant replayed every history: it proves the catch, not a count.
        return f"{named}: caught by {_code(mutation.get('detected_by'))}"
    histories = _plural(int(mutation.get("histories", 0)), "history", "histories")
    return f"{named}: {len(mutation.get('diverged', []))} of {histories} diverge"


def _per_test(seen: dict[str, _Shown]) -> list[tuple[str | None, _Shown]]:
    """One entry when every test of a contract shows the same thing (no test named); otherwise one per test."""
    first, *rest = seen.values()
    return [(None, first)] if all(shown == first for shown in rest) else list(seen.items())


def _catch_of(row: Row, mutant: str) -> Catch | None:
    return next((catch for catch in row.catches if catch.mutant == mutant), None)


def _by_contract(rows: list[Row]) -> dict[str, list[Row]]:
    """Rows grouped by contract, in row order: several test functions can check one contract."""
    contracts: dict[str, list[Row]] = {}
    for row in rows:
        contracts.setdefault(row.contract, []).append(row)
    return contracts


def _capped(rows: list[str], limit: int) -> list[str]:
    """At most ``limit`` table rows, then a line that says how many the receipts hold beyond them."""
    if len(rows) <= limit:
        return rows
    return [*rows[:limit], "", f"…and {len(rows) - limit} more in the receipts."]


def _node(node: str) -> str:
    """A pytest node id in a table cell, small, with its file and its test on separate lines.

    Inline code never wraps inside a word, so a whole node id would set the column's minimum width and
    push a full-width table past the comment's edge.
    """
    path, separator, name = str(node).rpartition("::")
    lines = [_code(path), _code(separator + name)] if separator else [_code(node)]
    return f"<sub>{'<br>'.join(lines)}</sub>"


def _bugs_table(rows: list[Row], *, counted: bool = True) -> list[str]:
    """Bugs the contracts found earlier, as their manifests declare them, and what this run re-checks of each."""
    contracts = _by_contract(rows)
    found = _found_bugs(rows)
    if not found:
        return []
    lines = []
    for contract, bug in found:
        mutant = bug.get("mutant")
        seen = {row.test: _rechecked(_catch_of(row, mutant) if mutant else None) for row in contracts[contract]}
        checks = [text if test is None else f"{text} in {_code(test)}" for test, text in _per_test(seen)]
        facts = [f"found by {_text(bug['found_by'])}"] if bug.get("found_by") else []
        details = " · ".join([*facts, *checks])
        regression = _node(bug["regression_test"]) if bug.get("regression_test") else "—"
        lines.append(
            f"| {_code(contract)} | {_text(bug.get('summary')).rstrip('.')}<br><sub>{details}</sub> "
            f"| {_ref(bug.get('fixed_in'))} | {regression} |"
        )
    these = "these contracts" if len(contracts) > 1 else "this contract"
    table = [
        f"| Contract | {SPREAD}Bug | Fixed in | Regression test |",
        "| :-- | :-- | :-- | :-- |",
        *_capped(lines, MAX_FAILURES * 2),
        "",
    ]
    title = f"🐞 {_plural(len(found), 'real bug')} found by {these}" if counted else "🐞 Real bugs: what each was"
    return _collapsible(title if counted else f"{title}, and the test that pins it", table)


def _mutants_table(rows: list[Row], *, counted: bool = True) -> list[str]:
    """Every mutant: what it breaks and where it was caught, one row per bug and contract.

    A mutant that reintroduces a found bug is listed here too, and marked: the found bug's row says
    what it proves about that bug. Bugs any test did not catch come first, the tests that missed them first
    among their rows, and keep the block open.
    """
    missed: list[str] = []
    caught: list[str] = []
    bugs = uncaught = 0
    for contract, members in _by_contract(rows).items():
        reintroducing = {bug.get("mutant") for bug in members[0].bugs_found if bug.get("mutant")}
        for catch in members[0].catches:
            bugs += 1
            breaks = _text(catch.description).rstrip(".") if catch.description else "—"
            if catch.mutant in reintroducing:
                breaks += "<br><sub>reintroduces a real bug this contract found</sub>"
            per_test = _per_test({row.test: _mutant_cells(_catch_of(row, catch.mutant) or catch) for row in members})
            target = caught if all(hit for _, (hit, _, _) in per_test) else missed
            uncaught += target is missed
            for test, (_, by, at) in sorted(per_test, key=lambda item: item[1][0]):
                where = _code(contract) + (f"<br><sub>{_code(test)}</sub>" if test else "")
                target.append(f"| {where} | {_code(catch.mutant)} | {breaks} | {by} | {at} |")
    if not bugs:
        return []
    verdict = f"{uncaught} not caught" if uncaught else "all caught" if bugs > 1 else "caught"
    table = [
        f"| Contract | {_term('Mutant')} | {SPREAD}What it breaks | Caught by | At |",
        "| :-- | :-- | :-- | :-- | :-- |",
        *_capped(missed + caught, MAX_FAILURES),
        "",
    ]
    fake = "planted fake bug" if bugs == 1 else "planted fake bugs"
    title = (
        f"🧪 {_plural(bugs, 'mutant')} ({fake}), {verdict}"
        if counted
        else "🧪 Mutants: what each planted fake bug breaks, and which history caught it"
    )
    return _collapsible(title, table, expanded=bool(missed))


# A field one observation has and the other lacks; Quint records keep their fields, but receipts are data.
_ABSENT = object()


def _record(value: object) -> bool:
    """A Quint record as ``compare.plain`` writes it: not a variant, nor a tuple, set, map or unserializable."""
    return isinstance(value, dict) and set(value) != {"tag", "value"} and not any(key.startswith("#") for key in value)


def _changes(before: object, after: object, path: str = "") -> list[tuple[str, object, object]]:
    """The fields that differ between two observations, as ``(path, before, after)``, records opened field by field.

    Anything that is not a record (a list, variant, set, map or scalar) changes as a whole.
    """
    # dry-lint: distinct-from src/pytest_quint_connect/compare.py::differences
    # differences() judges decoded ITF against runtime data by Quint type rules; this lists, for readers, which
    # fields of the model's own observation one step changed, from receipt JSON, where no runtime value exists.
    if before == after:
        return []
    if not (_record(before) and _record(after)):
        return [(path or "observation", before, after)]
    assert isinstance(before, dict) and isinstance(after, dict)
    found: list[tuple[str, object, object]] = []
    for key in [*after, *(key for key in before if key not in after)]:
        found += _changes(before.get(key, _ABSENT), after.get(key, _ABSENT), f"{path}.{key}" if path else str(key))
    return found


def _changed(before: object, after: object) -> str:
    """One table cell: each changed field as ``path: before → after``, or that nothing changed."""
    changes = _changes(before, after)
    if not changes:
        return "unchanged"

    def shown_value(value: object) -> str:
        return "absent" if value is _ABSENT else _value(value)

    shown = [_code(f"{path}: {shown_value(old)} → {shown_value(new)}", 160) for path, old, new in changes[:MAX_CHANGES]]
    if len(changes) > MAX_CHANGES:
        shown.append(f"and {_plural(len(changes) - MAX_CHANGES, 'more field')}")
    return "<br>".join(shown)


def _steps(illustrated: dict[str, Any]) -> list[str]:
    """One witness as a table: the observation it starts from, then what each action changes in it.

    Showing whole observations would bury the change: a wide observation is cut at the same unchanged
    prefix in every row.
    """
    steps = list(illustrated.get("steps", []))
    lines = [
        f"**Witness {_code(illustrated.get('witness'))}, step by step**: the observation the model starts from, "
        "then what it expects each action to change.",
        "",
        "| State | Action | Model expects |",
        "| --: | :-- | :-- |",
    ]
    previous: object = None
    for index, step in enumerate(steps[:MAX_STEPS]):
        call = _call(step.get("action"), step.get("arguments"))
        observed = step.get("observed")
        expects = _code(_value(observed), 600) if index == 0 else _changed(previous, observed)
        lines.append(f"| {_code(step.get('state'))} | {call} | {expects} |")
        previous = observed
    lines.append("")
    if len(steps) > MAX_STEPS:
        lines += [f"…and {_plural(len(steps) - MAX_STEPS, 'more state')} in the receipt.", ""]
    return lines


def _says(rows: list[Row], *, expanded: bool = True) -> list[str]:
    """What each contract claims: its actions, invariant, witnesses, bounds and one witness's steps.

    A passing run proves the code agrees with the model; this is what a reviewer reads to judge the model.
    The README shows it open; the pull-request comment folds it below the actions a person must take.
    """
    lines: list[str] = []
    for contract, members in _by_contract(rows).items():
        claims = members[0].claims
        if claims is None:
            continue
        bounds = " · ".join(f"{key} {_code(value)}" for key, value in claims.bounds.items())
        witnesses = ", ".join(_code(name) for name in claims.witnesses) or "none"
        lines += _collapsible(
            f"What <code>{_text(contract, 120)}</code> says",
            [
                "**Actions**, and the code each one drives:",
                "",
                *(f"- {_code(action)}: {_text(boundary)}" for action, boundary in claims.actions.items()),
                "",
                f"**Invariant** {_code(claims.invariant)}, checked while sampling histories.",
                "",
                f"**Witnesses**, scenarios replayed exactly as written: {witnesses}.",
                "",
                f"**Bounds**: {bounds}.",
                "",
                *(_steps(claims.illustrated) if claims.illustrated else []),
            ],
            expanded=expanded,
        )
    return lines


def _overview(
    evidence: Evidence,
    *,
    green: bool,
    legend: bool = True,
    expanded: bool | None = None,
    counted: bool = True,
) -> list[str]:
    """The contract table, what each status means, then the bugs found and the mutants; the sticky and
    the README block share it.

    The table's summary carries the numbers, unless ``counted`` is false because a header above already
    gives them. When everything is green the table starts collapsed; when anything is red or incomplete it
    stays open, failing rows first.
    """
    if not evidence.rows:
        return []
    totals = Totals.of(evidence.rows)
    numbers = [
        _plural(totals.contracts, "contract"),
        f"{totals.passed}/{totals.cases} cases",
        *([totals.mutants_caught()] if totals.mutants else []),
        _plural(totals.histories, "history", "histories"),
    ]
    each = "Each contract test: its status, cases, histories and coverage"
    summary = f"{ICON[evidence.status or 'passed']} {' · '.join(numbers) if counted else each}"
    lines = _collapsible(summary, [*_table(evidence.rows), ""], expanded=not green if expanded is None else expanded)
    # The sticky explains statuses in its alert and glossary; the README block, which has neither, here.
    present = [state for state in SEVERITY if state != "passed" and any(row.status == state for row in evidence.rows)]
    if present and legend:
        lines.append("<br>".join(f"<sub>{ICON[state]} {state}: {MEANING[state]}.</sub>" for state in present))
        lines.append("")
    return [*lines, *_bugs_table(evidence.rows, counted=counted), *_mutants_table(evidence.rows, counted=counted)]


# GitHub sizes a comment's tables to their content. A wide spacer floated into each cell, which GitHub caps
# at the comment's width, stretches the table across the comment on any screen for one pixel of height.
# It floats right: GitHub pads a floated image on the side away from its float, and padding past the right
# edge would give the table a scrollbar. (The same trick as dry-lint's sticky.)
SPREAD = '<img width="2000" height="1" align="right" alt="">'


# Quinty the starfish stamps approval when every contract passed and traces the failing path when one
# failed; otherwise (filtered, replayed, interrupted, nothing ran) Quinty inspects the contract.
BANNERS = {
    "passed": ("-passed", "every history matched the model"),
    "failed": ("-failed", "the code left the model's path"),
}


def _banner(assets: str, status: str | None) -> list[str]:
    """The full-width banner, light or dark with the reader's theme, served from the action's own revision."""
    variant, words = BANNERS.get(status or "", ("", "contract report"))
    return [
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{assets}/sticky-banner{variant}-dark.svg">',
        f'  <img alt="pytest-quint-connect: {words}" src="{assets}/sticky-banner{variant}-light.svg" width="100%">',
        "</picture>",
        "",
    ]


# The header's numbers, in this order. Every count the comment gives is one of these, said once, with what it
# counts, so "case", "mutant", "real bug" and "disagreement" each keep one meaning (the glossary's).
SCOREBOARD = ("contracts", "cases passed", "mutants caught", "real bugs found", "disagreements in this PR")
GLYPH_WIDTH, GLYPH_HEIGHT = 14, 28


@dataclass(frozen=True)
class Score:
    """One header number: its text, its color (good, bad, warning or neutral), and what it counts."""

    text: str
    color: str
    label: str
    meaning: str


def _scores(evidence: Evidence, ledger: "Ledger | None" = None, pull: str = "") -> list[Score]:
    """The header's numbers, colored by pytest-obligation's rule: red only for a failure, amber for what did
    not run, green for what passed or was caught, plain for what is only counted."""
    rows = evidence.rows
    totals = Totals.of(rows)
    by_contract = {row.contract: row for row in rows}.values()
    witnesses, samples = sum(row.witnesses for row in by_contract), sum(row.samples for row in by_contract)
    failing = any(row.failures or row.generation_failure or row.covered is False for row in rows)
    cases_color = "good" if totals.passed == totals.cases else "bad" if failing else "warning"
    found = _found_bugs(rows)
    here = sum(1 for _, bug in found if _fixed_here(bug, pull))
    fixes = list(dict.fromkeys(_ref(bug.get("fixed_in")) for _, bug in found if not _fixed_here(bug, pull)))
    if not found:
        bugs = "bugs in the code under test that a contract found; none recorded"
    elif here == len(found):
        bugs = "found by a contract and fixed in this pull request; a mutant now puts each back"
    else:
        where = f"{here} in this pull request, " if here else ""
        bugs = f"found by a contract and fixed: {where}{', '.join(fixes)}; a mutant now puts each back"
    if ledger is not None:
        caught, fixed, still = len(ledger.caught), ledger.fixed, ledger.open
        across = f"across this pull request's {_plural(len(ledger.pushes), 'push', 'pushes')}"
    else:
        caught = still = sum(len(row.failures) for row in rows)
        fixed, across = 0, "in this run"
    return [
        Score(str(totals.contracts), "neutral", "contracts", "Quint models checked against the code"),
        Score(
            f"{totals.passed}/{totals.cases}",
            cases_color,
            "cases passed",
            f"{_plural(totals.histories, 'history', 'histories')} ({witnesses} witnesses, {samples} samples) "
            "replayed, plus mutant and coverage checks",
        ),
        Score(
            f"{totals.detected}/{totals.mutants}",
            "good" if totals.detected == totals.mutants else "bad",
            "mutants caught",
            "planted fake bugs; each must make some history disagree with the model",
        )
        if totals.mutants
        else Score("0", "warning", "mutants caught", "no planted fake bugs, so nothing shows the histories can fail"),
        Score(str(len(found)), "good" if found else "neutral", "real bugs found", bugs),
        Score(
            str(caught),
            "bad" if still else "good" if caught else "neutral",
            "disagreements in this PR",
            f"histories where the code reached a different state than the model, or mutants no history caught, "
            f"{across}: {fixed} fixed, {still} still failing",
        ),
    ]


def _glyph(base: str, name: str, alt: str = "") -> str:
    return (
        f'<picture><source media="(prefers-color-scheme: dark)" srcset="{base}{name}-dark.svg">'
        f'<img src="{base}{name}-light.svg" width="{GLYPH_WIDTH}" height="{GLYPH_HEIGHT}" '
        f'alt="{escape(alt, quote=True)}"></picture>'
    )


def _number(score: Score, base: str) -> str:
    """Large colored digits from the action's fixed glyphs; bold text without them. Only the first glyph
    carries the count as alt text, so a screen reader reads ``13/13 cases passed`` once, not digit by digit."""
    if not base:
        return f"**{score.text}**"
    slugs = ["slash" if char == "/" else char for char in score.text]
    alts = [f"{score.text} {score.label}"] + [""] * (len(slugs) - 1)
    return "".join(_glyph(base, f"{score.color}-{slug}", alt) for slug, alt in zip(slugs, alts, strict=True))


def _scoreboard(evidence: Evidence, base: str = "", ledger: "Ledger | None" = None, pull: str = "") -> list[str]:
    """The comment's header: each key number over its name and what it counts, spanning the comment."""
    scores = _scores(evidence, ledger, pull)
    return [
        "| " + " | ".join(_number(score, base) for score in scores) + " |",
        "| " + " | ".join(":-:" for _ in scores) + " |",
        "| " + " | ".join(f"{SPREAD}**{score.label}**<br><sub>{score.meaning}</sub>" for score in scores) + " |",
        "",
    ]


MAX_FIXED = 10


def _fixed(ledger: "Ledger") -> list[str]:
    """Which histories this pull request fixed, by name: a count alone does not say what got better."""
    fixed = [(key, entry) for key, entry in ledger.caught.items() if entry["state"] == "fixed"]
    if not fixed:
        return []
    lines = []
    for key, entry in sorted(fixed, key=lambda item: (item[1]["found"], item[0]))[:MAX_FIXED]:
        contract, test, case = (key.split(" · ", 2) + ["", ""])[:3]
        what = "a mutant no history caught" if entry.get("kind") == "survived" else "the code left the model"
        when = f"caught in push {entry['found']}" + (f", fixed in push {entry['fixed']}" if entry.get("fixed") else "")
        lines.append(f"- ✅ {_code(contract)} {_code(case)} in {_code(test)}: {what}; {when}.")
    if len(fixed) > MAX_FIXED:
        lines.append(f"- …and {len(fixed) - MAX_FIXED} more.")
    return [f"**Fixed in this PR** ({_plural(len(fixed), 'history', 'histories')}):", "", *lines, ""]


# Where render_comment places the claims, so it can leave them out when the comment would not fit.
CLAIMS = "<!-- pytest-quint-connect:claims -->"


def render_comment(
    evidence: Evidence,
    *,
    repository: Path,
    title: str = "",
    head: str = "",
    run_url: str = "",
    readme_note: str = "",
    assets: str = "",
    ledger: "Ledger | None" = None,
    pull: str = "",
) -> str:
    """The pull-request sticky: what ran, what failed, and the command that reproduces each failure.

    ``assets`` is the URL of the action's own ``docs/assets`` when its banner exists; ``ledger`` is this
    pull request's history, rendered as the headline tally and carried forward as hidden JSON; ``pull`` is
    its number, so a bug this pull request fixes is credited to it.
    """
    status = evidence.status
    lines = _banner(assets, status) if assets else []
    lines.append(f"### {ICON.get(status or '', '➖')} Quint contracts: {_verdict(evidence)}")
    if title:
        lines.append(f"<sub>{_code(title, 120)}</sub>")
    lines.append("")
    # Top to bottom: what a person must do, the evidence for it, what this pull request changed, then every
    # detail folded. A green run has nothing to do, so its found-bug credit leads; a red one's follows the failures.
    lines += _alert(evidence, repository)
    # The key numbers, once, each with what it counts; everything below names items, not new counts.
    if evidence.rows:
        lines += _scoreboard(evidence, f"{assets}/score/" if assets else "", ledger, pull)
    green = _green(evidence, ledger)
    if green:
        lines += _credit(evidence.rows, pull)
    failures = [(row, failure) for row in evidence.rows for failure in row.failures]
    generation = [row for row in evidence.rows if row.generation_failure]
    if failures or generation:
        lines += ["#### What failed", ""]
        for row in {row.contract: row for row in generation}.values():
            reason = _code(row.generation_failure, 400)
            lines += [f"**{_code(row.contract)}**: Quint could not generate histories: {reason}", ""]
        lines += _failures(evidence.rows, repository)
    # A failed run's alert names its failures, an incomplete one's its reasons: list only what neither says.
    reasons = _new_reasons(evidence) if evidence.status == "failed" else []
    if reasons:
        lines += ["#### Not complete", ""]
        lines += [f"- {_code(line, 300)}" for line in reasons[:MAX_FAILURES]]
        lines.append("")
    if readme_note:
        lines += [readme_note, ""]
    if not green:
        lines += _credit(evidence.rows, pull)
    # The header counts this pull request's disagreements; here, the histories it fixed, by name.
    if ledger is not None:
        lines += _fixed(ledger)
    weak = [row for row in evidence.rows if row.witnesses_only]
    if weak:
        lines += [f"<sub>⚠️ {_code(row.test)}: {WITNESSES_ONLY}.</sub>" for row in weak] + [""]
    # Details, all folded: the contract table with bugs and mutants, what each contract claims (the first thing
    # left out to fit the budget) and the glossary.
    lines += _overview(evidence, green=green, legend=False, expanded=False, counted=False)
    lines.append(CLAIMS)
    lines += _glossary()
    footer = [_plural(evidence.receipts, "receipt")]
    if head:
        footer.append(f"head {_code(head[:12])}")
    if run_url:
        footer.append(f"[run]({run_url})")
    footer.append(f"[what a passing run proves]({LIMITS})")
    lines.append(f'<sub>{" · ".join(footer)} · <a href="{HOME}">pytest-quint-connect</a></sub>')
    hidden = f"\n{ledger.comment()}\n" if ledger is not None else ""
    at = lines.index(CLAIMS)
    body = "\n".join([*lines[:at], *_says(evidence.rows, expanded=False), *lines[at + 1 :]]) + "\n"
    if len(body) + len(hidden) > COMMENT_BUDGET:
        # A cut inside a collapsed block would hide the footer in it: leave the claims out before cutting.
        body = "\n".join([*lines[:at], *lines[at + 1 :]]) + "\n"
    if len(body) + len(hidden) > COMMENT_BUDGET:
        body = body[: COMMENT_BUDGET - 300 - len(hidden)]
        # Close any block the cut fell inside, so the note below it is not hidden in a collapsed section.
        unclosed = body.count("<details") - body.count("</details>")
        body += "\n\n" + "</details>\n" * unclosed + "\n…truncated; the full report is in the run's artifact.\n"
    return body + hidden


def _badge(label: str, message: str, color: str) -> str:
    def part(text: str) -> str:
        return urllib.parse.quote(text.replace("-", "--").replace("_", "__").replace(" ", "_"), safe="")

    return f"https://img.shields.io/badge/{part(label)}-{part(message)}-{color}"


COUNTER = "https://pytest-quint-connect-counter.gigaverse-oss.workers.dev"
_SIGN = re.compile(r"/v1/sign\.svg\?key=([0-9a-f]{64})&amp;name=([^\"&]*)")


@dataclass(frozen=True)
class Sign:
    """A repository's sign on the global counter: its key (the SHA-256 of its numeric id) and the name it shows."""

    key: str
    name: str

    @classmethod
    def of(cls, repository_id: str, name: str) -> "Sign":
        return cls(hashlib.sha256(f"github-repository:{repository_id}".encode()).hexdigest(), name)

    @classmethod
    def found(cls, document: str) -> "Sign | None":
        """The sign an existing block already shows, so a local refresh keeps it without the repository id."""
        match = _SIGN.search(document)
        return cls(match.group(1), urllib.parse.unquote(match.group(2))) if match else None

    def picture(self) -> str:
        query = f"key={self.key}&amp;name={urllib.parse.quote(self.name, safe='')}"
        alt = f"What pytest-quint-connect's contracts caught in {self.name}'s pull requests"
        return (
            f'<a href="{HOME}#impact"><picture>'
            f'<source media="(prefers-color-scheme: dark)" srcset="{COUNTER}/v1/sign.svg?{query}&amp;theme=dark">'
            f'<img alt="{escape(alt, quote=True)}" src="{COUNTER}/v1/sign.svg?{query}" width="100%">'
            "</picture></a>"
        )


def render_readme(evidence: Evidence, sign: Sign | None = None) -> str:
    """The README block: the same table, with nothing that differs between two runs of the same evidence.

    With ``sign``, the block opens with the repository's live sign from the global counter: what its
    contracts caught in pull requests. The sign's address never changes, so neither does the block.
    """
    contracts = sorted({row.contract for row in evidence.rows})
    totals = Totals.of(evidence.rows)
    status = evidence.status or "none"
    color = {"passed": "brightgreen", "failed": "red"}.get(status, "yellow")
    message = (
        f"{_plural(totals.contracts, 'contract')} {status} · {_plural(totals.histories, 'history', 'histories')}"
        f" · {totals.mutants_caught()}"
    )
    alt = f"Quint contracts: {message}"
    return "\n".join(
        [
            f"<!-- pytest-quint-connect:contracts {','.join(contracts)} -->",
            *([sign.picture(), ""] if sign else []),
            f'<a href="{HOME}" title="{MUTANTS_TIP}"><img alt="{alt}" src="{_badge("Quint contracts", message, color)}"></a>',
            "",
            *_overview(evidence, green=_green(evidence)),
            *_says(evidence.rows),
            f"<sub>Generated by [pytest-quint-connect]({HOME}) from contract receipts; "
            f"[what a passing run proves]({LIMITS}).</sub>",
        ]
    )


def embed(document: str, block: str) -> str:
    """``document`` with the text between its markers replaced by ``block``; text outside is untouched."""
    if document.count(START) != 1 or document.count(END) != 1 or document.index(START) > document.index(END):
        raise ReportError(f"the document must contain {START} once, followed by {END} once")
    before, _, rest = document.partition(START)
    _, _, after = rest.partition(END)
    return f"{before}{START}\n{block.strip()}\n{END}{after}"


def has_markers(document: str) -> bool:
    return START in document or END in document


@dataclass(frozen=True)
class ReadmeCheck:
    """``current``, ``stale`` (the diff says how), or ``unverifiable`` (this run lacks the evidence to say)."""

    state: str
    reason: str = ""
    diff: str = ""


def check_readme(
    document: str, evidence: Evidence, *, name: str = "README.md", sign: Sign | None = None
) -> ReadmeCheck:
    expected = embed(document, render_readme(evidence, sign)).replace("\r\n", "\n")
    current = document.replace("\r\n", "\n")
    if expected == current:
        return ReadmeCheck("current")
    listed = _LISTED.search(current)
    previous = set(filter(None, listed.group(1).split(","))) if listed else set()
    missing = sorted(previous - {row.contract for row in evidence.rows})
    if not evidence.complete or missing:
        why = (
            f"contracts {', '.join(missing)} did not run"
            if missing
            else "not every contract ran every case and passed in this run"
        )
        return ReadmeCheck("unverifiable", why)
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True), expected.splitlines(keepends=True), name, f"{name} (from receipts)"
        )
    )
    return ReadmeCheck("stale", "the evidence changed since the block was generated", diff)
