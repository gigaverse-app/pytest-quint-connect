"""``pytest-quint-connect``: generate typed driver bases, and report contract receipts."""

import argparse
import difflib
import os
import shlex
import shutil
import sys
from pathlib import Path

from .codegen import CodegenError, compile_ir, generate_module
from .contract import ContractError, load_contract
from .quint import QuintError, resolve_quint
from .replay import scratch_directory
from .report import (
    END,
    START,
    ReportError,
    check_readme,
    embed,
    has_markers,
    load,
    receipt_paths,
    render_comment,
    render_readme,
)


def default_output(manifest: Path, module: str) -> Path:
    return manifest.parent / f"{module}_driver.py"


def codegen(arguments: argparse.Namespace) -> int:
    contract = load_contract(Path(arguments.manifest))
    output = Path(arguments.output) if arguments.output else default_output(contract.manifest, contract.module)
    # Kept when generation fails, so the log an error names still exists.
    with scratch_directory("quint-codegen-", keep_on_failure=True) as logs:
        tool = resolve_quint(contract.quint_version, logs, arguments.quint or os.environ.get("QUINT"))
        ir = compile_ir(contract, tool, logs)
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


def report(arguments: argparse.Namespace) -> int:
    """Render receipts as a PR comment, and write or check the README block. Exit 1 only on a stale block."""
    paths = receipt_paths(arguments.receipts)
    evidence = load(paths)
    note, status = "", 0
    if arguments.readme:
        readme = Path(arguments.readme)
        document = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        refresh = f"pytest-quint-connect report {shlex.join(arguments.receipts)} --readme {shlex.quote(str(readme))}"
        if not has_markers(document):
            print(f"{readme}: no {START} block; nothing to keep current")
        elif arguments.check:
            result = check_readme(document, evidence, name=str(readme))
            if result.state == "stale":
                sys.stdout.write(result.diff)
                print(f"\n{readme}: the contract block is stale; run `{refresh}` and commit it", file=sys.stderr)
                note = f"> [!WARNING]\n> The contract block in `{readme}` is stale. Refresh it with `{refresh}`."
                status = 1
            elif result.state == "unverifiable":
                print(f"{readme}: not checked: {result.reason}")
                note = f"<sub>`{readme}` contract block not checked: {result.reason}.</sub>"
            else:
                print(f"{readme}: the contract block is current")
        else:
            updated = embed(document, render_readme(evidence))
            if updated != document:
                readme.write_bytes(updated.encode("utf-8"))
                print(f"updated {readme}")
            else:
                print(f"{readme} is up to date")
    comment = render_comment(
        evidence,
        repository=Path(arguments.repository_root),
        title=arguments.title,
        head=arguments.head,
        run_url=arguments.run_url,
        readme_note=note,
    )
    if arguments.comment:
        Path(arguments.comment).write_bytes(comment.encode("utf-8"))
    elif not arguments.readme:
        sys.stdout.write(comment)
    if arguments.status_file:
        Path(arguments.status_file).write_text(evidence.status or "none", encoding="utf-8")
    if arguments.artifact_dir:
        # Each receipt's whole directory: its traces, logs and mismatch files explain a failure.
        for index, receipt in enumerate(paths, start=1):
            name = "-".join(part for part in receipt.parent.parts if part not in (".", "..", receipt.anchor))
            shutil.copytree(receipt.parent, Path(arguments.artifact_dir) / f"{index}-{name}", dirs_exist_ok=True)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pytest-quint-connect", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser(
        "report", help="render results.json receipts as a pull-request comment and a README block"
    )
    summary.add_argument("receipts", nargs="+", help="results.json files or recursive globs (quote them)")
    summary.add_argument("--comment", help="write the pull-request comment Markdown here (default: stdout)")
    summary.add_argument("--readme", help=f"keep the block between {START} and {END} in this document current")
    summary.add_argument("--check", action="store_true", help="with --readme: exit 1 if the block is stale")
    summary.add_argument("--title", default="", help="names this lane or session in the comment")
    summary.add_argument("--head", default="", help="commit the receipts were produced from")
    summary.add_argument("--run-url", default="", help="link to the run that produced the receipts")
    summary.add_argument(
        "--repository-root", default=".", help="sessions under this directory get a cd line in reproduce commands"
    )
    summary.add_argument("--status-file", help="write the worst contract status here")
    summary.add_argument("--artifact-dir", help="copy each receipt's directory (traces, mismatches) here")
    summary.set_defaults(handler=report)
    generate = commands.add_parser("codegen", help="generate a typed driver base class from a contract's Quint model")
    generate.add_argument("manifest", help="the contract's quint-connect.json")
    generate.add_argument("-o", "--output", help="module to write (default: MODULE_driver.py beside the manifest)")
    generate.add_argument("--class-name", help="name of the generated base class (default: <Contract>Driver)")
    generate.add_argument("--quint", help="Quint executable (default: QUINT or the locked install)")
    generate.add_argument(
        "--check", action="store_true", help="exit 1 if the module differs from what would be written"
    )
    generate.set_defaults(handler=codegen)
    arguments = parser.parse_args(argv)
    try:
        return arguments.handler(arguments)
    except (CodegenError, ContractError, QuintError, ReportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
