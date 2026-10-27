# Week 6 plan — the CLI (W5–W6)

Follows [Week 4](week-4-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Position

W4's gate is **partially open**: `web_fetch` and the derived egress boundary are
closed with live evidence, while MCP wiring, confined installs and the
credential non-leak drill are not. W5 was folded into the roadmap's single
"W5–W6 — The CLI" item and has no separate plan. Starting W6 therefore means
starting the CLI with three open W4 clauses recorded here, not a finished W4.

## Gate

Roadmap, verbatim:

> A first-class terminal agent (`harness run`, `harness chat`, `harness diff`)
> talking to the same services, same policy gate, same audit trail. Includes
> local-workspace mode with the same confinement, offline token storage, and a
> non-JSONL scriptable output mode.
>
> **Gate:** the same task completes from the CLI and the web, with identical
> audit events.

Four claims to show:

1. a run started from the CLI reaches the same orchestrator gate (plan →
   approval → start) and produces the **same event sequence** as the same run
   driven over HTTP;
2. `harness diff` shows the run's real diff, not a client-side reimplementation;
3. the session token survives a restart in an offline store with owner-only
   permissions;
4. `--json` output is one machine-readable object per invocation — no decoration,
   no JSONL requirement.

## What exists today (audited 2026-10-26)

| Piece | State | Where | Gap |
|---|---|---|---|
| Typed API client | **exists** | `clients/python/harness_sdk` — 104 generated operations plus a handwritten transport (bearer, envelope, retries, WS replay) | no command surface |
| Run lifecycle SDK calls | **exists** | `propose_plan`, `approve_plan`, `reject_plan`, `start_run`, `get_run`, `get_engine_result` | none |
| Diff read | **exists** | `run_diff(run_id)` | none |
| CLI entry point | **absent** | — | nothing installs a `harness` command |
| Token storage | **absent** | `env_token()` only reads `HARNESS_TOKEN` | no offline store, no 0600 discipline |
| Scriptable output | **absent** | — | every surface is Python |
| Local-workspace mode | **absent** | engine confinement lives server-side (`ContainerBoundary`, `confine.scrubbed_env`) | a local executor must reuse it; it cannot be a second, weaker path |
| Chat | **absent** | — | needs a session story before it is more than a loop over `run` |

## Decisions owed before code

1. **Where the CLI lives — decided 2026-10-26.** Inside the Python client
   distribution (`harness_sdk.cli`) with a `harness` console script. One package
   to install, one transport, one error envelope. A separate distribution can
   come later if the CLI grows its own release cadence.
2. **Auth.** `harness login --email` runs the OTP flow (dev reads the code from
   identity logs); `--token` stores an existing session token without a network
   call. Store under `$XDG_CONFIG_HOME/harness/credentials.json`, directory
   `0700`, file `0600`, atomic replace. `HARNESS_TOKEN` overrides the file.
3. **Approval is not bypassable.** `harness run` shows the plan and asks before
   `approve_plan`; `--yes` is an explicit operator decision, and `--json` never
   implies approval. A denied prompt calls `reject_plan`, so nothing is left
   pending.
4. **What `run` executes.** A server-side run through the same API the web uses.
   Local-workspace mode is a later slice and must be the same engine under the
   same confinement — not a subprocess shortcut.

## Slices, in order

1. **Scaffold, credentials, `login`/`logout`.** Console script, config module
   with the permission contract, and tests. No network in the default test path.
2. **`run`, `status`, `diff`.** Plan → show → approve → start → follow → answer;
   `--json` for each. Then the parity drill: one goal from the CLI and the same
   goal over HTTP, comparing `agent_events` rows.
3. **`chat`.** An interactive loop over the same run path, with the session
   identity made explicit (what is remembered between turns, and where).
4. **Local-workspace mode.** Design first: reuse `ContainerBoundary` /
   `confine` rather than write a second executor; refuse to run when confinement
   is unavailable. No evidence, no feature.

## Known constraints

- The CLI must never hold a service token — only the user's session token.
- `--json` output is a single object; progress goes to stderr, so a pipe stays
  parseable.
- Polling is the first transport; WS streaming comes only when the CLI needs it
  (a scriptable `--follow` that prints events is a different contract).
- The web remains the behavioural reference: if the CLI needs an API change to
  reach parity, that is a contract decision, not a CLI workaround.
