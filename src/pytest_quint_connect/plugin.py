"""pytest integration: one parametrized test per declaration, one case per Quint history.

A consumer declares a contract in a test module::

    from pytest_quint_connect import quint_contract

    test_cache = quint_contract("contracts/lru/quint-connect.json", driver="lru_driver")

and supplies a fixture ``lru_driver`` that returns a driver factory. pytest then
collects ``test_cache[witness-NAME]``, ``test_cache[sample-N]``,
``test_cache[mutant-NAME]`` and ``test_cache[coverage]``. Several test functions may
declare the same contract (for example one per implementation); its traces are
generated once per session and shared.
"""

import json
import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from .compare import plain
from .contract import Contract, ContractError, display_path, load_contract
from .itf import ItfError, load_trace
from .quint import Generation, QuintError, QuintTool, generate, resolve_quint
from .replay import DriverFactory, ReplayMismatch, TraceError, replay_trace, steps

__all__ = ["quint_contract"]

_MARK = "__quint_contract__"
RECEIPT = "results.json"

WITNESSES_ONLY = (
    "every mutant was caught only by a named witness; no sampled history caught one. Raise bounds.n_traces "
    "or max_steps until samples catch them too, or accept knowingly that sampling adds no detection here"
)


def witnesses_only(mutations: dict[str, Any], samples: int) -> bool:
    """True when sampling ran and caught none of the detected mutants: only the named witnesses did.

    This module writes ``caught_by_sample`` into each receipt, and owns its reading too: the terminal
    summary and ``report`` both ask here. Receipts written before the field existed lack it, and say
    nothing either way.
    """
    return (
        samples > 0 and bool(mutations) and all(entry.get("caught_by_sample") is False for entry in mutations.values())
    )


@dataclass(frozen=True)
class _Declaration:
    manifest: Path
    driver: str


@dataclass(frozen=True)
class Case:
    """One parametrized case of a contract test."""

    contract: Contract
    kind: str  # witness | sample | mutant | coverage | replay
    name: str
    test: str  # node id of the declaring test function, without the case

    @property
    def id(self) -> str:
        return "coverage" if self.kind == "coverage" else f"{self.kind}-{self.name}"


def quint_contract(manifest: str | os.PathLike[str], *, driver: str = "quint_driver") -> Callable[..., None]:
    """Declare a Quint contract test. Assign the result to a ``test_*`` name in a test module.

    ``manifest`` is relative to the declaring module's directory. ``driver`` names a
    fixture that returns a factory ``(HistoryContext) -> Driver``. The factory is
    called once per history, so every history starts from fresh state, and the
    plugin closes each driver when its history ends.
    """

    def test(quint_case: Case, request: pytest.FixtureRequest) -> None:
        factory = request.getfixturevalue(driver)
        if not callable(factory):
            raise TypeError(f"fixture {driver!r} must return a driver factory, got {type(factory).__name__}")
        request.config.stash[_STATE].run_case(quint_case, factory, request.node)

    setattr(test, _MARK, _Declaration(Path(manifest), driver))
    test.__doc__ = f"Replay Quint histories of {manifest} through driver fixture {driver!r}."
    return pytest.mark.quint_contract(test)


@dataclass
class _TestRun:
    """Results of one declaring test function."""

    contract: Contract
    test: str
    outcomes: dict[str, str] = field(default_factory=dict)
    steps: dict[str, int] = field(default_factory=dict)
    mutations: dict[str, Any] = field(default_factory=dict)
    imported: dict[str, str] = field(default_factory=dict)
    collected: list[str] = field(default_factory=list)
    # Why a case failed, for reports: an observed mismatch, or a mutant that survived every history.
    findings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def missing(self) -> list[str]:
        return [case for case in self.contract.case_ids() if self.outcomes.get(case) != "passed"]


class _Session:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.root = Path(str(config.rootpath))
        artifacts = config.getoption("quint_artifacts") or config.getini("quint_artifacts")
        self.artifacts = (self.root / artifacts).resolve()
        self.replay_path: str | None = config.getoption("quint_replay")
        self.replay_mutant: str | None = config.getoption("quint_mutant")
        self.require_complete = bool(config.getoption("quint_require_complete"))
        self.required = [name for name in config.getini("quint_required_contracts") if name]
        self.timeout = float(config.getini("quint_timeout"))
        # The configuration file named with -c, relative to rootdir; None when pytest found it itself.
        explicit = config.getoption("inifilename", None) and config.inipath
        self.config_file = display_path(explicit, self.root) if explicit else None
        self.contracts: dict[str, Contract] = {}
        self.generations: dict[str, Generation] = {}
        self.failures: dict[str, Exception] = {}
        self.runs: dict[str, _TestRun] = {}
        self.tool: QuintTool | None = None
        self.tool_error: Exception | None = None
        self.incomplete: list[str] = []
        self.quint_note: str | None = None

    def note(self, line: str) -> None:
        """Which Quint this session uses, for the summary; a first install, which can take a minute, also now."""
        self.quint_note = line
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None and line.startswith("installing"):
            reporter.write_line(f"pytest-quint-connect: {line}")

    @property
    def mode(self) -> str:
        return "replay-only" if self.replay_path else "generate-and-replay"

    def configured_quint(self) -> str | None:
        return self.config.getoption("quint") or self.config.getini("quint_executable") or os.environ.get("QUINT")

    def register(self, contract: Contract, test: str) -> None:
        known = self.contracts.setdefault(contract.name, contract)
        if known.manifest != contract.manifest:
            raise ContractError(
                f"two manifests declare contract {contract.name!r}: {known.manifest} and {contract.manifest}"
            )
        self.runs.setdefault(test, _TestRun(known, test))

    def generation(self, contract: Contract) -> Generation:
        """Generate this contract's traces once per session; a failure is remembered and re-raised."""
        if contract.name in self.failures:
            raise self.failures[contract.name]
        if contract.name not in self.generations:
            try:
                if self.tool is None:
                    if self.tool_error is not None:
                        raise self.tool_error
                    try:
                        self.tool = resolve_quint(
                            contract.quint_version, self.artifacts / "_toolchain", self.configured_quint(), self.note
                        )
                    except Exception as error:
                        self.tool_error = error
                        raise
                if self.tool.version != contract.quint_version:
                    raise QuintError(
                        f"{contract.name} requires Quint {contract.quint_version}; this session uses {self.tool.version}"
                    )
                self.generations[contract.name] = generate(
                    contract, self.artifacts / contract.name, self.tool, timeout=self.timeout
                )
            except Exception as error:
                self.failures[contract.name] = error
                raise
        return self.generations[contract.name]

    def reproduce(self, case: Case, trace: Path, mutant: str | None) -> str:
        """A command that replays ``trace`` as printed, from the directory pytest was invoked in."""
        invoked = self.config.invocation_params.dir
        words = ["pytest"]
        if self.config_file is not None:
            # An explicit -c chose the configuration (and so the rootdir); without it the node does not resolve.
            words += ["-c", display_path(self.root / self.config_file, invoked)]
        path, separator, function = case.test.partition("::")
        words += [display_path(self.root / path, invoked) + separator + function]
        words += ["--quint-replay", display_path(trace, invoked)]
        if mutant:
            words += ["--quint-mutant", mutant]
        return shlex.join(words)

    def run_case(self, case: Case, factory: DriverFactory, item: pytest.Item) -> None:
        run = self.runs[case.test]
        work = self.artifacts / "_work"
        if case.kind == "replay":
            trace = Path(self.replay_path or "").resolve()
            item.user_properties.append(("quint_trace", str(trace)))
            result = replay_trace(
                trace,
                case.contract,
                factory,
                mutant=self.replay_mutant,
                work_base=work,
                reproduce=self.reproduce(case, trace, self.replay_mutant),
            )
            run.steps[case.id] = len(result.actions)
            return
        generation = self.generation(case.contract)
        if case.kind in ("witness", "sample"):
            trace = generation.traces[case.id]
            item.user_properties.append(("quint_trace", str(trace)))
            item.user_properties.append(("quint_seed", case.contract.bounds.seed))
            try:
                result = replay_trace(
                    trace, case.contract, factory, work_base=work, reproduce=self.reproduce(case, trace, None)
                )
            except ReplayMismatch as mismatch:
                saved = self.save_mismatch(case, mismatch)
                run.findings[case.id] = {
                    "state": mismatch.state,
                    "action": mismatch.action,
                    "arguments": mismatch.as_json()["arguments"],
                    # "entries[0].key: values differ; expected: 'b'; actual: 'a'"
                    "differences": [
                        "; ".join(" ".join(line.split()) for line in difference.render().splitlines())
                        for difference in mismatch.diff[:3]
                    ],
                    "difference_count": len(mismatch.diff),
                    "mismatch": display_path(saved, self.root),
                }
                raise
            run.steps[case.id] = len(result.actions)
            run.imported.update(result.imported)
            return
        if case.kind == "mutant":
            self.run_mutant(case, generation, factory, item)
            return
        if case.kind == "coverage":
            reached: set[str] = set()
            for trace in generation.traces.values():
                document = json.loads(trace.read_text(encoding="utf-8"))
                reached.update(_action_of(state, case.contract) for state in document["states"])
            missing = sorted(set(case.contract.actions) - reached)
            if missing:
                pytest.fail(
                    f"{case.contract.name}: declared actions never reached by any generated history: {missing}",
                    pytrace=False,
                )
            return
        raise AssertionError(f"unknown case kind {case.kind}")

    def run_mutant(self, case: Case, generation: Generation, factory: DriverFactory, item: pytest.Item) -> None:
        """Replay histories through the mutant until one is caught, or through all if it reintroduces a found bug.

        Only an observed-state mismatch counts as detection. Any other exception (a setup error, a
        crash in the mutant) propagates and fails the case. Witnesses replay first; when one catches the
        mutant, the samples still replay until one catches it too, so the receipt can say whether sampling
        adds detection power (``caught_by_sample``) or only the named scenarios do.
        """
        run = self.runs[case.test]
        every_history = case.contract.bug_reintroduced_by(case.name) is not None
        diverged: list[str] = []
        for trace_id, trace in generation.traces.items():
            if diverged and not every_history and not trace_id.startswith("sample-"):
                continue
            try:
                replay_trace(
                    trace,
                    case.contract,
                    factory,
                    mutant=case.name,
                    work_base=self.artifacts / "_work",
                    reproduce=self.reproduce(case, trace, case.name),
                )
            except ReplayMismatch as mismatch:
                if not diverged:
                    # Where it was first caught: the trace id here, its state, action and differences below.
                    run.mutations[case.name] = {"detected_by": trace_id, **mismatch.as_json()}
                    item.user_properties.append(("quint_detected_by", trace_id))
                diverged.append(trace_id)
                if not every_history and trace_id.startswith("sample-"):
                    break
        if diverged:
            mutation = run.mutations[case.name]
            mutation["caught_by_sample"] = any(trace_id.startswith("sample-") for trace_id in diverged)
            if every_history:
                mutation.update(histories=len(generation.traces), diverged=diverged)
            return
        run.findings[case.id] = {"survived": len(generation.traces)}
        pytest.fail(
            f"mutant {case.name!r} survived all {len(generation.traces)} generated histories of "
            f"{case.contract.name!r}: no observation differed from the model",
            pytrace=False,
        )

    def save_mismatch(self, case: Case, mismatch: ReplayMismatch) -> Path:
        directory = self.artifacts / case.contract.name
        directory.mkdir(parents=True, exist_ok=True)
        stem = case.test.rsplit("::", 1)[-1]
        path = directory / f"mismatch-{stem}-{case.id}.json"
        path.write_text(json.dumps(mismatch.as_json(), indent=2) + "\n", encoding="utf-8")
        return path

    def receipt(self, status: str) -> dict[str, Any]:
        contracts = []
        for name, contract in sorted(self.contracts.items()):
            entry: dict[str, Any] = {
                "name": name,
                "manifest": contract.manifest.as_posix(),
                "bounds": vars(contract.bounds),
                "invariant": contract.invariant,
                "actions": dict(contract.actions),
                "convention": contract.convention,
                "witnesses": list(contract.witnesses),
                "mutants": list(contract.mutants),
                "mutant_specs": [mutant.as_json() for mutant in contract.mutant_specs],
                "bugs_found": [bug.as_json() for bug in contract.bugs_found],
                "limits": list(contract.limits),
            }
            try:
                entry["sha256"] = contract.fingerprints()
            except OSError as error:
                entry["sha256_error"] = str(error)
            generation = self.generations.get(name)
            if generation is not None:
                entry["quint"] = generation.quint.version if generation.quint else None
                entry["commands"] = generation.commands
                entry["traces"] = {case: path.as_posix() for case, path in generation.traces.items()}
                if contract.witnesses:
                    witness = contract.witnesses[0]
                    try:
                        entry["illustrated"] = _illustration(contract, witness, generation.traces[f"witness-{witness}"])
                    except (OSError, ItfError, TraceError) as error:
                        entry["illustrated_error"] = f"{type(error).__name__}: {error}"
            if name in self.failures:
                failure = self.failures[name]
                entry["generation_failure"] = f"{type(failure).__name__}: {failure}"
            entry["tests"] = [
                {
                    "test": run.test,
                    "complete": self.mode == "generate-and-replay" and not run.missing(),
                    "not_passed": run.missing() if self.mode == "generate-and-replay" else [],
                    "outcomes": dict(sorted(run.outcomes.items())),
                    "steps": run.steps,
                    "mutations": run.mutations,
                    "imported_modules": run.imported,
                    "findings": dict(sorted(run.findings.items())),
                }
                for run in self.runs.values()
                if run.contract.name == name
            ]
            contracts.append(entry)
        return {
            "tool": "pytest-quint-connect",
            "status": status,
            "mode": self.mode,
            "source_trace": str(Path(self.replay_path).resolve()) if self.replay_path else None,
            "replay_mutant": self.replay_mutant,
            "head": _git_head(self.root),
            "root": self.root.as_posix(),
            "config_file": self.config_file,
            "required": self.required,
            "incomplete_required": self.incomplete,
            "contracts": contracts,
        }

    def write_receipt(self, status: str) -> None:
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / RECEIPT).write_text(json.dumps(self.receipt(status), indent=2) + "\n", encoding="utf-8")

    def check_required(self) -> None:
        """Record why this session is not complete: a replay, a filtered run, or a missing required contract."""
        if self.replay_path:
            self.incomplete.append("replay-only session: replaying one saved trace is never complete coverage")
            return
        for name in self.required:
            if not any(run.contract.name == name for run in self.runs.values()):
                self.incomplete.append(f"{name}: required contract was not collected")
        for run in self.runs.values():
            missing = run.missing()
            if missing:
                self.incomplete.append(f"{run.test}: {len(missing)} case(s) did not run or did not pass: {missing}")


def _illustration(contract: Contract, witness: str, trace: Path) -> dict[str, Any]:
    """One witness, step by step: each action the model took and the observation it expects afterwards.

    Reports show it so a reviewer can judge the model itself; it is read from the generated trace, like
    every expectation, and witnesses are scripted runs, so it is the same in every session.
    """
    return {
        "witness": witness,
        "steps": [
            {"state": step.index, "action": step.action, "arguments": plain(step.args), "observed": plain(expected)}
            for step, expected in steps(load_trace(trace), contract)
        ],
    }


def _action_of(raw_state: dict[str, Any], contract: Contract) -> str:
    if contract.convention == "mbt":
        return raw_state["mbt::actionTaken"]
    return raw_state[contract.event][contract.action_field]


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


_STATE = pytest.StashKey[_Session]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("quint-connect", "Quint model-based testing (pytest-quint-connect)")
    group.addoption("--quint", dest="quint", help="Quint executable (default: QUINT, ini, or the locked install)")
    group.addoption("--quint-artifacts", dest="quint_artifacts", help="directory for traces, logs and results.json")
    group.addoption(
        "--quint-replay", dest="quint_replay", help="replay one saved .itf.json trace instead of generating"
    )
    group.addoption("--quint-mutant", dest="quint_mutant", help="with --quint-replay: replay through this mutant")
    group.addoption(
        "--quint-require-complete",
        dest="quint_require_complete",
        action="store_true",
        help="fail the session unless every required contract ran every case and passed",
    )
    parser.addini("quint_executable", "Quint executable path", default="")
    parser.addini("quint_artifacts", "artifact directory, relative to rootdir", default=".artifacts/quint-connect")
    parser.addini("quint_required_contracts", "contract names a complete run must include", type="linelist")
    parser.addini("quint_timeout", "seconds allowed for each Quint command", default="300")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "quint_contract: a pytest-quint-connect contract test (set automatically)")
    if config.getoption("quint_mutant") and not config.getoption("quint_replay"):
        raise pytest.UsageError("--quint-mutant needs --quint-replay")
    state = _Session(config)
    config.stash[_STATE] = state
    if (state.artifacts / RECEIPT).exists():
        # A crash later in this session must not leave an earlier success standing.
        state.write_receipt("running")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    declaration = getattr(metafunc.function, _MARK, None)
    if not isinstance(declaration, _Declaration):
        return
    state: _Session = metafunc.config.stash[_STATE]
    manifest = declaration.manifest
    if not manifest.is_absolute():
        manifest = Path(str(metafunc.definition.path)).parent / manifest
    contract = load_contract(manifest)
    test = metafunc.definition.nodeid
    state.register(contract, test)
    if state.replay_path:
        cases = [Case(contract, "replay", Path(state.replay_path).name, test)]
    else:
        cases = [Case(contract, "witness", name, test) for name in contract.witnesses]
        cases += [Case(contract, "sample", str(index), test) for index in range(contract.bounds.n_traces)]
        cases += [Case(contract, "mutant", name, test) for name in contract.mutants]
        cases.append(Case(contract, "coverage", "", test))
    metafunc.parametrize("quint_case", cases, ids=[case.id for case in cases], indirect=True)


def _case_of(item: pytest.Item) -> Case | None:
    if not isinstance(item, pytest.Function) or not hasattr(item, "callspec"):
        return None
    case = item.callspec.params.get("quint_case")
    return case if isinstance(case, Case) else None


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    state: _Session = config.stash[_STATE]
    if os.environ.get("PYTEST_XDIST_WORKER") and any(_case_of(item) is not None for item in items):
        # Workers would regenerate into, and delete, the same trace directory, and judge completeness per process.
        raise pytest.UsageError(
            "pytest-quint-connect contract tests do not run under pytest-xdist; "
            "run them without -n, or deselect them with -m 'not quint_contract'"
        )
    if state.replay_path:
        tests = {case.test for case in map(_case_of, items) if case is not None}
        if len(tests) != 1:
            raise pytest.UsageError(
                f"--quint-replay needs exactly one selected contract test; {len(tests)} were collected. "
                "Name the test node, e.g. pytest tests/test_x.py::test_contract --quint-replay TRACE"
            )


def pytest_collection_finish(session: pytest.Session) -> None:
    state: _Session = session.config.stash[_STATE]
    for item in session.items:
        case = _case_of(item)
        if case is not None:
            state.runs[case.test].collected.append(case.id)
    if state.runs:
        state.write_receipt("running")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    case = _case_of(item)
    if case is not None:
        run = item.config.stash[_STATE].runs[case.test]
        if report.when == "call" or report.outcome != "passed":
            if run.outcomes.get(case.id) in (None, "passed"):
                run.outcomes[case.id] = report.outcome
        if report.when == "call" and call.excinfo is not None and isinstance(call.excinfo.value, ReplayMismatch):
            report.longrepr = str(call.excinfo.value)
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash.get(_STATE, None)
    if state is None or (not state.runs and not state.required):
        return
    state.check_required()
    failed = exitstatus not in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED) or bool(state.failures)
    # "passed" means every collected contract test ran every case; a filtered run is "partial".
    filtered = state.replay_path is None and any(run.missing() for run in state.runs.values())
    if failed:
        status = "failed"
    elif state.incomplete or filtered or not state.runs:
        status = "partial"
    else:
        status = "passed"
    state.write_receipt(status)
    if state.require_complete and state.incomplete and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    state = config.stash.get(_STATE, None)
    if state is None or not state.runs:
        return
    terminalreporter.section("pytest-quint-connect")
    for run in state.runs.values():
        if not run.collected:
            terminalreporter.write_line(f"{run.test}: not selected")
            continue
        passed = sum(1 for outcome in run.outcomes.values() if outcome == "passed")
        generation = state.generations.get(run.contract.name)
        total = 1 if state.replay_path else len(run.contract.case_ids())
        terminalreporter.write_line(
            f"{run.test}: {passed}/{total} cases passed; "
            f"{len(generation.traces) if generation else 0} fresh histories; "
            f"{len(run.mutations)}/{len(run.contract.mutants)} mutants detected"
        )
        if witnesses_only(run.mutations, run.contract.bounds.n_traces):
            terminalreporter.write_line(f"{run.test}: {WITNESSES_ONLY}", yellow=True)
    if state.replay_path:
        terminalreporter.write_line("replay-only: one saved trace was checked; this is not complete coverage")
    for line in state.incomplete:
        terminalreporter.write_line(f"INCOMPLETE {line}", red=True)
    if state.quint_note:
        terminalreporter.write_line(f"quint: {state.quint_note}")
    terminalreporter.write_line(f"receipt: {state.artifacts / RECEIPT}")


@pytest.fixture
def quint_case(request: pytest.FixtureRequest) -> Case:
    """The history being checked. Generation happens here, so a Quint failure is a setup error."""
    case: Case = request.param
    if case.kind != "replay":
        request.config.stash[_STATE].generation(case.contract)
    return case
