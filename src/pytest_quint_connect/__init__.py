"""Replay Quint-generated histories against real Python implementations under pytest.

A community Python integration in the spirit of Quint Connect; not an official
Informal Systems or Quint project.
"""

from importlib.metadata import PackageNotFoundError, version

from .compare import Difference, differences
from .contract import Contract, ContractError, load_contract
from .itf import ItfError, QList, QMap, QTuple, Record, Trace, Unserializable, Variant, decode, load_trace
from .plugin import quint_contract
from .quint import QuintError
from .replay import (
    Driver,
    DriverFactory,
    HistoryContext,
    ImportEscape,
    ReplayMismatch,
    ReplayResult,
    Step,
    TraceError,
    replay_trace,
)
from .typed import ActionDriver

try:
    __version__ = version("pytest-quint-connect")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "1.0.0"

__all__ = [
    "ActionDriver",
    "Contract",
    "ContractError",
    "Difference",
    "Driver",
    "DriverFactory",
    "HistoryContext",
    "ImportEscape",
    "ItfError",
    "QList",
    "QMap",
    "QTuple",
    "QuintError",
    "Record",
    "ReplayMismatch",
    "ReplayResult",
    "Step",
    "Trace",
    "TraceError",
    "Unserializable",
    "Variant",
    "__version__",
    "decode",
    "differences",
    "load_contract",
    "load_trace",
    "quint_contract",
    "replay_trace",
]
