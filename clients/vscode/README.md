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
   only on approval starts the run. Execution happens in the server's confined
   container; the extension host never runs agent code.
3. **Result** — when the engine finishes, the run sits at
   `awaiting_done_signal` with a *proposed* diff. The editor shows it and asks.
   Confirming is the product's `done-signal`, and it is the only thing that
   records a diff server-side.
4. **Review** — each recorded file is shown as a *reviewed* diff computed from
   the server's own patch, and approve/reject is `review_change` bound to the
   `diffSha` that was displayed. A patch that does not hash to that digest is
   never shown.
5. **Apply** — approved bytes are written with `WorkspaceEdit`-style file IO
   after a re-read; a file that changed since its diff was shown is refused, not
   overwritten. Binary, symlink, rename-only and patch-omitted changes are
   refused with the reason.

## Layout

```
src/core/      all policy-shaped behaviour; imports no `vscode`, tested with fakes
  context.ts   goal rendering, cap, reduction notice, strict git-hunk parsing
  diff.ts      digest verification, strict unified-diff apply, per-file split
  run.ts       propose → approve → start → confirm result → review → apply
src/adapter/   the two things core cannot do itself
  sdk.ts       typed TS SDK as the transport
  vscode.ts    modals, diff tabs, virtual documents, file IO, status bar
src/extension.ts  activation and commands
scripts/w7_ide_drill.ts  live gate drill (core + a directory-backed fake editor)
test/          29 tests over the core
```

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

The `vscode` adapter is typechecked and built, but no VS Code window was opened
in the environment these records were produced in. The gate's claims about the
policy round-trip are proven through `src/core`; claims about how a diff tab is
*rendered* are not tested here, and `docs/week-7-plan.md` says so rather than
leaving it to inference.
