"""What one pull request's contract runs found, accumulated across its pushes.

The sticky comment carries this ledger as hidden JSON, so each run extends the previous one instead of
forgetting it. A *disagreement* is a case whose replay showed an observed-state mismatch, or a mutant
that survived every history: evidence about the code or the model, never a crash or a setup error. It
is *caught* on the first push that reports it and *fixed* once a later push runs that case and it
passes. A case that fails again after a fix is open again.
"""

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .report import Evidence

SCHEMA = 1
TAG = "pytest-quint-connect-ledger"
_HIDDEN = re.compile(r"<!-- pytest-quint-connect-ledger (\{.*?\}) -->", re.S)
# Bounds keep the hidden JSON well inside GitHub's comment limit, however long a pull request runs.
MAX_PUSHES = 50
MAX_CAUGHT = 200


@dataclass
class Ledger:
    pushes: list[dict[str, Any]] = field(default_factory=list)
    caught: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def fixed(self) -> int:
        return sum(1 for entry in self.caught.values() if entry["state"] == "fixed")

    @property
    def open(self) -> int:
        return sum(1 for entry in self.caught.values() if entry["state"] == "open")

    def to_json(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "pushes": self.pushes, "caught": self.caught}

    def comment(self) -> str:
        """The hidden comment. ``<`` and ``>`` are escaped so receipt text can never close it early."""
        text = json.dumps(self.to_json(), separators=(",", ":"), sort_keys=True)
        escaped = text.replace("<", "\\u003c").replace(">", "\\u003e")
        return f"<!-- {TAG} {escaped} -->"


def parse(text: str) -> Ledger:
    """The ledger in a previous comment or file; an empty, foreign or malformed one starts afresh."""
    match = _HIDDEN.search(text) or re.fullmatch(r"\s*(\{.*\})\s*", text, re.S)
    if not match:
        return Ledger()
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return Ledger()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return Ledger()
    pushes = [push for push in data.get("pushes", []) if isinstance(push, dict) and isinstance(push.get("head"), str)]
    caught = {
        str(key): entry
        for key, entry in dict(data.get("caught", {})).items()
        if isinstance(entry, dict) and entry.get("state") in ("open", "fixed")
    }
    return Ledger(pushes, caught)


def advance(previous: Ledger, evidence: "Evidence", *, head: str) -> Ledger:
    """``previous`` extended by this run's evidence for ``head``. A rerun of a head replaces its push."""
    pushes = [push for push in previous.pushes if push["head"] != head]
    number = next((push["push"] for push in previous.pushes if push["head"] == head), len(pushes) + 1)
    caught = {key: dict(entry) for key, entry in previous.caught.items()}
    for row in evidence.rows:
        for failure in row.failures:
            kind = "survived" if "survived" in failure.finding else "mismatch" if "state" in failure.finding else None
            if kind is None:
                continue  # a crash or setup error: no evidence about the code
            key = f"{row.contract} · {row.test} · {failure.case}"
            entry = caught.setdefault(key, {"found": number, "kind": kind, "state": "open"})
            entry["state"] = "open"
        for case in row.passed_cases:
            key = f"{row.contract} · {row.test} · {case}"
            if key in caught:
                caught[key]["state"] = "fixed"
    pushes.append({"push": number, "head": head, "status": evidence.status or "none"})
    pushes.sort(key=lambda push: push["push"])
    kept = dict(sorted(caught.items(), key=lambda item: (item[1]["state"] != "open", -item[1]["found"]))[:MAX_CAUGHT])
    return Ledger(pushes[-MAX_PUSHES:], kept)
