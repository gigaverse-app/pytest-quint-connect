"""Install a built wheel into a fresh environment and run the LRU example from outside the checkout.

This proves plugin discovery through the ``pytest11`` entry point of the installed
distribution, not through the source tree:

    uv build --wheel
    uv run python scripts/smoke_distribution.py dist/pytest_quint_connect-*.whl

Quint comes from ``QUINT`` or the plugin's locked npm install, as for any user.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "lru_cache"
EXAMPLE_FILES = ("lru.qnt", "quint-connect.json", "lru_cache.py", "conftest.py", "test_lru_contract.py", "pytest.ini")


def run(arguments: list[str], cwd: Path, env: dict[str, str], *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(arguments), flush=True)
    result = subprocess.run(arguments, cwd=cwd, env=env, text=True, capture_output=True)
    print(result.stdout[-4000:], result.stderr[-2000:], sep="\n", flush=True)
    if result.returncode != expect:
        raise SystemExit(f"expected exit {expect}, got {result.returncode}: {' '.join(arguments)}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="pqc-smoke-") as directory:
        base = Path(directory)
        environment = base / "venv"
        project = base / "project"
        project.mkdir()
        for name in EXAMPLE_FILES:
            shutil.copyfile(EXAMPLE / name, project / name)
        env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "VIRTUAL_ENV")}
        uv = shutil.which("uv")
        if uv is None:
            raise SystemExit("uv is required")
        run([uv, "venv", "--python", args.python, str(environment)], base, env)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([uv, "pip", "install", "--python", str(python), str(wheel)], base, env)
        located = run(
            [str(python), "-c", "import pytest_quint_connect as p; print(p.__file__)"], project, env
        ).stdout.strip()
        if Path(located).resolve().is_relative_to(ROOT):
            raise SystemExit(f"imported from the checkout, not the wheel: {located}")
        traced = run([str(python), "-m", "pytest", "--trace-config", "--help"], project, env).stdout
        registered = [
            line for line in traced.splitlines() if "pytest_quint_connect" in line and "site-packages" in line
        ]
        if not registered:
            raise SystemExit("pytest did not load pytest_quint_connect from the installed distribution")
        run([str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--quint-require-complete"], project, env)
        receipt = json.loads((project / ".artifacts/quint-connect/results.json").read_text(encoding="utf-8"))
        (contract,) = receipt["contracts"]
        assert receipt["status"] == "passed" and all(test["complete"] for test in contract["tests"]), receipt
        assert len(contract["traces"]) == 15 and contract["quint"] == "0.33.1", contract
        fault = run(
            [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--lru-implementation", "stale-read"],
            project,
            env,
            expect=1,
        )
        assert "implementation state differs from the model" in fault.stdout, fault.stdout
        print(f"smoke passed: {located}")


if __name__ == "__main__":
    main()
