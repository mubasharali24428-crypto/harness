"""``hxr`` — the host side of Hx: plan a run, or run one locally and record it.

``hxc`` answers "is this program well formed?". ``hxr`` answers "may it run
here, what did it do, and what is the evidence?".

Exit codes are part of the interface:

* ``0`` — the run finished and its ``ensure``/return contract held.
* ``1`` — refused (check errors, digest mismatch, unsupported construct) or the
  program or host failed.
* ``2`` — usage or I/O failure.
* ``3`` — the host denied the request.
* ``4`` — a declared budget was exhausted.
* ``5`` — the run was cancelled.

stdout carries only the requested JSON (plan or evidence); diagnostics and
failures go to stderr, the same split the checker keeps.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from harness_hx.api import PROFILES, check_source
from harness_hx.diagnostics import render_json, render_line
from harness_hx.manifest import build_manifest

from .authority import authorize
from .bridge import (
    ReadOnlyRepoBridge,
    RecordingBridge,
    ReplayBridge,
    recording_from_json,
    recording_to_json,
)
from .errors import HxHostError
from .run import run_source

_EXIT = {"ok": 0, "refused": 1, "error": 1, "denied": 3, "budget": 4, "cancelled": 5}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="the .hx program to read")
    parser.add_argument("--profile", default="agent", choices=list(PROFILES))
    parser.add_argument(
        "--grant",
        action="append",
        default=[],
        metavar="CAP",
        help="a capability this host grants; repeat or comma-separate. Empty grants nothing.",
    )
    parser.add_argument(
        "--permissions",
        default=None,
        help="the principal's Harness permissions, comma-separated (e.g. read,comment)",
    )
    parser.add_argument("--principal", default="anonymous")
    parser.add_argument("--policy", default="local-dev")
    parser.add_argument(
        "--modules",
        default="auto",
        metavar="DIR|none",
        help="where `use NAME` is read from: a directory, 'auto' for the program's own "
        "directory (default), or 'none' to refuse any module at all",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON on stdout")


def _modules(args: argparse.Namespace) -> Path | None:
    """The module root this host resolves `use` names against.

    The default is the program's own directory, because that is where a helper sits
    in every layout this language has been used in. `'none'` is not a shortcut for a
    single-file check — it is the reviewer's way of asking what the program means when
    no helper is available, and it makes a `use` an error rather than a silent guess.
    """
    if args.modules == "none":
        return None
    if args.modules == "auto":
        return Path(args.path).resolve().parent
    return Path(args.modules)


def _grants(raw: list[str]) -> list[str]:
    names: list[str] = []
    for item in raw:
        names.extend(part.strip() for part in item.split(",") if part.strip())
    return names


def _permissions(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def _read(path_text: str) -> str | None:
    try:
        return Path(path_text).read_text(encoding="utf-8")
    except OSError as error:
        sys.stderr.write(f"hxr: cannot read {path_text}: {error}\n")
        return None


def _plan(args: argparse.Namespace) -> int:
    source = _read(args.path)
    if source is None:
        return 2
    result = check_source(source, args.path, args.profile, modules=_modules(args))
    if args.json:
        sys.stderr.write(render_json(result.diagnostics) + "\n")
    else:
        for diagnostic in result.diagnostics:
            sys.stderr.write(render_line(diagnostic) + "\n")
    if not result.ok:
        return 1
    manifest = build_manifest(result, args.profile)
    decision = authorize(
        manifest,
        granted=_grants(args.grant),
        principal_permissions=_permissions(args.permissions),
        principal=args.principal,
        policy=args.policy,
    )
    payload = {"manifest": manifest, "policy": decision.to_json()}
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        granted = ", ".join(sorted(decision.granted)) or "nothing"
        sys.stdout.write(f"workflows: {len(manifest.get('workflows', []))}\n")
        sys.stdout.write(f"requested: {', '.join(sorted(decision.requested)) or 'nothing'}\n")
        sys.stdout.write(f"granted: {granted}\n")
        for denial in decision.denials:
            sys.stdout.write(f"denied: {denial.capability} — {denial.reason}\n")
    return 0 if decision.allowed else 3


def _load_recording(path_text: str) -> dict[str, Any] | None:
    """Read a saved recording. A file that cannot be read is a usage failure.

    The *format* check is deliberately not here: `recording_from_json` raises
    `HXH104` for a wrong or missing version, so a v1 file is reported as a refused
    run with a code, not as a stack trace.
    """
    try:
        loaded = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"hxr: cannot read the recording {path_text}: {error}\n")
        return None
    if not isinstance(loaded, dict):
        sys.stderr.write("hxr: the recording must be a JSON object\n")
        return None
    return loaded


def _prices(path_text: str | None) -> tuple[dict[str, Any] | None, int]:
    """Read the host's price table. A bad table is a usage failure, not a run.

    It would be easy to treat an unreadable price file as "no prices" and let the
    run go on unpriced — which is exactly the silent-free-crossing the enforcement
    exists to prevent. So the CLI stops instead.
    """
    if path_text is None:
        return None, 0
    try:
        loaded = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"hxr: cannot read the price table {path_text}: {error}\n")
        return None, 2
    if not isinstance(loaded, dict):
        sys.stderr.write("hxr: the price table must be a JSON object of operation -> amount\n")
        return None, 2
    return loaded, 0


def _run(args: argparse.Namespace) -> int:
    source = _read(args.path)
    if source is None:
        return 2
    prices, usage_failure = _prices(args.prices)
    if usage_failure:
        return usage_failure
    inputs: dict[str, Any] = {}
    if args.inputs is not None:
        try:
            inputs = json.loads(args.inputs)
        except json.JSONDecodeError as error:
            sys.stderr.write(f"hxr: --inputs is not JSON: {error}\n")
            return 2
        if not isinstance(inputs, dict):
            sys.stderr.write("hxr: --inputs must be a JSON object\n")
            return 2
    root = Path(args.root) if args.root else Path(args.path).resolve().parent
    bridge = ReadOnlyRepoBridge(root=root)
    if args.record is not None and args.replay is not None:
        sys.stderr.write("hxr: --record and --replay are alternatives; pick one\n")
        return 2
    if args.replay is not None:
        loaded = _load_recording(args.replay)
        if loaded is None:
            return 2
        bridge = ReplayBridge(recording_from_json(loaded))
    elif args.record is not None:
        bridge = RecordingBridge(bridge)
    outcome = run_source(
        source,
        path=args.path,
        profile=args.profile,
        inputs=inputs,
        bridge=bridge,
        granted=_grants(args.grant),
        principal_permissions=_permissions(args.permissions),
        principal=args.principal,
        policy=args.policy,
        expected_digest=args.digest,
        expected_program_digest=args.program_digest,
        prices=prices,
        modules=_modules(args),
    )
    for diagnostic in outcome.diagnostics:
        sys.stderr.write(render_line(diagnostic) + "\n")
    if outcome.error is not None:
        sys.stderr.write(json.dumps(outcome.error.as_record(outcome.path)) + "\n")

    evidence = outcome.to_json()
    if args.record is not None and isinstance(bridge, RecordingBridge):
        # Written whether or not the run succeeded: the recording of a run that
        # failed is the artifact M7 exists for, and an in-flight exchange has to
        # survive to be refused by the next replay.
        Path(args.record).write_text(
            json.dumps(recording_to_json(bridge.exchanges), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.evidence:
        Path(args.evidence).write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.json:
        sys.stdout.write(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"status: {outcome.status}\n")
        sys.stdout.write(f"workflow: {outcome.workflow}\n")
        sys.stdout.write(f"run: {outcome.run_id}\n")
        if outcome.ok:
            sys.stdout.write(json.dumps(evidence["outcome"]["output"], indent=2) + "\n")
    return _EXIT.get(outcome.status, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hxr",
        description="Plan or run an Hx workflow against a host. Never merges, never approves.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="check, build the manifest, decide the request")
    _common(plan_parser)

    run_parser = sub.add_parser("run", help="run one workflow locally and write evidence")
    _common(run_parser)
    run_parser.add_argument("--root", default=None, help="repository root the program may read")
    run_parser.add_argument("--inputs", default=None, help="workflow inputs as a JSON object")
    run_parser.add_argument(
        "--digest",
        default=None,
        help="expected sha256 of the source bytes (the entry file alone)",
    )
    run_parser.add_argument(
        "--program-digest",
        default=None,
        metavar="SHA256",
        help="expected program_sha256 of the approved manifest: pins every file the "
        "program is built from, not just the entry",
    )
    run_parser.add_argument(
        "--prices",
        default=None,
        metavar="FILE",
        help="host price table (JSON: operation -> USD amount); makes budget.cost a limit",
    )
    run_parser.add_argument(
        "--record",
        default=None,
        metavar="FILE",
        help="write this run's exchanges (successes and failures) as a v2 recording",
    )
    run_parser.add_argument(
        "--replay",
        default=None,
        metavar="FILE",
        help="answer every crossing from a saved recording; never reaches a host",
    )
    run_parser.add_argument("--evidence", default=None, help="write the evidence bundle here")

    try:
        args = parser.parse_args(argv)
        return _plan(args) if args.command == "plan" else _run(args)
    except HxHostError as error:  # defensive: run_source already turns these into results
        sys.stderr.write(json.dumps(error.as_record()) + "\n")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
