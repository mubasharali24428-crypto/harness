# Capability review: Claude Code, Codex, Cursor against the harness (W29)

Fetched 2026-11-05. Scope: the controls that matter for the roadmap's governance and delegation
stages (approvals, sandbox, subagents, denials, hooks, cloud agents). It supersedes the product
claims in `capability-gap-2026-10-25.md` where they differ; that file's harness rows are not
re-verified here.

## How to read the evidence

* **No page shows a last-updated date.** The only dates are the fetch date above and the dated
  changelog entries Cursor's run-modes page carries. Every "current" claim therefore means
  "as served on 2026-11-05", not "as of a release".
* Pages were read through a fetch tool that truncates long pages. Facts marked **[v]** come from
  verbatim page text; **[s]** come from a model summary of the part the tool could not return
  verbatim. Treat **[s]** as a lead to confirm, not a quotation. The Claude Code hooks page was
  mostly summarised (about 40k of 286k characters verbatim).
* OpenAI's `developers.openai.com/codex/...` URLs redirect (HTTP 308) to
  `learn.chatgpt.com/docs/...` copies with the same paths; the copies were read.
* Search-result snippets were used only to find pages, never as evidence.

## Sources

| Product | Page | Date shown |
|---|---|---|
| Claude Code | https://code.claude.com/docs/en/sub-agents | none |
| Claude Code | https://code.claude.com/docs/en/permissions | none |
| Claude Code | https://code.claude.com/docs/en/sandboxing | none |
| Claude Code | https://code.claude.com/docs/en/hooks | none |
| Codex | https://learn.chatgpt.com/docs/agent-approvals-security | none |
| Codex | https://learn.chatgpt.com/docs/agent-configuration/subagents | none |
| Codex | https://learn.chatgpt.com/docs/sandboxing | none |
| Codex | https://learn.chatgpt.com/docs/sandboxing/auto-review | none |
| Cursor | https://cursor.com/docs/agent/security/run-modes | changelog entries dated May 22, May 29 and Oct 1, 2026 |
| Cursor | https://cursor.com/docs/subagents | none |
| Cursor | https://cursor.com/docs/hooks | none |
| Cursor | https://cursor.com/help/ai-features/background-agents | none |

## Findings by area

### Sandbox: what is actually enforced

| | Claude Code | Codex | Cursor |
|---|---|---|---|
| Boundary | OS sandbox for Bash/PowerShell/Monitor commands and their children; **off by default** [v] | OS sandbox on spawned commands; read-only / workspace-write / danger-full-access [s] | Seatbelt (macOS), Landlock+seccomp (Linux, kernel 6.2+); falls back to asking if unavailable [v] |
| Network | no direct route; proxy checks `allowedDomains`, which starts empty [v] | off by default [v]; `network_access` opt-in [s] | blocked by default, opened by network mode and `sandbox.json` [v] |
| Not covered | built-in Read/Edit/Write/WebFetch/WebSearch, command hooks, local MCP servers, LSP [v] | not stated in what was read | Run Modes call Auto-review "not a security boundary" [v] |
| Escape hatch | model may retry with `dangerouslyDisableSandbox`, approval depends on mode [v] | `--yolo` removes sandbox and approvals [s] | Run Everything: no sandbox, no classifier [v] |

The harness's own boundary is container mounts plus Seatbelt/Landlock behind a command gate, with
the scope paths bound into the mounts (drilled this session: `m03-scope`, W27 denial drill). The
shared lesson across all three products matches the harness's earlier M01 finding: **a rule on
command text is not the boundary; the OS layer is.** Claude Code says so about its own
permission rules (`Bash(curl *)` does not stop `sh -c 'curl ...'`) [v].

### Approvals

* Claude Code: modes `default`, `acceptEdits`, `plan`, `auto` (classifier), `dontAsk`,
  `bypassPermissions`; rules evaluated deny, then ask, then allow; managed settings outrank
  user settings [v/s]. Admins can disable bypass and auto [v].
* Codex: approval policy is separate from the sandbox (`on-request`, `never`, `granular`;
  `untrusted` retired) [s]. `approvals_reviewer = "auto_review"` routes eligible boundary
  requests to a reviewer agent; the page calls this a reviewer swap, not a permission grant [s].
* Cursor: Auto-review (default since 3.6, May 29 2026), Allowlist, Run Everything [v]. Cloud
  Agents do not use Run Modes and never ask for approval [v].

Gap for the harness: none of the three binds an approval to a versioned plan the way the
harness's run-intent versions do (approval is voided by an intent edit). That is a harness
strength, but only on the evidence read here, which did not include the products' plan-mode
internals. Not claimed as a verified difference.

### Subagents and delegation

| | Claude Code | Codex | Cursor |
|---|---|---|---|
| Context | own window, fresh; a fork inherits [v/s] | parallel threads, results collected [v] | own window, clean start [v] |
| Permissions | `permissionMode` per subagent, but ignored when the parent is in bypass/acceptEdits/auto [s] | **inherit parent sandbox and approvals; a custom agent file cannot override** [s] | readonly flag blocks edits and state-changing shell [v] |
| Isolation | optional temporary git worktree [v] | not stated | shared checkout by default; optional worktree or cloud VM [v] |
| Cancel | `x` in panel or `/tasks`, `TaskStop`, SDK `stop_task` [s]; `maxTurns` ends it [v] | panel stops all; ask Codex to stop one [s] | **cancellation not documented** [v] |
| Nesting | not read | not read | one level only [v] |

The harness's W28 differs in the places that the roadmap cares about: every child is its own run
needing human plan approval, scope paths are bound to the child intent and an empty scope is
refused, cancel cascades to children (`_stop_child_runs`), and there is a reviewable read-only
synthesis with sibling path-overlap detection. Cursor documents no cancellation, and Codex
subagents inherit rather than narrow permissions. Not shown by this review: any of the three
products' behaviour under a lost lease or crash, which the pages do not describe.

### Denials: reason, alternative, requesting more scope (W27)

* **Codex** is the only one with a documented denial protocol, and only for auto-review: the
  reviewer's rationale goes back to the model, which is told not to reach the same outcome by a
  workaround, and may continue only with a materially safer alternative or stop and ask the
  user [s]. A circuit breaker interrupts after 3 consecutive denials or 10 in 50 [s]. `/approve`
  gives one retry of a recent denied action [s]. Timeouts are reported separately from denials [s].
* **Claude Code** hooks can block with a reason: exit code 2 turns stderr into the reason [s];
  PermissionDenied fires after a denial but cannot change it [s].
* **Cursor** hooks: exit code 2 blocks; permission responses are allow, deny or ask, and deny beats
  ask beats allow [v].
* Not established from the pages read: whether the model sees a reason for a plain sandbox or rule
  denial in each product, and any generic "grant more scope" flow beyond the approval prompt.

The harness's W27 slice 1 (reason, `code`, safe `alternative`, `requestScope` path, no retry or
model-change suggestion) is comparable to Codex's auto-review rationale in intent. The harness has
no circuit breaker on repeated denials and no one-click scope request; both are real gaps against
Codex's documented behaviour.

### Hooks

Claude Code lists 33 lifecycle events, with PreToolUse, PermissionRequest, UserPromptSubmit,
PreModelSwitch and several others able to block [v/s]; hooks also run inside subagents [v].
A PreToolUse hook that times out does not block by default, and `onFailure: "block"` changes that
(v2.1.295+) [s]. Cursor: blocking hooks include beforeShellExecution, beforeMCPExecution,
beforeReadFile, subagentStart; most `after*` hooks are observational [v/s]; in cloud agents only
command hooks run and several event types are unavailable [v].

Harness: no user-defined hook layer exists. Policy is fixed in the gate and the intent. Whether
the roadmap needs one is a product decision, not something this review settles.

### Cloud and remote execution

Cursor cloud agents run on a dedicated VM with secret redaction and network policy, open PRs
automatically, and the page describes no human approval gate before merge [v]. Claude Code and
Codex remote execution was not read in this pass. The harness's W13 BYO runner is the comparable
piece; its remote-host evidence is a roadmap exit criterion that this review does not provide.

## What this changes for the harness

1. **Denial circuit breaker (new gap, cheap):** repeated identical denials in one run should stop
   the run for the user instead of letting the model probe. Codex documents the thresholds (3
   consecutive, 10 of 50).
2. **Distinguish timeout from denial** in the gate's reply; Codex tells the model a timeout alone
   does not mean unsafe.
3. **Scope request as a first-class object** rather than prose telling the user to edit the
   intent. The `requestScope` field from W27 is the hook for it.
4. **Do not claim parity** with Claude Code's hook system or Codex's reviewer agent. The harness
   has neither.
5. **Strengths to keep stating only with evidence:** per-child plan approval, bound child scope,
   cascading cancel, read-only synthesis. These are drilled in `docs/briefs/evidence/w28/`.

## Update after the review (2026-11-05)

Items 1 to 3 above now exist in the harness as a first slice (see `STATUS.md`, W27 slice 2): a
denial circuit breaker with Codex's thresholds, a timeout marked apart from a denial, and a
`scopeRequest` object. A later slice makes the orchestrator end the run on repeated denials
and carry the scope request on the tool-result event. Gaps that remain: no UI reads the scope
request, and the UI and the scope-miss path have no live proof.

## Limits of this review

* No page dates; versions appear only as feature requirements (for example Claude Code
  v2.1.295 on hooks and v2.1.310 on sandboxing).
* Large parts rest on tool-generated summaries [s]. Anything that will drive a decision should be
  re-read in full by the owner or an independent reviewer (this is the hand-off W33 asks for).
* Claude Code, Codex and Cursor changelogs, pricing, models, IDE features and enterprise controls
  were not reviewed. Memory, rules/instructions files, MCP and skills were out of scope.
* Search was US-only and limited to the official domains listed above.
