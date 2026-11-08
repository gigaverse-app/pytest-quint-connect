"""Copy real receipts into test fixtures with machine-specific paths and the commit replaced.

    uv run python scripts/normalize_receipt.py RECEIPT OUTPUT

Receipts record absolute paths (rootdir, traces, Quint commands) and the Git head of the run.
Fixtures keep everything else exactly as the plugin wrote it, with the checkout root replaced by
``/repo`` and the head by a fixed value, so they read the same on every machine.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAD = "0123456789abcdef0123456789abcdef01234567"


def normalize(value: object, root: str) -> object:
    if isinstance(value, str):
        for local, stable in ((root, "/repo"), (Path.home().as_posix(), "/home/user")):
            value = value.replace(local, stable).replace(local.replace("/", "\\"), stable)
        return value
    if isinstance(value, list):
        return [normalize(item, root) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item, root) for key, item in value.items()}
    return value


def main() -> None:
    source, output = map(Path, sys.argv[1:3])
    receipt = json.loads(source.read_text(encoding="utf-8"))
    receipt = normalize(receipt, ROOT.as_posix())
    assert isinstance(receipt, dict)
    receipt["head"] = HEAD
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes((json.dumps(receipt, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
