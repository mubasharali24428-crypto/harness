"""``hxc`` — check Hx sources and emit the frozen diagnostic contract.

Exit codes are part of the interface:

* ``0`` — no errors. Warnings are allowed; a warning is information, not a
  refusal.
* ``1`` — at least one error. Diagnostics went to stderr.
* ``2`` — usage or I/O failure: the file could not be read, the profile name is
  unknown, a manifest was requested for more than one program.

Nothing is ever written to stdout except an explicitly requested manifest
(``--manifest -``), because stdout belongs to a running program and this tool
may one day share a pipeline with one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .api import PROFILES, CheckResult, check_source
from .diagnostics import render_json, render_line
from .manifest import render_manifest
from .version import SPEC_VERSION, TOOLCHAIN_VERSION


def _check_one(path_text: str, profile: str) -> CheckResult:
    path = Path(path_text)
    source = path.read_text(encoding="utf-8")
    return check_source(source, str(path), profile)


def _emit(result: CheckResult, as_json: bool) -> None:
    if as_json:
        sys.stderr.write(render_json(result.diagnostics) + "\n")
        return
    for diagnostic in result.diagnostics:
        sys.stderr.write(render_line(diagnostic) + "\n")


def _write_manifest(result: CheckResult, profile: str, target: str) -> int:
    payload = render_manifest(result, profile)
    if target == "-":
        sys.stdout.write(payload)
        return 0
    Path(target).write_text(payload, encoding="utf-8")
    return 0


def _run_check(args: argparse.Namespace) -> int:
    if args.manifest is not None and len(args.files) != 1:
        sys.stderr.write("hxc: --manifest needs exactly one program\n")
        return 2
    results: list[CheckResult] = []
    status = 0
    for path_text in args.files:
        try:
            result = _check_one(path_text, args.profile)
        except OSError as error:
            sys.stderr.write(f"hxc: cannot read {path_text}: {error}\n")
            status = 2
            continue
        results.append(result)
        _emit(result, args.json)
        if not result.ok and status == 0:
            status = 1
    if status != 0 or args.manifest is None:
        return status
    return _write_manifest(results[0], args.profile, args.manifest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hxc",
        description="Check Hx programs (the Harness dialect of Hermes).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"hxc {TOOLCHAIN_VERSION} (hx spec {SPEC_VERSION})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check one or more .hx programs")
    check.add_argument("files", nargs="+", metavar="FILE.hx")
    check.add_argument(
        "--profile",
        choices=PROFILES,
        default="agent",
        help="language profile chosen by the host (default: agent)",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="write one JSON array of diagnostics to stderr",
    )
    check.add_argument(
        "--manifest",
        metavar="PATH",
        help="write the policy-request manifest ('-' for stdout); one program only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_error:  # argparse already printed the reason
        return int(exit_error.code or 0)
    if args.command == "check":
        return _run_check(args)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())
