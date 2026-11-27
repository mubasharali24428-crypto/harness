# AI-specific threat model (G-G) — 2026-11-21 (P6)

Scope: what an LLM (or text that steers one) can do inside the AI Engineering
Harness that a normal web threat model misses. Complements docs/sandbox-isolation.md
(the OS boundary) and the P0 findings ledger. Owner-facing summary; the
controls column names the enforcing code.

## Trust boundaries and what crosses them untrusted

| # | Channel | Untrusted content | Reaches |
|---|---|---|---|
| C1 | Repo content (files, READMEs, source) | attacker-committed text if a pilot team's repo is public/PR-driven | the model's context every turn |
| C2 | Issue / PR / review text | third-party prose | goal text, model context |
| C3 | MCP tool output | external servers' results | model context (tool results) |
| C4 | Goal images | pixels only | model (vision); image_gate re-checks outputs |
| C5 | Web/eGRESS tool output | web pages fetched at run time | model context |
| C6 | Provider responses | whatever the model emits | tool-call dispatch (bash, edits, MCP) |

## Threats (T) and the control that must hold

- **T1 Prompt injection via repo/issue text (C1/C2 → C6):** injected text
  orders the model to widen scope.
  Control: intent scopePaths are ENGINE-enforced at argument level
  (`shell_gate.py` scope check, deny + scope-request flow; W27 denial drill
  `pilot-prep/w27-denial.json` proves a denied path stays unread, marker never
  leaks, workspace unchanged) + Landlock/Seatbelt write roots (M03) + the
  plan approval gate (a plan with new paths needs a HUMAN approval again —
  intent edits are approval-requiring, `docs/decisions/2026-11-05-intent-permissions.md`).
- **T2 Self-approval / plan forgery:** model text claims "the user approved".
  Control: approval is a DB state transition only a human session reaches
  (`AWAITING_PLAN_APPROVAL`; MCP start_run does NOT auto-approve — S-04
  disposition pending re-verification in P3 follow-up); no model output is
  ever read as an approval signal.
- **T3 Exfiltration through allowed egress:** injected command smuggles repo
  secrets to an allow-listed host.
  Control: egress allow-list proxy is CONNECT-443-only with purpose ports;
  shell gate blocks non-allow-listed binaries (curl etc. are NOT on the
  binary allow-list — the model cannot open arbitrary sockets); provider
  keys never enter the workspace env (P2 sanitize); `w4-egress-network`
  drills prove blocked egress shapes.
- **T4 Evidence forgery (fake test pass):** model prints "✅ tests passed".
  Control: CI/verify path runs the REAL suite (`verify.tests` seam); the
  harness records tool calls + hashes (`agent_tool_calls`, forbidden-path
  post-run hash check), so a claimed pass without the run is detectable; the
  audit chain (migration 0036 hash chain + external anchor) makes tampering
  evident.
- **T5 Cost exhaustion / denial-loop:** injected goal makes the model loop on
  denials burning budget.
  Control: denial circuit breaker (W27, `w27-breaker.json`), per-run tool-call
  limits enforced in-run, budget ceiling checks against the host price table
  before each crossing (M6), tier caps.
- **T6 Tool-output smuggling (C3/C5):** MCP result or fetched page contains
  text crafted as instructions.
  Control: tool outputs are DATA to the model; the same T1 controls apply to
  any action the model takes on them (nothing auto-executes; every shell
  command passes the gate; every write passes scope roots).
- **T7 Goal-image abuse (C4):** pixels embed instructions the vision model
  obeys. Same as T1 once it becomes an action attempt.
  Control: image_gate on outputs; all action-level controls unchanged.

## The one-line law

Model output is NEVER an authority: not an approval, not a scope grant, not a
policy exception. Every path to a real effect crosses a control that does not
read model text (DB approval state, argument-level gate, OS confinement,
allow-listed egress, human-typed verdict).

## Replay fixture requirement (P6 exit check)

Fixtures where the model OBEYS injected text and attempts each T1–T5 action
must come back: stopped, recorded as a denial event, visible in the UI, no
paid calls (stub provider). See scripts/e2e replay stub in the perf audit;
replay cases land in section4 tests (next slice, G-G remainder).
