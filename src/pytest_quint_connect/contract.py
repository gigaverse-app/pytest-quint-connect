"""The contract manifest: which Quint model to run, how to read its traces, and what is required.

A manifest is a JSON file. Paths in it are relative to ``root``, which is itself
relative to the manifest's directory (default: the manifest's directory).
See ``docs/manifest.md`` for every field.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = ["Bounds", "Bug", "Contract", "ContractError", "Mutant", "load_contract"]

DEFAULT_QUINT_VERSION = "0.33.1"

_REQUIRED = {"version", "module", "model", "invariant", "actions", "bounds"}
_OPTIONAL = {
    "name",
    "root",
    "witnesses",
    "mutants",
    "bugs_found",
    "convention",
    "event",
    "action_field",
    "observe",
    "mbt",
    "init",
    "step",
    "backend",
    "quint_version",
    "historical",
    "historical_sha256",
    "sources",
    "attest",
    "description",
    "limits",
    "state_names",
    "codegen_header",
}


class ContractError(ValueError):
    """The manifest is malformed, incomplete or refers to missing files."""


@dataclass(frozen=True)
class Bounds:
    seed: int
    max_samples: int
    max_steps: int
    n_traces: int


def _declared(item: object) -> dict[str, Any]:
    """A manifest object's fields, without the optional ones it left out."""
    return {key: value for key, value in asdict(item).items() if value is not None}


@dataclass(frozen=True)
class Mutant:
    """A deliberate fault the driver factory builds from ``HistoryContext.mutant``; ``description`` says what it does."""

    name: str
    description: str | None = None

    def as_json(self) -> dict[str, Any]:
        return _declared(self)


@dataclass(frozen=True)
class Bug:
    """A real defect this contract found and that was fixed: the manifest's ``bugs_found`` entry.

    ``mutant`` names the mutant that reintroduces it. Every history is replayed through that
    mutant, so the receipt can say how many diverge; the rest of the entry is declared.
    """

    summary: str
    fixed_in: str
    found_by: str | None = None
    regression_test: str | None = None
    mutant: str | None = None

    def as_json(self) -> dict[str, Any]:
        return _declared(self)


@dataclass(frozen=True)
class Attestation:
    """Require every imported module of ``package`` to come from ``source``."""

    package: str
    source: Path


@dataclass(frozen=True)
class Contract:
    """A validated manifest."""

    name: str
    manifest: Path
    root: Path
    module: str
    model: Path
    invariant: str
    actions: Mapping[str, str]
    bounds: Bounds
    witnesses: tuple[str, ...]
    mutant_specs: tuple[Mutant, ...]
    bugs_found: tuple[Bug, ...]
    convention: str
    event: str
    action_field: str
    observe: tuple[str, ...]
    observe_single: bool
    mbt: bool
    init: str
    step: str
    backend: str
    quint_version: str
    historical: Path | None
    historical_sha256: str | None
    sources: tuple[Path, ...]
    attest: Attestation | None
    limits: tuple[str, ...]
    # Comment lines ``codegen`` writes first, such as a license notice; never part of the fingerprint.
    codegen_header: Path | None = None
    # The model variable whose value names each state, such as a sum type ``Empty | PartlyFilled | Full``.
    # Reports draw the model's state machine in those names; the driver is never compared on it.
    state_names: str | None = None

    @property
    def mutants(self) -> tuple[str, ...]:
        """Mutant names in manifest order: what ``HistoryContext.mutant`` and case ids carry."""
        return tuple(mutant.name for mutant in self.mutant_specs)

    def bug_reintroduced_by(self, mutant: str) -> Bug | None:
        """The found bug this mutant reintroduces, if any: such a mutant replays every history."""
        # dry-lint: distinct-from src/pytest_quint_connect/report.py::_catch_of
        # This reads the manifest's declared bugs to decide how a mutant replays; _catch_of reads one test's
        # recorded mutant outcome from a receipt. Reports are built from receipts only and never load a Contract.
        return next((bug for bug in self.bugs_found if bug.mutant == mutant), None)

    @property
    def directory(self) -> Path:
        return self.manifest.parent

    def case_ids(self) -> list[str]:
        """Every case a complete run of this contract executes, in order."""
        return (
            [f"witness-{name}" for name in self.witnesses]
            + [f"sample-{index}" for index in range(self.bounds.n_traces)]
            + [f"mutant-{name}" for name in self.mutants]
            + ["coverage"]
        )

    def provenance_files(self) -> list[Path]:
        files = [self.manifest, self.model, *self.sources]
        if self.historical is not None:
            files.append(self.historical)
        return files

    def fingerprints(self) -> dict[str, str]:
        result = {}
        for path in self.provenance_files():
            result[display_path(path, self.root)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def check_historical(self) -> None:
        if self.historical is None:
            return
        digest = hashlib.sha256(self.historical.read_bytes()).hexdigest()
        if digest != self.historical_sha256:
            raise ContractError(
                f"historical model changed: {self.historical} has SHA-256 {digest}, "
                f"the manifest pins {self.historical_sha256}"
            )


def display_path(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` in POSIX form when it lies inside ``root``, else absolute.

    Receipts and reports name files this way so they read the same on every machine.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _string(data: Mapping[str, Any], key: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise ContractError(f"manifest field {key!r} must be a non-empty string")
    return value


def _names(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ContractError(f"manifest field {key!r} must be a list of names")
    if len(set(value)) != len(value):
        raise ContractError(f"manifest field {key!r} repeats a name")
    return tuple(value)


def _strings(value: object, required: tuple[str, ...], optional: tuple[str, ...], what: str) -> dict[str, str]:
    """An object with every ``required`` field, no field outside ``optional``, each a non-empty string."""
    if not isinstance(value, dict) or not set(required) <= set(value) <= {*required, *optional}:
        allowed = f"; optional {list(optional)}" if optional else ""
        raise ContractError(f"{what} must be an object with the fields {list(required)}{allowed}")
    if not all(isinstance(item, str) and item for item in value.values()):
        raise ContractError(f"{what}: every field must be a non-empty string")
    return value


def _mutant(entry: object) -> Mutant:
    """A ``mutants`` entry: a name, or ``{"name", "description"}``."""
    if isinstance(entry, str) and entry:
        return Mutant(entry)
    if not isinstance(entry, dict):
        raise ContractError(f"a mutants entry must be a name or an object, got {entry!r}")
    return Mutant(**_strings(entry, ("name", "description"), (), f"mutant {entry.get('name')!r}"))


def _mutants(data: Mapping[str, Any]) -> tuple[Mutant, ...]:
    value = data.get("mutants", [])
    if not isinstance(value, list):
        raise ContractError("manifest field 'mutants' must be a list of names or mutant objects")
    mutants = tuple(_mutant(entry) for entry in value)
    if len({mutant.name for mutant in mutants}) != len(mutants):
        raise ContractError("manifest field 'mutants' repeats a name")
    return mutants


def _bugs(data: Mapping[str, Any], mutants: tuple[str, ...]) -> tuple[Bug, ...]:
    """``bugs_found``: each ``{"summary", "fixed_in", "found_by"?, "regression_test"?, "mutant"?}``."""
    value = data.get("bugs_found", [])
    if not isinstance(value, list):
        raise ContractError("manifest field 'bugs_found' must be a list of objects")
    fields = ("found_by", "regression_test", "mutant")
    bugs = tuple(Bug(**_strings(entry, ("summary", "fixed_in"), fields, "a bugs_found entry")) for entry in value)
    claimed = [bug.mutant for bug in bugs if bug.mutant is not None]
    undeclared = sorted(set(claimed) - set(mutants))
    if undeclared:
        raise ContractError(f"bugs_found names undeclared mutants {undeclared}; declare them in 'mutants'")
    if len(set(claimed)) != len(claimed):
        raise ContractError("two bugs_found entries name the same mutant")
    return bugs


def _positive(bounds: Mapping[str, Any], key: str, *, minimum: int = 1) -> int:
    value = bounds.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ContractError(f"bounds.{key} must be an integer >= {minimum}")
    return value


def load_contract(manifest: Path) -> Contract:
    """Read and validate a manifest. Every referenced file must exist."""
    manifest = manifest.resolve()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractError(f"manifest not found: {manifest}") from None
    except json.JSONDecodeError as error:
        raise ContractError(f"manifest is not JSON: {manifest}: {error}") from None
    if not isinstance(data, dict):
        raise ContractError(f"manifest must be a JSON object: {manifest}")
    missing = _REQUIRED - set(data)
    if missing:
        raise ContractError(f"manifest {manifest} lacks {sorted(missing)}")
    unknown = set(data) - _REQUIRED - _OPTIONAL
    if unknown:
        raise ContractError(f"manifest {manifest} has unknown fields {sorted(unknown)}")
    if data["version"] != 1:
        raise ContractError(f"unsupported manifest version {data['version']!r}; this plugin reads version 1")

    root = (manifest.parent / data.get("root", ".")).resolve()

    def path(value: str, what: str) -> Path:
        resolved = (manifest.parent / value).resolve()
        if not resolved.is_file():
            raise ContractError(f"{what} not found: {resolved}")
        return resolved

    actions = data["actions"]
    if not isinstance(actions, dict) or not actions or not all(isinstance(key, str) for key in actions):
        raise ContractError("manifest field 'actions' must map each action name to its implementation boundary")
    raw_bounds = data["bounds"]
    if not isinstance(raw_bounds, dict):
        raise ContractError("manifest field 'bounds' must be an object")
    unknown_bounds = set(raw_bounds) - {"seed", "max_samples", "max_steps", "n_traces"}
    if unknown_bounds:
        raise ContractError(f"unknown bounds {sorted(unknown_bounds)}")
    bounds = Bounds(
        seed=_positive(raw_bounds, "seed", minimum=0),
        max_samples=_positive(raw_bounds, "max_samples"),
        max_steps=_positive(raw_bounds, "max_steps"),
        n_traces=_positive(raw_bounds, "n_traces", minimum=0),
    )
    witnesses = _names(data, "witnesses")
    mutants = _mutants(data)
    if not witnesses and bounds.n_traces == 0:
        raise ContractError("a contract needs at least one witness or one sampled trace")

    convention = data.get("convention", "event")
    if convention not in ("event", "mbt"):
        raise ContractError("convention must be 'event' or 'mbt'")
    mbt = data.get("mbt", True)
    if not isinstance(mbt, bool):
        raise ContractError("mbt must be true or false")
    if convention == "mbt":
        if witnesses:
            raise ContractError(
                "the mbt convention cannot drive named witnesses: `quint test --out-itf` emits no "
                "mbt::actionTaken metadata (Quint 0.33.1). Use the event convention for witnesses."
            )
        if not mbt:
            raise ContractError("the mbt convention requires mbt: true")

    observe = data.get("observe", "observed")
    if isinstance(observe, str) and observe:
        observed, single = (observe,), True
    elif isinstance(observe, list) and observe and all(isinstance(item, str) and item for item in observe):
        observed, single = tuple(observe), False
    else:
        raise ContractError("observe must be a state variable name or a non-empty list of names")

    state_names = _string(data, "state_names") if "state_names" in data else None
    if state_names in observed:
        raise ContractError(
            f"state_names {state_names!r} is also observed; name the states with a variable the driver is not "
            "compared on, so naming them never changes what the code must do"
        )

    historical = data.get("historical")
    historical_sha256 = data.get("historical_sha256")
    if (historical is None) != (historical_sha256 is None):
        raise ContractError("historical and historical_sha256 must be given together")
    sources = data.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
        raise ContractError("sources must be a list of paths")
    attest = data.get("attest")
    attestation = None
    if attest is not None:
        if not isinstance(attest, dict) or set(attest) != {"package", "source"}:
            raise ContractError("attest must be {'package': name, 'source': directory}")
        source_dir = (root / _string(attest, "source")).resolve()
        if not source_dir.is_dir():
            raise ContractError(f"attest.source is not a directory: {source_dir}")
        attestation = Attestation(_string(attest, "package"), source_dir)
    limits = data.get("limits", [])
    if isinstance(limits, str):
        limits = [limits]

    contract = Contract(
        name=_string(data, "name", manifest.parent.name),
        manifest=manifest,
        root=root,
        module=_string(data, "module"),
        model=path(_string(data, "model"), "model"),
        invariant=_string(data, "invariant"),
        actions=dict(actions),
        bounds=bounds,
        witnesses=witnesses,
        mutant_specs=mutants,
        bugs_found=_bugs(data, tuple(mutant.name for mutant in mutants)),
        convention=convention,
        event=_string(data, "event", "event"),
        action_field=_string(data, "action_field", "op"),
        observe=observed,
        observe_single=single,
        mbt=mbt,
        init=_string(data, "init", "init"),
        step=_string(data, "step", "step"),
        backend=_string(data, "backend", "typescript"),
        quint_version=_string(data, "quint_version", DEFAULT_QUINT_VERSION),
        historical=path(historical, "historical model") if historical else None,
        historical_sha256=historical_sha256,
        sources=tuple((root / item).resolve() for item in sources) if sources else (),
        attest=attestation,
        limits=tuple(str(item) for item in limits),
        codegen_header=path(_string(data, "codegen_header"), "codegen_header") if "codegen_header" in data else None,
        state_names=state_names,
    )
    check_sources(contract)
    return contract


def check_sources(contract: Contract) -> None:
    for source in contract.sources:
        if not source.is_file():
            raise ContractError(f"manifest source missing: {source}")
