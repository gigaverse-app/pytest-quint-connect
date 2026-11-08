"""Render pytest-quint-connect receipts as a pull-request comment and a README block.

Both views are built from the same rows, read only from ``results.json`` receipts, so a report
never claims more than the sessions that wrote them. The README block leaves out the run, the
commit and every failure detail, so it changes only when the evidence changes: a committed block
can be checked against a fresh run the way ``codegen --check`` checks a generated driver.
"""

import difflib
import glob
import json
import re
import shlex
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contract import display_path

if TYPE_CHECKING:
    from .ledger import Ledger

START = "<!-- pytest-quint-connect:start -->"
END = "<!-- pytest-quint-connect:end -->"
_LISTED = re.compile(r"<!-- pytest-quint-connect:contracts (.*?) -->")
HOME = "https://github.com/gigaverse-app/pytest-quint-connect"
LIMITS = f"{HOME}/blob/main/docs/limits.md"
# GitHub refuses comments over 65,536 characters; leave room for the publisher's trailer.
COMMENT_BUDGET = 60_000
MAX_FAILURES = 20

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
    failures: list[Failure] = field(default_factory=list)
    passed_cases: frozenset[str] = frozenset()

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
                    failures=[Failure(case, outcomes[case], findings.get(case, {})) for case in sorted(failed)],
                    passed_cases=frozenset(case for case, outcome in outcomes.items() if outcome == "passed"),
                )
            )
    return rows


def _code(text: object, limit: int = 200) -> str:
    """Inline code safe inside a table cell, whatever the receipt contains."""
    value = " ".join(str(text).split()).replace("`", "'").replace("|", "\\|")
    return f"`{value if len(value) <= limit else value[: limit - 1] + '…'}`"


def _plural(count: int, word: str, plural: str = "") -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _headline(evidence: Evidence) -> str:
    """``2 contracts · 1 test failed, 1 passed``: contract tests per status, worst first."""
    if not evidence.rows:
        return "no contract ran in this run"
    counts = [(status, count) for status in SEVERITY if (count := sum(row.status == status for row in evidence.rows))]
    # Only the first count names its unit: "1 test failed, 1 passed".
    parts = [f"{_plural(counts[0][1], 'test')} {counts[0][0]}"] + [f"{count} {status}" for status, count in counts[1:]]
    contracts = len({row.contract for row in evidence.rows})
    return f"{_plural(contracts, 'contract')} · {', '.join(parts)}"


def _table(rows: list[Row]) -> list[str]:
    """Four columns, so the table fits a pull-request comment at any width; details sit under each contract."""
    lines = [
        "| | Contract | Cases passed | Mutants caught |",
        "| :-: | :-- | --: | --: |",
    ]
    for row in rows:
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


def _failure(row: Row, failure: Failure, repository: Path) -> list[str]:
    finding = failure.finding
    lines = [f"**{_code(row.contract)}** · {_code(failure.case)}"]
    if "survived" in finding:
        survived = finding["survived"]
        lines.append(f": the mutant survived all {survived} histories; no observation differed from the model.")
    elif "state" in finding:
        arguments = ", ".join(f"{key}={value!r}" for key, value in dict(finding.get("arguments", {})).items())
        action = _code(f"{finding.get('action')}({arguments})")
        lines.append(f": observed state differs from the model at state {finding['state']}, after {action}")
        details = [f"\n- {_code(line)}" for line in finding.get("differences", [])]
        extra = int(finding.get("difference_count", 0)) - len(details)
        if extra > 0:
            details.append(f"\n- and {_plural(extra, 'more difference')}")
        if finding.get("mismatch"):
            details.append(f"\n\n<sub>Full mismatch in the run's artifact: {_code(finding['mismatch'])}</sub>")
        lines += details
    else:
        lines.append(": failed; see the job log.")
    command = "pytest " + shlex.quote(f"{row.test}[{failure.case}]")
    directory = _directory(row, repository)
    if directory and directory != ".":
        command = f"cd {shlex.quote(directory)}\n{command}"
    command = command.replace("```", "'''")
    return [
        "".join(lines),
        "",
        "Reproduce (regenerates the same histories from the manifest's seed):",
        "",
        "```bash",
        command,
        "```",
        "",
    ]


# GitHub sizes a comment's tables to their content. A wide spacer floated into each cell, which GitHub caps
# at the comment's width, stretches the table across the comment on any screen for one pixel of height.
# It floats right: GitHub pads a floated image on the side away from its float, and padding past the right
# edge would give the table a scrollbar. (The same trick as dry-lint's sticky.)
SPREAD = '<img width="2000" height="1" align="right" alt="">'


def _banner(assets: str) -> list[str]:
    """The full-width banner, light or dark with the reader's theme, served from the action's own revision."""
    return [
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{assets}/sticky-banner-dark.svg">',
        f'  <img alt="pytest-quint-connect" src="{assets}/sticky-banner-light.svg" width="100%">',
        "</picture>",
        "",
    ]


def _tally(ledger: "Ledger") -> list[str]:
    """This pull request's disagreements across its pushes, as a table that spans the comment."""
    caught = len(ledger.caught)
    cells = [f"{SPREAD}**{count}**" for count in (caught, ledger.fixed, ledger.open)]
    pushes = len(ledger.pushes)
    return [
        "| 🐞 Disagreements caught | ✅ Fixed in this PR | ❗ Still failing |",
        "| :--: | :--: | :--: |",
        f"| {' | '.join(cells)} |",
        "",
        f"<sub>Across {_plural(pushes, 'push', 'pushes')}. A disagreement is a history where the code reached a "
        "different state than the model, or a mutant no history caught; crashes and setup errors are not "
        "counted.</sub>",
        "",
    ]


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
) -> str:
    """The pull-request sticky: what ran, what failed, and the command that reproduces each failure.

    ``assets`` is the URL of the action's own ``docs/assets`` when its banner exists; ``ledger`` is this
    pull request's history, rendered as the headline tally and carried forward as hidden JSON.
    """
    status = evidence.status
    lines = _banner(assets) if assets else []
    lines.append(f"### {ICON.get(status or '', '➖')} Quint contracts: {_headline(evidence)}")
    if title:
        lines.append(f"<sub>{_code(title, 120)}</sub>")
    lines.append("")
    if ledger is not None:
        lines += _tally(ledger)
    if evidence.rows:
        lines += _table(evidence.rows)
        lines.append("")
    present = [state for state in SEVERITY if state != "passed" and any(row.status == state for row in evidence.rows)]
    if present:
        lines.append("<br>".join(f"<sub>{ICON[state]} {state}: {MEANING[state]}.</sub>" for state in present))
        lines.append("")
    failures = [(row, failure) for row in evidence.rows for failure in row.failures]
    generation = [row for row in evidence.rows if row.generation_failure]
    if failures or generation:
        lines += ["#### What failed", ""]
        for row in {row.contract: row for row in generation}.values():
            reason = _code(row.generation_failure, 400)
            lines += [f"**{_code(row.contract)}**: Quint could not generate histories: {reason}", ""]
        for row, failure in failures[:MAX_FAILURES]:
            lines += _failure(row, failure, repository)
        if len(failures) > MAX_FAILURES:
            lines += [f"…and {len(failures) - MAX_FAILURES} more; every mismatch is in the run's artifact.", ""]
    if evidence.incomplete:
        lines += ["#### Not complete", ""]
        lines += [f"- {_code(line, 300)}" for line in evidence.incomplete[:MAX_FAILURES]]
        lines.append("")
    if readme_note:
        lines += [readme_note, ""]
    footer = [_plural(evidence.receipts, "receipt")]
    if head:
        footer.append(f"head {_code(head[:12])}")
    if run_url:
        footer.append(f"[run]({run_url})")
    footer.append(f"[what a passing run proves]({LIMITS})")
    lines.append(f'<sub>{" · ".join(footer)} · <a href="{HOME}">pytest-quint-connect</a></sub>')
    body = "\n".join(lines) + "\n"
    hidden = f"\n{ledger.comment()}\n" if ledger is not None else ""
    if len(body) + len(hidden) > COMMENT_BUDGET:
        body = (
            body[: COMMENT_BUDGET - 200 - len(hidden)] + "\n\n…truncated; the full report is in the run's artifact.\n"
        )
    return body + hidden


def _badge(label: str, message: str, color: str) -> str:
    def part(text: str) -> str:
        return urllib.parse.quote(text.replace("-", "--").replace("_", "__").replace(" ", "_"), safe="")

    return f"https://img.shields.io/badge/{part(label)}-{part(message)}-{color}"


def render_readme(evidence: Evidence) -> str:
    """The README block: the same table, with nothing that differs between two runs of the same evidence."""
    contracts = sorted({row.contract for row in evidence.rows})
    detected = sum(row.mutants_detected for row in evidence.rows)
    mutants = sum(row.mutants for row in evidence.rows)
    # Several test functions can replay one contract's histories (an implementation and its refactor);
    # count those histories once. Mutant checks stay per test function: each runs against its own driver.
    histories = sum({row.contract: row.histories for row in evidence.rows}.values())
    status = evidence.status or "none"
    color = {"passed": "brightgreen", "failed": "red"}.get(status, "yellow")
    message = (
        f"{_plural(len(contracts), 'contract')} {status} · {_plural(histories, 'history', 'histories')}"
        f" · {detected}/{mutants} mutants caught"
    )
    alt = f"Quint contracts: {message}"
    return "\n".join(
        [
            f"<!-- pytest-quint-connect:contracts {','.join(contracts)} -->",
            f'<a href="{HOME}"><img alt="{alt}" src="{_badge("Quint contracts", message, color)}"></a>',
            "",
            *_table(evidence.rows),
            "",
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


def check_readme(document: str, evidence: Evidence, *, name: str = "README.md") -> ReadmeCheck:
    expected = embed(document, render_readme(evidence)).replace("\r\n", "\n")
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
