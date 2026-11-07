"""Execute the public action's shared file plan; this consumer does no selection."""

import json
import os
import subprocess
import sys


def main() -> int:
    mode = os.environ.get("CI_TEST_MODE")
    try:
        files = json.loads(os.environ.get("CI_TEST_FILES", "[]"))
        valid = isinstance(files, list) and all(isinstance(path, str) and path for path in files)
    except ValueError:
        files, valid = [], False
    # Missing/failed planning must execute complete roots, never an empty pytest command.
    if os.environ.get("CI_PLANNER_RESULT") != "success" or not valid or mode not in {"none", "scoped", "full"}:
        mode = "full"
    if mode == "scoped" and not files:
        mode = "full"
    if mode == "none":
        if files:
            mode = "full"
        else:
            print("No affected test files; pytest was not started.")
            return 0
    roots = ["tests"] if mode == "full" else files
    coverage = []
    if mode == "full" and os.environ.get("CI_COVERAGE") == "true":
        coverage = ["--cov", "--cov-report=term-missing", "--cov-report=xml"]
    print(f"Executing {mode} test plan: {len(roots)} paths", flush=True)
    return subprocess.run(
        # Planning already happened. Integration tests load the plugin in their
        # subprocesses; do not make it an implicit dependency of every CI test.
        [sys.executable, "-m", "pytest", "-p", "no:selective", *sys.argv[1:], *coverage, "--", *roots]
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
