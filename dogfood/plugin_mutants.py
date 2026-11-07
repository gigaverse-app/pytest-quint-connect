"""Deliberate faults in pytest-quint-connect's own receipt and completeness logic.

Loaded into an inner pytest process with ``-p plugin_mutants`` when
``PQC_DOGFOOD_MUTANT`` names a fault. Each patch replaces one real decision in the
installed plugin, so the session-receipts contract must observe a difference.
"""

import os
from typing import Any

from pytest_quint_connect import plugin

MUTANT = os.environ.get("PQC_DOGFOOD_MUTANT", "")
_Session: Any = plugin._Session
_TestRun: Any = plugin._TestRun

if MUTANT == "filtered-counts-as-complete":
    # A -k run that skipped cases is judged as if every case ran.
    _TestRun.missing = lambda self: []
elif MUTANT == "replay-counts-as-complete":
    # Replaying one saved trace no longer marks the session incomplete.
    original_check = _Session.check_required

    def check_required(self: Any) -> None:
        if not self.replay_path:
            original_check(self)

    _Session.check_required = check_required
elif MUTANT == "failed-session-keeps-old-receipt":
    # A session that fails, or is still running, leaves the previous results.json in place.
    original_write = _Session.write_receipt

    def write_receipt(self: Any, status: str) -> None:
        if status not in ("running", "failed"):
            original_write(self, status)

    _Session.write_receipt = write_receipt
elif MUTANT == "require-complete-ignored":
    # --quint-require-complete is accepted but has no effect on the exit code.
    original_init = _Session.__init__

    def init(self: Any, config: Any) -> None:
        original_init(self, config)
        self.require_complete = False

    _Session.__init__ = init
elif MUTANT:
    raise RuntimeError(f"unknown dogfood mutant {MUTANT!r}")
