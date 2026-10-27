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
   version. The SDK is the same code path the web uses. *Amended 2026-10-27 by
   decision 6:* this holds for the API surface; execution is a subprocess of
   `deploy/runners/local_engine.py` — the very script the CLI executes, so the
   launcher stays single-implementation even though two clients invoke it.
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

Three things the flow had to learn from the product rather than assume:

* the server only records a run's diff when a human confirms it
  (`POST /runs/{id}/done-signal`, body `{diffText}`), and `/changes` +
  `review_change` bind to *that* text — so the editor has two approvals, the
  plan and the result, and the second one is what makes digests exist;
* a server-side run has **no** proposed diff anywhere in its result (decision 6),
  which is why execution is the confined local runner;
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

## Decision 6 — where an IDE run executes, and why the answer is not "the server"

Found while wiring the drill, not while writing prose: **a server-side run has
no proposed diff to review.** `/runs/{id}/diff` and `/changes` read
`get_done_diff`, and the only writer of that value is `signal_done` — the
human's `done-signal`. `engine_result` carries no diff for a server run (only
W6's local launcher computes one, `git diff --no-index` inside the container),
and the web UI's Done button sends text a human typed. So a client that wanted
an inline diff *before* confirming one would have to invent it from tool
results: fabricated bytes, reviewed as if they were the run's.

Two consequences, both taken seriously:

1. **The IDE runs W6's local path.** `src/adapter/local.ts` spawns the same
   `deploy/runners/local_engine.py` the CLI spawns, with the same arguments and
   the same container boundary (`preflight` refuses anything but
   `container`, and the seatbelt/off paths are refused regardless of server
   policy). One launcher, one `confine` module, one `ContainerBoundary` — the
   extension adds a UI, not an executor. It also does **not** call
   `start_run`: the queue only claims EXECUTING runs, and starting one would
   double-execute a plan whose work already happened on the operator's machine.
2. **The IDE run then supplies the diff the product lacks.** The operator
   confirms the patch the confined run produced, and that text goes up as the
   `done-signal`. `/changes` and `review_change(diffSha)` then exist for an
   editor flow for the first time — which is what makes "approve/reject from the
   editor names bytes" provable rather than aspirational.

**What this costs, stated plainly:** an IDE run emits `plan_proposed` and
`plan_approved` on the server but no `execution_started`, and no per-tool
`agent_events` — the same audit shape W6 recorded for `harness run --local`.
The confinement claim is carried by the container the run actually executes in
(observed, read-only root, dropped caps, internal network) plus the recorded
diff and the per-file review rows, not by an event the off-host path cannot
truthfully mint.

**Cross-check, because two derivations of a file can disagree:** the launcher
copies the run's changed files to a scratch dir; before writing, the controller
compares the applier's output with that copy and refuses if they differ. The
editor never writes bytes that only one of the two produced.

### One consequence of decision 6 that deserves its own sentence

`DONE_SIGNAL_STATES` is *every non-terminal state* and the state machine's
`signal_done` agrees: the done-gate is a **role + evidence** check
(`signals.py`: executor role, non-empty diff), not a proof that this machine
executed anything. So an IDE run — approved on the server, executed on the
operator's machine inside the container boundary — can legitimately be
completed and reviewed there, which is exactly what the flow needs. It also
means the completion row is not evidence of *where* work happened, so the
evidence names the confinement instead: the drill observes the worker container
(read-only root, dropped caps, internal network) while the run is live, and
records `serverEventTypes` — `plan_proposed, plan_approved,
done_signal_received, review_*` with no `execution_started` — rather than
claiming a server-side tool audit it cannot produce (same limit W6 recorded for
`harness run --local`).

## Slice 5 outcome — the gate is shown, not asserted (2026-10-27)

`clients/vscode/scripts/w7_ide_drill.ts` drives the extension's **own core**
against the live stack with a directory-backed fake editor. Evidence
`w7-ide-1793116802.json`, verdict **PASS**, ten checks:

| Check | What it proves |
|---|---|
| `goalCarriedSelection` | the stored `agent_plans.goal` (279 chars) contains `@src/note.py:1-2` **and** the selected line — what the editor showed is what the run received |
| `planApproved` | the plan was accepted through the server gate before anything ran |
| `workerObserved` + `workerOnInternalNetwork` | the run executed in `harness-engine-worker:vdev-local` with `readOnlyRoot: true`, `CapDrop: ALL`, attached only to the run's **internal** network |
| `confinementNamed` | the outcome reports `confinement: container`, so evidence says which boundary it was taken in |
| `diffShown` | both stages reached the editor: `proposed:bide_note.txt`, then `reviewed:bide_note.txt` |
| `wrongDigestRefused` | a review against bytes other than the displayed patch is refused **by the server**: `409 CHANGE_STALE` |
| `reviewApprovedWithDigest` | the recorded change is `approved` at `51aceeca…`, the digest the editor verified before showing anything |
| `appliedBytesMatch` | the file on disk is exactly the reviewed bytes: `reviewed in the editor` |
| `noRefusals` | no file was quietly dropped from the round-trip |

The run chose the filename (`bide_note.txt`, not the requested
`ide_note.txt`) — irrelevant to the claim and left visible: the flow reviews
whatever the confined run produced, bound to its digest, not a name the client
assumed.

**What the gate does NOT get, stated plainly.** `serverEventTypes` is
`plan_proposed, plan_approved, done_signal_received, review_passed` — there is
no `execution_started` and no per-tool `agent_events`, because execution is
off-host (decision 6) and no existing endpoint lets a client mint tool rows it
did not observe. Confinement is therefore evidenced by the container itself,
and the audit by the gate + recorded diff + per-file decision. W6 recorded the
same limit for `harness run --local`; it is not papered over here.

**Two drill bugs, kept out of the evidence set.** The first two FAIL runs were
the drill's fault, not the product's: `execFileSync` returns only stdout, so
`docker logs` (where the login code is printed) looked like "no OTP"; and the
goal lookup read `plan_versions` (queue-written) instead of `agent_plans`
(gate-written), so a correct goal read as absent. Their evidence files were
deleted rather than committed as product failures, and the corrected lookups are
part of the script.

**Still unexercised here:** the `vscode`-bound adapter — tabs, modals,
SecretStorage — is typechecked and built but no editor window was opened in this
environment, which is what the evidence's `drive` field says.

## Week 7 status — editor half closed, H3 half blocked on a prerequisite this repo does not contain

The roadmap line is **"W7 / H3 IDE"**, and its exit evidence has three clauses.
The first is now backed by live evidence; the other two are not, and the reason
is not effort:

| Clause | State | Why |
|---|---|---|
| *Review in editor passes through policy* | **closed** — `w7-ide-1793116802.json`, ten checks | the gate, the confined worker and the digest-bound review are all shown live |
| *Hermes hover/definition/rename meet declared semantics* | **blocked** | H3 needs the Hermes corpus (`docs/roadmap-2026-10-25.md:320`), and **no `.hms` file, `hermesc` or language-server stub exists in this repo** — it is an external codebase the roadmap proposes to adopt |
| *Second-language integration evaluated* | **blocked, same reason** | "add an adapter, not fork the extension" is a design rule that can only be *exercised* once a real language server exists to adapt |

H3's own prerequisite row in `docs/marathon-execution-plan.md:156` is
**H0–H2 Hermes demonstration**, gated on *"external rights/license evidence"* —
an external gate, not repo work. So the honest statement is: W7's editor gate is
closed; the LSP clause of the same roadmap row cannot be started here, and no
number of VS Code work would make it closer.

What remains on the editor side, and is *not* claimed as done: packaging the
extension as an installable `.vsix` and publishing it, a real-window smoke test
(modals, diff tabs, SecretStorage), and `@doc`/`@commit` context kinds. The
`hermes-language-server` would plug into `pickSymbols`'s position — the
extension's symbol source is currently VS Code's own `DocumentSymbolProvider`,
which is exactly the seam an LSP is meant to replace.
