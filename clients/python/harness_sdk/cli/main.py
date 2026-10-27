"""`harness` — the terminal front-end (W6).

    harness login --email you@example.com     # or --token <session token>
    harness run "fix the failing test" --project <id> [--yes] [--json]
    harness status <runId> [--json]
    harness diff <runId> [--json]

Output policy: human text by default; `--json` prints exactly one object to
stdout (progress notes go to stderr), so `harness run --json | jq` is safe.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from ..client import Harness
from ..errors import ApiError
from . import config
from .commands import (
    CliError,
    execute_run,
    format_diff,
    format_steps,
    run_diff,
    run_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Run, follow and inspect Harness runs from a terminal.",
    )
    # Service locations are global: `harness run --orchestrator-url …` must work,
    # not only `login`. (Only login used to define these, so every other
    # command crashed on argparse.Namespace attribute access — found by the
    # CLI/web parity drill, not by unit tests that inject a client.)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--identity-url")
    common.add_argument("--orchestrator-url")
    subs = parser.add_subparsers(dest="command", required=True)

    login = subs.add_parser("login", parents=[common], help="store a session token")
    login.add_argument("--email", help="request an OTP for this address")
    login.add_argument("--token", help="store an existing session token instead")

    subs.add_parser("logout", parents=[common], help="remove the stored token")

    run = subs.add_parser("run", parents=[common], help="propose, approve and follow one run")
    run.add_argument("goal")
    run.add_argument("--project", help="project id (required when you belong to several)")
    run.add_argument("--yes", action="store_true", help="approve without asking")
    run.add_argument("--json", action="store_true", help="one machine-readable object on stdout")
    run.add_argument("--timeout", type=float, default=900.0)

    status = subs.add_parser("status", parents=[common], help="state and recent events for a run")
    status.add_argument("run_id")
    status.add_argument("--json", action="store_true")

    diff = subs.add_parser("diff", parents=[common], help="the run's recorded diff")
    diff.add_argument("run_id")
    diff.add_argument("--json", action="store_true")
    return parser


def _emit(stdout: TextIO, as_json: bool, payload: dict[str, Any], human: Callable[[], str]) -> None:
    if as_json:
        json.dump(payload, stdout, indent=2, sort_keys=True)
        stdout.write("\n")
    else:
        stdout.write(human().rstrip("\n") + "\n")


def _sdk_from_credentials(args: argparse.Namespace) -> Harness:
    token = config.resolve_token()
    if not token:
        raise CliError("not logged in; run `harness login --email …` or set HARNESS_TOKEN")
    stored = config.load()
    identity = getattr(args, "identity_url", None) or (stored.identityUrl if stored else None)
    orchestrator = getattr(args, "orchestrator_url", None) or (
        stored.orchestratorUrl if stored else None
    )
    return Harness(token, identity_url=identity, orchestrator_url=orchestrator)


def _login(args: argparse.Namespace, prompt: Callable[[str], str], stderr: TextIO) -> int:
    if args.token and args.email:
        raise CliError("pass either --email or --token, not both")
    if args.token:
        target = config.save(
            config.Credentials(args.token, args.identity_url, args.orchestrator_url)
        )
        stderr.write(f"token stored at {target} (mode 0600)\n")
        return 0
    if not args.email:
        raise CliError("pass --email for the OTP flow, or --token to store an existing token")
    sdk = Harness(identity_url=args.identity_url, orchestrator_url=args.orchestrator_url)
    try:
        sdk.request_otp(args.email)
        code = prompt("login code: ").strip()
        if not code:
            raise CliError("no code entered")
        body = sdk.verify_otp(args.email, code)
    finally:
        sdk.close()
    token = str((body or {}).get("token") or "")
    if not token:
        raise CliError(f"verify-otp returned no token: {body!r}")
    target = config.save(config.Credentials(token, args.identity_url, args.orchestrator_url))
    stderr.write(f"logged in; token stored at {target} (mode 0600)\n")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    sdk: Any | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prompt: Callable[[str], str] = input,
) -> int:
    args = build_parser().parse_args(argv)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        if args.command == "login":
            return _login(args, prompt, err)
        if args.command == "logout":
            removed = config.clear()
            err.write("credentials removed\n" if removed else "no stored credentials\n")
            return 0
        client = sdk if sdk is not None else _sdk_from_credentials(args)
        if args.command == "run":
            result = execute_run(
                client,
                args.goal,
                project_id=args.project,
                assume_yes=args.yes,
                confirm=prompt,
                note=lambda message: err.write(message + "\n"),
                timeout=args.timeout,
            )
            _emit(
                out,
                args.json,
                result,
                lambda: (
                    "plan rejected; nothing was started"
                    if result["state"] == "rejected"
                    else f"{result['answer']}\n[run {result['runId']}: {result['state']}]"
                ),
            )
            return 0 if result["state"] not in {"error", "failed", "timeout"} else 1
        if args.command == "status":
            payload = run_status(client, args.run_id)
            _emit(
                out,
                args.json,
                payload,
                lambda: f"run {payload['runId']}: {payload['state']}\n{format_steps(payload['steps'])}",
            )
            return 0
        if args.command == "diff":
            payload = run_diff(client, args.run_id)
            _emit(out, args.json, payload, lambda: format_diff(payload))
            return 0
        raise CliError(f"unknown command {args.command!r}")
    except CliError as exc:
        err.write(f"error: {exc}\n")
        return 1
    except ApiError as exc:
        err.write(f"error: {exc.code} ({exc.status}): {exc}\n")
        return 1


def entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":  # `python -m harness_sdk.cli.main` behaves like `harness`
    raise SystemExit(main())
