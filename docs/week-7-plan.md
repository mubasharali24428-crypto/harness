# Week 7 plan — the IDE extension (W7)

Follows [Week 6](week-6-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Position

W4 is closed: the MCP leg, the confined-install leg and the credential
non-leak drill each have live evidence (see `week-4-plan.md`). W6 is closed:
`harness run`, `chat` and local-workspace mode share one gate, one engine and
one confinement path with the web.

W7's prerequisite in the roadmap is "shared CLI/API/control path" — that is the
thing W6 built, so the extension is a *client of that path*, not a new one.

## Gate

Roadmap, verbatim:

> Inline diff at the point of work, `@`-symbol context picking (files, symbols,
> git hunks), selection-as-context, status bar run indicator, approve/reject
> from the editor.
>
> **Gate:** an edit proposed in-IDE round-trips through the harness policy gate —
> the extension must not become a way to bypass confinement.

Expanded into five claims that must each be shown:

1. a request made from the editor enters the **same** gate (propose → approve →
   start) as the CLI and the web, and the goal on the wire carries exactly the
   selection/symbol/hunk context the editor showed;
2. the turn executes in the **confined worker container** — the extension host
   is not an execution site, and there is no "run it here" path to add later;
3. **approve/reject in the editor names bytes**: the decision is
   `review_change` bound to the `diffSha` the editor displayed, and a decision
   against a different patch is refused by the server (`CHANGE_STALE`);
4. **applying is a local edit the human triggered** after review, refused when
   the file changed since the diff was shown, and never executed by the agent;
5. **context is bounded and visible**: a selection larger than the cap is
   truncated or refused *with the fact recorded*, so "what the model saw" is
   answerable from the audit.

## What exists today (audited 2026-10-27)

| Piece | State | Where | Gap |
|---|---|---|---|
| Typed TS client | **exists** | `clients/ts` — 104 generated operations + transport + WS event stream; `dist/` builds | no command surface, no editor activation |
| Plan gate calls | **exists** | `orchestrator.propose_plan`, `approve_plan`, `reject_plan`, `start_run`, `get_run_status` | none |
| Diff read | **exists** | `orchestrator.run_diff(runId)`; `GET /runs/{id}/changes` per-file view | no TS reader for the change list shape |
| Change review **bound to bytes** | **exists** | `review_change(runId, changeId, {decision, diffSha})` — `CHANGE_STALE` when the digest differs, one decision per row, compare-and-swap (`section4/.../artifacts.py:1193`) | nothing in an editor calls it |
| Apply-back | **exists (Python)** | `harness_sdk.cli.local.apply_back` + `deletions_from_diff` | a TS applier does not exist; the CLI's `git apply`-style path is not importable here |
| VS Code extension | **absent** | — | everything below |
| Unified-diff parsing in TS | **absent** | — | needed for inline diff and for a safe apply |
| Symbol context | **absent** | — | VS Code's `DocumentSymbolProvider` answers it; no language server is required for the spike |
| Git-hunk context | **absent** | — | `git diff` output is the source; parsing must be strict |
| Status bar | **absent** | — | — |

## Decisions owed before code

1. **Where the extension lives — `clients/vscode`.** A sibling of `clients/ts`,
   not a new section: sections are isolated services that may import only
   `harness_contracts`, and an editor is a client. It depends on
   `../../ts/dist` by relative path (no npm workspace exists in this repo, and
   inventing one to share one package is a build-system change, not a W7 one).
2. **Transport — the typed client, not a subprocess.** Shelling out to `harness`
   would make the extension's policy behaviour depend on another binary's
   version. The SDK is the same code path the web uses.
3. **Apply — `WorkspaceEdit`, computed by a strict TS applier.** No shelling to
   `patch`/`git apply`: the bytes the editor shows are the bytes it writes, and
   an applier that cannot reproduce them **refuses** instead of guessing.
   Deletions and creations are explicit; binary patches are refused.
4. **Approve/reject — `review_change` with the displayed digest.** Plan approval
   stays `approve_plan` (the editor shows the plan text first). Both are policy
   gates; neither is replaceable by a local prompt.
5. **How the gate is proven without a desktop VS Code in this environment.**
   All behaviour lives in `src/core/*` with **no `vscode` import**; the
   activation layer is a thin adapter. `test/` drives the core against the live
   stack in Node (auth, plan gate, run, changes, review, diff, apply) and a fake
   editor. The `vscode`-bound glue is typechecked and covered by a manual smoke
   script that is **documented as not run here** — claiming otherwise would be
   the exact dishonesty this project's evidence rule exists to prevent.

## Slices, in order

1. **Context builder.** Pure module: selection → goal block with byte counts and
   a hard cap; `@file`, `@symbol` entries rendered deterministically; git hunks
   from a strict `git diff` parser. Tests pin the cap, the refusal message and
   the exact wire text.
2. **Diff and apply.** Pure module: parse the server's unified diff into
   per-file patches; apply to text with exact context matching; refuse on
   mismatch, binary content, or a path outside the workspace. Tests cover
   create/delete/modify/mismatch/escape.
3. **Run controller.** Adapter-shaped core (injected transport + injected
   editor): login token → propose → approve → start → poll → read changes →
   review. Tests against a scripted fake transport; no model calls.
4. **Extension shell.** `package.json`, commands (context pickers, run, approve,
   reject, apply, status), status bar item, virtual-document diff provider,
   SecretStorage token. Typecheck + lint gate.
5. **Gate drill.** Live: a selection becomes a goal, the run executes in its
   container (observed, mode named), the diff is shown, a wrong digest is
   refused, the right digest approves, the applier produces the reviewed bytes,
   and a stale file is refused. Evidence `w7-ide-*.json`.

## Known constraints

- The model in this stack is local and slow; the drill's timeout budget must be
  larger than the product default or the evidence ends as a deadline kill.
- No `vscode` module is available outside VS Code; `src/core` must stay free of
  it or the tests cannot run.
- The extension never holds provider credentials: it holds a session token, in
  SecretStorage, and nothing else.

## Slice status (2026-10-27)

Slices 1–4 are built and tested; slice 5 (the live drill) has not been run yet.

| Piece | State |
|---|---|
| `src/core/context.ts` | selection/file/symbol/hunk items, hard cap, drop-from-the-end and code-point truncation **recorded in the goal text itself**, workspace-relative path enforcement. Tested. |
| `src/core/diff.ts` | digest verification (`sha256(patch) == diffSha` or nothing is shown), strict hunk apply with exact context, unique-match relocation only, create/delete/no-trailing-newline handling, refusal for binary, symlink, rename-only, omitted patch and path escape, plus `splitPatchByFile` for the pre-completion preview. Tested. |
| `src/core/run.ts` | the whole round-trip with no `vscode` import: render goal → propose → show plan → approve/reject → start → poll → **read the engine's proposed diff → show it → ask the operator to confirm → `done-signal` with exactly those bytes** → re-read the recorded changes → per-file reviewed diff → `review_change(diffSha)` → stale re-check → write. `awaiting_done_signal` is a settled state: the engine finishing is not the run finishing. Tested (29 tests). |
| `src/adapter/sdk.ts` | the typed TS SDK as the transport; `describePlan` renders the plan text the operator approves. |
| `src/adapter/vscode.ts` | the editor port: modal plan/result/apply prompts, `vscode.diff` tabs labelled *proposed* vs *reviewed*, virtual documents, workspace file IO, and `git diff -- <file>` hunks offered as `@`-context (absent, never guessed, when git is unavailable). |
| `src/extension.ts` | activation, `login`/`logout`/`run`/`status`, token in SecretStorage only, status bar. |
| `scripts/w7_ide_drill.ts` | written; **not run yet**. Drives the core against the live stack with a directory-backed fake editor, observes the confined worker container, and proves a review bound to the wrong digest is refused (`CHANGE_STALE`). |

Two things the flow had to learn from the product rather than assume:

* the server only records a run's diff when a human confirms it
  (`POST /runs/{id}/done-signal`, body `{diffText}`), and `/changes` +
  `review_change` bind to *that* text — so the editor has two approvals, the
  plan and the result, and the second one is what makes digests exist;
* `clients/ts/dist` was 12 files stale against its own `src` (no `run_diff`,
  `list_changes`, `review_change`), which is why the extension did not compile
  at first. Rebuilt and committed with it.

**Honest limit, stated rather than hidden:** the `vscode`-bound adapter is
typechecked (`tsc --noEmit` clean) and built, but no VS Code window was opened
in this environment. The gate's policy claims are proven through the core;
"the tab title looks right in an editor" is not claimed, and the drill's
evidence file records `drive: … vscode adapter typechecked, not opened`.

## W4 dependency, still open at the same time

The credential non-leak drill (`scripts/e2e/w4_credential_leak_drill.py`) is
rerunning after the NUL audit-row fix (commit 4b2f300). Until it is green, W4
has one open clause and this week is running alongside it, per the governing
rule.
