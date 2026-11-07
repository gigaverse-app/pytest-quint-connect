"""``pytest-quint-connect codegen``: write or check a contract's generated driver base."""

import argparse
import difflib
import os
import sys
import tempfile
from pathlib import Path

from .codegen import CodegenError, compile_ir, generate_module
from .contract import ContractError, load_contract
from .quint import QuintError, resolve_quint


def default_output(manifest: Path, module: str) -> Path:
    return manifest.parent / f"{module}_driver.py"


def codegen(arguments: argparse.Namespace) -> int:
    contract = load_contract(Path(arguments.manifest))
    output = Path(arguments.output) if arguments.output else default_output(contract.manifest, contract.module)
    with tempfile.TemporaryDirectory(prefix="quint-codegen-") as directory:
        tool = resolve_quint(contract.quint_version, Path(directory), arguments.quint or os.environ.get("QUINT"))
        ir = compile_ir(contract, tool, Path(directory))
    source = generate_module(contract, ir, output=output, class_name=arguments.class_name)
    if arguments.check:
        current = output.read_text(encoding="utf-8") if output.is_file() else ""
        if current.replace("\r\n", "\n") == source:
            print(f"{output} is up to date")
            return 0
        sys.stdout.writelines(
            difflib.unified_diff(
                current.splitlines(keepends=True), source.splitlines(keepends=True), str(output), "generated"
            )
        )
        print(f"\n{output} is stale; rerun without --check to regenerate it", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(source.encode("utf-8"))
    print(f"wrote {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pytest-quint-connect", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("codegen", help="generate a typed driver base class from a contract's Quint model")
    generate.add_argument("manifest", help="the contract's quint-connect.json")
    generate.add_argument("-o", "--output", help="module to write (default: MODULE_driver.py beside the manifest)")
    generate.add_argument("--class-name", help="name of the generated base class (default: <Contract>Driver)")
    generate.add_argument("--quint", help="Quint executable (default: QUINT or the locked install)")
    generate.add_argument(
        "--check", action="store_true", help="exit 1 if the module differs from what would be written"
    )
    arguments = parser.parse_args(argv)
    try:
        return codegen(arguments)
    except (CodegenError, ContractError, QuintError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
