# Marathon 2.0 W5 — CLI diagnostics, evaluation capture, parity (M2-W5)

Follows the 2026-10-28 Marathon 2.0 roadmap, W5 row, and
[the W4 outcome](m2-w4-plan.md). This is **Marathon 2.0 W5**, distinct from the
historical W5–W6 CLI work, which is already in the tree.

Governing rule: *an unchecked gate stays open when a dependent week begins.*
W4's exit claims are checked; this row adds the machine contract a CLI needs to
be usable by an evaluation, and re-proves parity rather than assuming it.

## Purpose

The W5 row asks for the CLI to have the **same role/approval/audit behavior as
the web**, **secure token handling**, **tested reconnect**, and — new in
Marathon 2.0 — **evaluation capture with stable diagnostic outputs**.

The audit found the first three largely present (`cli_parity.py`, a 0600/0700
token store, SDK-level WS reconnect with seq dedupe). What was missing was a
machine contract: every failure exited 1, `--json` errors printed only to
stderr, there was no capture path, and the parity drill asserted a diff for a
goal that edits nothing.

## Audit (2026-10-28)

| Area | What exists | Where | Finding |
|---|---|---|---|
| Commands | `login`, `logout`, `run`, `status`, `diff`, `chat` (single-turn and interactive) | `clients/python/harness_sdk/cli/main.py` | `--json` prints one object per command; progress goes to stderr |
| Token store | `credentials.json`, 0600 in a 0700 dir, atomic replace; `HARNESS_TOKEN` wins over the file | `cli/config.py` | Owner-only and env-injectable; no token ever printed |
| Approval path | `execute_run` propose → confirm/`--yes` → approve → start; refusal rejects the plan | `cli/commands.py:140-260` | Same gate as the web; no implicit approval |
| Audit | Both paths reach the same event log; parity reads it | `scripts/e2e/cli_parity.py` | Lifecycle equality proven, not answer equality |
| Reconnect | SDK WS client resumes from `last_seq` with jittered backoff; HTTP retries exist in the transport | `ws.py`, `runtime.py`, `test_sdk.py` | Present for streaming; a dropped **poll** in `run` still aborted the command |
| Diagnostics | `ApiError` carries the contract code/status | `errors.py` | Not surfaced machine-readably; exit code was always 1 |
| Evaluation capture | none | — | An evaluator had to parse human output or re-call the API |
| Parity drill | CLI and HTTP lifecycle compared | `cli_parity.py` | Called `diff` and required 200 for a run that makes no changes — a drill defect, since the server correctly 404s without a done-signal diff |

## The contract added

`clients/python/harness_sdk/cli/diagnostics.py` is now the single definition.

| Exit | Name | Meaning |
|---|---|---|
| 0 | `OK` | command completed |
| 1 | `FAILURE` | completed with a failed state, or a non-policy 4xx |
| 2 | `USAGE` | argparse refused the command line |
| 3 | `AUTH` | token missing/rejected (401, `NOT_LOGGED_IN`, `INVALID_SESSION`) |
| 4 | `POLICY` | server refused on a rule (approval, role, pilot gate, lease, stale edit) |
| 5 | `TIMEOUT` | the CLI's own deadline elapsed |
| 6 | `TRANSPORT` | connection/5xx class; the only class retried |
| 130 | `INTERRUPTED` | Ctrl-C |

Every `--json` invocation prints exactly one object. Success carries
`schemaVersion`, `command`, `ok`, `exitCode` plus the command payload. Failure
carries the same four keys plus `error: {code, message, retryable, status?}`; the
human line still goes to stderr. `--capture PATH` writes the identical object
atomically with 0600, including failures, so a pipe and a capture cannot
disagree.

**Bounded reconnect.** `execute_run` retries `get_run` when the error classifies
as transport (status 0/408/429/5xx) at most three times with jittered backoff,
while the run deadline holds. It records `reconnects` and `lastTransportError` in
the result. A policy or 4xx refusal propagates immediately — a dropped poll is
not a failed run, and a denial is not a flaky connection.

## Slices

1. **Diagnostics module** — classification, contract keys, usage/interrupt
   payloads, atomic capture, shared backoff.
2. **CLI wiring** — one-object errors, `--capture` on `run`/`status`/`diff`/`chat`,
   `durationSeconds` on run/chat, exit codes from state.
3. **Reconnect** — bounded transport retry in the run poll loop, recorded in the
   payload.
4. **Parity drill repair** — when the server has no done-signal diff, the CLI
   must report the same 404 `NOT_FOUND` instead of the drill demanding a payload.
5. **Deterministic drill** — `scripts/e2e/w5_cli_diagnostics.py` plus the new
   `cli-diagnostics` corpus task.

## Outcome (2026-10-28)

**Diagnostics drill: 10/10 PASS in 1.59 s**, no model —
`docs/briefs/evidence/eval/w5-cli-diagnostics-1793196674.json`, runner record
`baseline-20261028T141128Z.json` (`revisionVerified: true`):

| Check | Observation |
|---|---|
| no token | exit 3, `NOT_LOGGED_IN`, one object |
| usage error | exit 2, `USAGE` object |
| unknown run | exit 1, server code `NOT_FOUND`, status 404 |
| capture equality | captured file parses equal to stdout |
| capture mode | 0600 |
| token store | file 0600 in dir 0700; logout removes it |
| token secrecy | token absent from stdout, stderr and capture |
| real run read | run created via the web API reads with exit 0, state `awaiting_plan_approval`, capture matches |
| success object | `ok: true`, `exitCode: 0` |

**CLI/web parity: PASS in 17.3 s**, live model —
`baseline-20261028T141223Z.json`, `pilot-prep/w6-cli-parity-1793196746.json`:

| Check | Observation |
|---|---|
| lifecycle | both paths: `plan_proposed → plan_approved → execution_started → engine_completed` |
| terminal state | both `awaiting_done_signal` |
| token store | 0600 in 0700 |
| `--json` | one object, no trailing output |
| diff absence | CLI reports the server's 404 `NOT_FOUND`; the drill now checks agreement on absence |

**Tests: 78 passed.**
`clients/python/tests/test_cli.py` — 28 (token, gate, JSON, capture, exit codes,
reconnect, token secrecy); `test_sdk.py` — 29 (envelope parsing, HTTP retry,
WS reconnect/backoff/dedupe); `test_cli_local.py` — 21 (local mode, run in the
Section 4 venv because it imports `local_engine`/pydantic).

## Gate position

| W5 exit claim | State |
|---|---|
| Same role/approval/audit behavior in CLI and web | **closed at this revision** (parity lifecycle + real run read) |
| Secure token handling | **closed** (0600/0700, env override, never in output or capture) |
| Reconnect tested | **closed** (bounded poll retry; WS backoff/dedupe tests) |
| Stable diagnostic outputs | **closed** (exit table + one-object errors, drilled) |
| Evaluation capture | **closed** (`--capture` equality and mode) |

## Limitations

- Reconnect covers the HTTP poll in `run`. The event stream's reconnect is
  SDK-level (`WsEventStream`, tests in `test_sdk.py`), and the CLI does not yet
  stream events for a run.
- The parity goal makes no edits, so parity is proven on lifecycle and on diff
  *absence*. A diff-present comparison exists in the drill but is not exercised
  by this goal.
- `test_cli_local.py` needs the Section 4 environment (`pydantic`,
  `harness_orchestrator.confine`); the minimal client venv cannot collect it.
- The live runs use the stack model; lifecycle equality does not depend on model
  output, but a model that never calls a tool would still settle the same way.
- No new commands or capabilities were added; this row is a contract and a
  retry, not reach.

## Carry-over

- **W6:** skills/hooks lifecycle; the CLI remains the reference path for
  approval evidence.
- **W7:** read-only LSP integration; the W4 client is the starting point.
- **W8:** concurrent runs; the transport retry here is per-command, not a
  background reconnect.
