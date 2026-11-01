# Harness — VS Code extension (W7)

A client of the same control path the CLI and the web use: the plan gate, the
confined worker, the durable audit. It is not an executor, and the only thing it
stores is a session token, in VS Code's SecretStorage.

## What it does

1. **Context** — the current selection (or the whole file), `@`-symbols picked
   from the document, and the file's uncommitted hunks are rendered into the
   goal. The block is capped; when it has to shrink, the goal itself says which
   items were dropped or truncated, so what the model saw is readable from the
   audit rather than implied.
2. **Run** — `Harness: Run on selection` proposes a plan, shows its text, and
   only on approval does anything execute. Execution is W6's confined runner
   (`deploy/runners/local_engine.py`, the same script the CLI spawns): read-only
   root, dropped capabilities, internal network, `container` confinement or a
   refusal. The extension host never runs agent code, and the run is not
   started on the server's queue — it would double-execute work already done
   on this machine.
3. **Result** — the run produces a patch. The editor shows it labelled
   *proposed*, with a **review summary** beside it, and asks. The summary lists the changed
   files (with line counts), the tests the agent ran and how each ended, failures, the cost,
   the elapsed time and what is still unknown. It is built from the agent's own recorded tool
   calls and token usage; the harness re-runs nothing. A test result is its exit status only
   (pass and fail counts are not recorded), a model the rate card cannot price shows its cost
   as *unknown* (never `$0`), and a run whose tool calls were not reported shows its tests as
   *unknown* (never *none*). Confirming is the product's `done-signal`, and it is the only
   thing that records a diff server-side.
4. **Review** — each recorded file is shown as a *reviewed* diff computed from
   the server's own patch, and approve/reject is `review_change` bound to the
   `diffSha` that was displayed. A patch that does not hash to that digest is
   never shown. The decision is **keyboard-first**: `Alt+Y` applies (`⌥Y` on macOS), `Alt+N`
   skips, `Alt+Shift+N` skips every remaining file (each is still recorded as rejected). The
   keys work from the diff editor and are live only while a decision is waiting; the same three
   choices are buttons on the notification and entries in the command palette. Between
   differences in a file use the editor's own next/previous-difference keys. The keys are
   ordinary keybindings and can be rebound.
5. **Apply** — approved bytes are written with `WorkspaceEdit`-style file IO
   after a re-read; a file that changed since its diff was shown is refused, not
   overwritten. Binary, symlink, rename-only and patch-omitted changes are
   refused with the reason.

## Layout

```
src/core/      all policy-shaped behaviour; imports no `vscode`, tested with fakes
  context.ts   goal rendering, cap, reduction notice, strict git-hunk parsing
  diff.ts      digest verification, strict unified-diff apply, per-file split
  summary.ts   the review summary: shape check, pricing from the rate card, the text shown
  gate.ts      one waiting review decision; keys and buttons answer it, stale prompts cannot
  run.ts       propose → approve → start → confirm result → review → apply
src/adapter/   the two things core cannot do itself
  sdk.ts       typed TS SDK as the transport
  vscode.ts    modals, diff tabs, virtual documents, file IO, status bar
src/extension.ts  activation and commands
scripts/w7_ide_drill.ts  live gate drill (core + a directory-backed fake editor)
test/          43 tests over the core
```

## Configuration

`harness.serverUrl` / `harness.identityUrl` point at the stack; `harness.repoRoot`
is **required** to run — it is the checkout whose `deploy/runners/local_engine.py`
executes the confined turn — and `harness.launcherPython` is the interpreter that
can import `harness_orchestrator` (e.g. `section4/.venv/bin/python`).
`harness.maxContextChars` bounds the context block; the goal itself reports any
reduction.

## Develop

```bash
npm install          # local dev deps; the SDK is a file: dependency on ../ts
npm run build        # emit dist/
npm run typecheck    # src + test + scripts
npm test             # tsx --test, no editor and no network required
npx tsx scripts/w7_ide_drill.ts --timeout-min 20   # live run, needs the compose stack
```

The live drill needs the stack up **from the repo root with the root env file**:

```bash
docker compose -f deploy/docker-compose.yml --env-file .env up -d
```

A slow local model needs more than the 600 s default engine deadline; the drill
runs against `ENGINE_RUN_TIMEOUT_SECONDS=1800`.

## What is not claimed

The gate's claims about the policy round-trip are proven through `src/core`.

W24 installed the 0.2.0 `.vsix` into a throwaway profile and ran one task in a real VS Code
1.140.0 window, twice, with `scripts/w24_window_run.mjs` (evidence and screenshots in
`docs/briefs/evidence/w24/`). **That run was automated**: keys and clicks were sent over the
DevTools protocol, so no person used the editor. Repeat it by hand once:

1. `npm run package`, then `code --install-extension harness-vscode.vsix` into a clean profile.
2. Set `harness.repoRoot` and `harness.launcherPython`; open a folder with a failing test.
3. `Harness: Sign in`, select the failing code, `Harness: Run on selection`, approve the plan.
4. At the confirm step read the review summary, confirm, then decide with `Alt+Y` / `Alt+N`
   (`⌥Y` / `⌥N` on macOS). The file on disk must change only after `Alt+Y`.

Known limits, none fixed by W24:

* **On macOS Docker Desktop the agent cannot read the workspace from inside the confined run**
  (Landlock denies the shell gate on host bind mounts, `docs/m2-w21-plan.md`), so its own
  `python -m unittest` fails there. The review summary says so (tests *failed*) even when the
  edit is right; the host's test run is the check.
* A test result is its exit status; pass and fail counts are not recorded.
* A local run is not metered in the server's cost ledger, and the server run stays `executing`.
* The plan text shown for approval is a fixed template.
