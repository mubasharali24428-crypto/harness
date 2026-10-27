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
   **Done 2026-10-27.** The answer to "what is remembered": only the history
   block `session.compose_goal` renders into the next turn's goal — it is on
   the wire in the `propose_plan` body, so it is auditable, not implied; and
   "where": the transcript file at `$XDG_CONFIG_HOME/harness/sessions/<id>.json`
   (0700 dir, 0600 file, same contract as credentials). Each turn is a normal
   plan-gated run; `--yes` covers every turn only as an explicit operator
   decision, and a refusal rejects that turn's plan and ends the loop. The
   server has no conversation object — chat deliberately does not invent one;
   if session-scoped memory on the server is ever wanted, that is a contract
   decision. Evidence: `w6-chat-1793092724.json` (two live turns, gate events
   identical to `harness run`, turn 2's wire goal carried turn 1).
4. **Local-workspace mode.** Design first: reuse `ContainerBoundary` /
   `confine` rather than write a second executor; refuse to run when confinement
   is unavailable. No evidence, no feature.

## Slice 4 design — decided 2026-10-27 after the audit

The audit first, because it killed two tempting shapes:

* **Seatbelt is not an option for the real runtime.** `engine.py:619-633`
  refuses it outright (sandbox-exec breaks the dsh PTY, observed, not
  assumed) and fails closed unless the mode is `container` or explicit `off`.
  So "same engine under the same confinement" on a developer laptop means
  the **container** boundary: `confine.wrap_command(mode="container")` +
  `ContainerBoundary` (internal network, model relay, egress proxy) + the
  same `harness-engine-worker:p0` image.
* **The worker daemon's security law forbids caller-shaped paths.**
  `worker_daemon.py:1-15`: paths come only from the daemon's own
  `DSH_WORKSPACE`; the body deliberately carries no path, image or limits.
  A CLI that "passes a local dir" through the daemon would break the design,
  so local mode does NOT reuse the daemon's HTTP surface.

Decision (both options confirmed with the operator 2026-10-27):

1. **Execution site — local engine, server gate.** `harness run --local DIR`
   proposes + approves the plan on the server exactly as before, and does NOT
   call `start_run` (the queue only claims EXECUTING runs, so no phantom
   server execution and no double-run). The actual turn executes on the
   host via a thin launcher that constructs the SAME
   `DeepSeekHarnessWrapper` with the container path (`isolated=True`,
   `HARNESS_ENGINE_CONFINEMENT=container`) — one engine, one confine module,
   one boundary class; the launcher is launch code, not a second executor.
   It follows the established `deploy/runners/engine_boot.py` host-side
   pattern (sys.path bootstrap of section4+contracts), not new section code.
2. **The agent never touches the live workspace.** The launcher seeds a run
   directory (copy of DIR, no `.git`), the confined worker runs there and
   owns the diff (baseline `git init`+commit inside the worker, then
   `git diff` — computed inside the boundary, not by a client-side
   reimplementation); the CLI applies the recorded result back to DIR with
   a file-level copy limited to what the diff names. Rationale: the wrapper's
   run-workspace law (`<root>/<session_id>`, no symlinked or mounted-in user
   dirs) plus keeping `.git` out of the agent's reach; a mount-in design
   would need engine surgery that is a contracts conversation.
3. **Audit story — existing APIs only, honestly bounded.** Server-side:
   `plan_proposed`, `plan_approved`, audit rows for the plan gate (identical
   to a web run up to the execution boundary), the run's patch uploaded as a
   `kind=diff` artifact, and a run comment recording `executed=local`,
   confinement mode, image, and diff sha. Tool-call-level events from an
   off-host engine CANNOT be minted into `agent_events` by any existing
   endpoint — claiming "identical audit events" for local mode would be
   false. Recorded openly: off-host event provenance is a contracts
   decision (1.0.0-rc6 candidate), not a CLI workaround. The CLI prints and
   the evidence file stores the worker's NDJSON so nothing is silently lost.
   All three trace details were corrected by the first live run (2026-10-27),
   each now pinned by a test: the artifact endpoint takes the RAW body as the
   file (the generated stub posts none — `EMPTY_ARTIFACT`), a comment needs
   `refId` alongside `refKind` (`BAD_REF` otherwise), and a drill must check
   the status of the list it reads (a 404 was being reported as "no
   comments"). Showing the worker's progress lines rather than parsing them
   away is what made the looping turn visible instead of mysterious.
4. **Fail closed, from the launcher's first line.** No docker binary, no
   daemon, no `harness-engine-worker:p0`, no live credentials, or
   `HARNESS_ENGINE_CONFINEMENT` resolving to anything but `container` →
   refuse with an actionable message; never degrade to `off` or seatbelt.
   "No evidence, no feature" also applies downward: `--local` prints the
   refusal path rather than attempting an unconfined spawn.

## Slice 4 build order

1. `deploy/runners/local_engine.py` — the launcher (seed dir, wrapper
   construction, container confinement, in-worker diff, NDJSON out).
   **Done 2026-10-27.** Also copies the changed engine output (and its
   deletion list) out of scratch before cleanup, so the CLI applies bytes it
   can actually read instead of a manifest it would have to infer.
2. CLI `run --local` / `chat` turn path through it: seed, launch, apply
   back, upload artifact, comment provenance, `--json` object.
   **Done 2026-10-27** for both: `run --local DIR` and `chat --local DIR`,
   where the directory is a property of the chat session (stored in the
   session file), so `--resume` picks the same workspace back up.
3. Unit tests (no docker on the test path): fail-closed matrix, apply-back
   touches only diff-named files, comment/artifact payloads shaped right.
   **Done** — 22 tests over the launcher, the applier, the trace shapes and
   the chat-local session; 69 total green.
4. Live drill against the running stack: one goal edits a scratch dir,
   server shows plan events + diff artifact + local-provenance comment,
   workspace updated; evidence `w6-local-<epoch>.json`.
   **Partially done — the boundary is proven, the full-success drill is not
   yet green.** Measured 2026-10-27: a live turn inside the container
   boundary created `present.txt` and the in-boundary diff at a 200 s
   deadline (status `error`, `changed: ["present.txt"]` — the worker was
   killed after writing, which is exactly the fail-closed budget behaviour);
   at 240 s the CLI mapped that to `state=error`, the server showed only
   plan events (no server execution), and nothing was applied to the
   workspace because the engine's list was empty at kill time. The wiring
   is exercised end-to-end; the missing piece is a model turn that finishes
   inside the budget, so the drill's default is now 900 s. Two real defects
   the live attempt found and fixed: the applier deleted every untouched
   file (it inferred "deleted" from "absent from the change list") — now only
   the diff's `+++ /dev/null` headers can delete; and the diff header
   normaliser sliced file paths by a fixed offset.

## Known constraints

- The CLI must never hold a service token — only the user's session token.
- `--json` output is a single object; progress goes to stderr, so a pipe stays
  parseable.
- Polling is the first transport; WS streaming comes only when the CLI needs it
  (a scriptable `--follow` that prints events is a different contract).
- The web remains the behavioural reference: if the CLI needs an API change to
  reach parity, that is a contract decision, not a CLI workaround.
