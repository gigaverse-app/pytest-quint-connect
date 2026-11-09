"""Find the pinned Quint CLI and generate ITF traces with it."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .contract import Contract
from .itf import ItfError, load_trace

__all__ = ["Generation", "QuintError", "QuintTool", "generate", "resolve_quint"]

TOOLCHAIN = Path(__file__).resolve().parent / "toolchain"
_ACCEPTED_STATUS = {"ok", "passed"}
# QNT101 for a definition or parameter that reuses a Quint built-in name (`next`, `assign`, `head`, ...).
_BUILTIN_REDEFINED = re.compile(r"Built-in name '([^']+)' is redefined")


class QuintError(RuntimeError):
    """Quint is missing, has the wrong version, failed, or generated no usable trace."""


@dataclass(frozen=True)
class QuintTool:
    executable: Path
    version: str
    prefix: tuple[str, ...] = ()

    @property
    def command(self) -> list[str]:
        """How to start Quint: ``[executable]``, or ``[node, cli.js]`` in place of a Windows npm shim."""
        return list(self.prefix) if self.prefix else [str(self.executable)]


def _launcher(executable: Path) -> tuple[str, ...]:
    """Run npm's Windows ``quint.cmd`` shim as ``node cli.js``.

    ``cmd.exe`` would otherwise reinterpret ``|``, ``^`` and ``&`` in arguments such
    as the witness ``--match`` pattern.
    """
    if executable.suffix.lower() not in (".cmd", ".bat"):
        return ()
    cli = executable.parent.parent / "@informalsystems" / "quint" / "dist" / "src" / "cli.js"
    node = shutil.which("node")
    if not cli.is_file() or node is None:
        raise QuintError(
            f"{executable} is a cmd.exe shim and the Quint package beside it was not found; "
            "pass --quint the path of an executable that does not go through cmd.exe"
        )
    return (node, str(cli))


@dataclass
class Generation:
    """Freshly generated traces for one contract, keyed by case id (``witness-NAME``, ``sample-N``)."""

    directory: Path
    traces: dict[str, Path]
    commands: list[list[str]] = field(default_factory=list)
    quint: QuintTool | None = None


def _shim(path: Path) -> Path:
    """On Windows, npm installs ``quint.cmd`` beside a POSIX ``quint`` script that cannot be executed."""
    if os.name == "nt" and path.suffix == "" and path.with_suffix(".cmd").is_file():
        return path.with_suffix(".cmd")
    return path


def _cache_root() -> Path:
    configured = os.environ.get("PYTEST_QUINT_CONNECT_CACHE")
    if configured:
        return Path(configured)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "pytest-quint-connect"


def run(arguments: list[str], log: Path, *, cwd: Path | None = None, timeout: float = 300) -> str:
    """Run a command, keep its combined output in ``log``, and fail with the log's path on a nonzero exit."""
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError:
        log.write_text(f"executable not found: {arguments[0]}\n", encoding="utf-8")
        raise QuintError(f"executable not found: {arguments[0]}") from None
    except subprocess.TimeoutExpired as error:
        log.write_text(str(error.output or ""), encoding="utf-8")
        raise QuintError(f"timed out after {timeout}s; see {log}: {' '.join(arguments)}") from None
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        # The tool's own words first: reports truncate long errors, and the command line says least.
        tail = "\n".join(result.stdout.strip().splitlines()[-40:])
        raise QuintError(
            f"{tail}\n{_rename_hint(result.stdout)}command failed ({result.returncode}); log: {log}\n"
            f"command: {' '.join(arguments)}"
        )
    return result.stdout


def _rename_hint(output: str) -> str:
    """Say what to do about a reused built-in name: Quint's QNT101 says only that it is redefined."""
    names = list(dict.fromkeys(_BUILTIN_REDEFINED.findall(output)))
    if not names:
        return ""
    quoted = ", ".join(f"'{name}'" for name in names)
    verb = "is" if len(names) == 1 else "are"
    return (
        f"hint: rename this action, parameter or definition; {quoted} {verb} reserved in Quint (a built-in name). "
        "Names such as key, value, entry or amount are free.\n"
    )


def _managed_install(version: str, logs: Path, notify: Callable[[str], None]) -> Path:
    """Install the locked Quint toolchain shipped with this package into a per-user cache.

    ``notify`` hears one line: that the cached install is reused, or that a first install starts (it can take
    a minute), so a user can tell which Quint ran and where it lives.
    """
    lock = TOOLCHAIN / "package-lock.json"
    pinned = json.loads((TOOLCHAIN / "package.json").read_text(encoding="utf-8"))["dependencies"][
        "@informalsystems/quint"
    ]
    if pinned != version:
        raise QuintError(
            f"the contract needs Quint {version}, but this plugin bundles a lock for {pinned}; "
            "install that version yourself and pass --quint PATH"
        )
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    target = _cache_root() / f"quint-{version}-{digest}"
    executable = _shim(target / "node_modules/.bin/quint")
    if executable.is_file():
        notify(f"using the bundled Quint {version} from {target}")
        return executable
    npm = shutil.which("npm")
    if npm is None:
        raise QuintError(
            "Quint is not installed and npm was not found. Install Node.js 22+ with npm, "
            "or pass --quint PATH (or set QUINT) to an existing Quint executable."
        )
    notify(f"installing the bundled Quint {version} into {target} with npm ci (first use only)")
    staging = target.with_name(target.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(TOOLCHAIN / name, staging / name)
    run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], logs / "npm-ci.log", cwd=staging, timeout=600)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return _shim(target / "node_modules/.bin/quint")


def resolve_quint(
    version: str, logs: Path, configured: str | None = None, notify: Callable[[str], None] = lambda line: None
) -> QuintTool:
    """Use ``configured`` (``--quint``/ini/``QUINT``) or the managed install, and require ``version``."""
    if configured:
        executable = _shim(Path(configured))
        if not executable.is_file():
            located = shutil.which(configured)
            if located is None:
                raise QuintError(f"configured Quint executable not found: {configured}")
            executable = Path(located)
    else:
        executable = _managed_install(version, logs, notify)
    prefix = _launcher(executable)
    tool = QuintTool(executable, "", prefix)
    reported = run([*tool.command, "--version"], logs / "quint-version.log", timeout=120).strip()
    if reported != version:
        raise QuintError(f"Quint version mismatch: the contract requires {version}, {executable} is {reported}")
    return QuintTool(executable, reported, prefix)


def generate(contract: Contract, output: Path, tool: QuintTool, *, timeout: float = 300) -> Generation:
    """Typecheck the model, then write fresh witness and sample traces into ``output``.

    ``output`` is emptied first so that traces from an earlier run can never satisfy
    this one. Every required witness and exactly ``bounds.n_traces`` samples must
    appear, and each trace must report a passing status.
    """
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    contract.check_historical()
    quint = tool.command
    model = str(contract.model)
    seed = str(contract.bounds.seed)
    # Verbosity 2: a failing witness names its assertion and line; a violated invariant prints the trace.
    common = ["--main", contract.module, "--backend", contract.backend, "--seed", seed, "--verbosity", "2"]
    generation = Generation(output, {}, quint=tool)

    def call(arguments: list[str], log: str) -> None:
        generation.commands.append(arguments)
        run(arguments, output / log, cwd=contract.directory, timeout=timeout)

    call([*quint, "typecheck", model], "typecheck.log")
    if contract.witnesses:
        pattern = "^(" + "|".join(contract.witnesses) + ")$"
        call(
            [
                *quint,
                "test",
                model,
                *common,
                "--match",
                pattern,
                "--max-samples",
                "1",
                "--out-itf",
                str(output / "witness-{test}-{seq}.itf.json"),
            ],
            "witnesses.log",
        )
        for name in contract.witnesses:
            found = sorted(output.glob(f"witness-{name}-*.itf.json"))
            if len(found) != 1:
                raise QuintError(f"Quint generated {len(found)} traces for required witness {name!r}; expected 1")
            generation.traces[f"witness-{name}"] = found[0]
    if contract.bounds.n_traces:
        arguments = [
            *quint,
            "run",
            model,
            *common,
            "--init",
            contract.init,
            "--step",
            contract.step,
            "--invariant",
            contract.invariant,
            "--max-samples",
            str(contract.bounds.max_samples),
            "--max-steps",
            str(contract.bounds.max_steps),
            "--n-traces",
            str(contract.bounds.n_traces),
            "--out-itf",
            str(output / "sample-{seq}.itf.json"),
        ]
        if contract.mbt:
            arguments.append("--mbt")
        call(arguments, "simulation.log")
        for index in range(contract.bounds.n_traces):
            path = output / f"sample-{index}.itf.json"
            if not path.is_file():
                emitted = len(list(output.glob("sample-*.itf.json")))
                raise QuintError(
                    f"Quint emitted {emitted} sampled traces; the contract requires {contract.bounds.n_traces}"
                )
            generation.traces[f"sample-{index}"] = path
    for case, path in generation.traces.items():
        try:
            trace = load_trace(path)
        except ItfError as error:
            raise QuintError(f"{case}: Quint wrote a malformed trace: {error}") from None
        if trace.status not in _ACCEPTED_STATUS:
            raise QuintError(f"{case}: trace status is {trace.status!r}, not a passing run: {path}")
    return generation
