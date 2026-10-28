# Marathon 2.0 W7 — LSP integration, editor verification, Hermes H3 (M2-W7)

Follows the 2026-10-28 Marathon 2.0 roadmap, the W7 row, and
[the W6 outcome](m2-w6-plan.md). This is **Marathon 2.0 W7**, distinct from the
historical W7 (VS Code extension and the editor gate), whose plan is in
[week-7-plan.md](week-7-plan.md).

Governing rule: *an unchecked gate stays open when a dependent week begins.* W6
left the skills prototype advisory-only and Hermes blocked; W7 adds the first
real language-server proof and re-checks the editor path at this revision.

## Purpose

W7 asks for:

1. **read-only LSP diagnostics, symbols, definitions and references** with
   timeout, output and process limits;
2. **real language servers assessed separately from Hermes**, so a passing
   editor integration is not mistaken for an `.hms` toolchain claim;
3. verification that the **editor package** and its digest-bound review still
   hold at this revision.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Worker image | Python 3.12, pip 25.0.1, Debian 12; **no** `pylsp`, `pyright`, `clangd`, `gopls`, `ruff`, `typescript-language-server`, `jedi`, `pyflakes` | `harness-engine-worker:vdev-local` |
| LSP client | W4 spike only: initialize + raw request, no notifications, no document lifecycle | `section5/src/harness_sandbox/lsp_client.py` |
| Editor package | `clients/vscode` with `core/` (diff, run, context) and `adapter/` (sdk, local); `typecheck`, `build`, `test` scripts | `clients/vscode/package.json` |
| Editor gate | historical PASS, 10 checks, 2026-10-25 | `pilot-prep/w7-ide-1793116802.json` |
| Local launcher default image | `harness-engine-worker:p0` — predates the W11 vision patch | `deploy/runners/local_engine.py` |
| Hermes H3 | no `.hms` corpus, no `hermesc`, no grammar; H0 paperwork still absent | W6 audit |

Two assumptions were treated as unproven until measured: that a real server
would answer inside the existing limits, and that the editor path still applied
reviewed bytes under the reviewed filename.

## What changed

### The client (`section5/src/harness_sandbox/lsp_client.py`)

The W4 client proved limits; W7 gives it the operations the harness needs:

- **Server-initiated requests are answered** (`workspace/configuration`,
  `window/workDoneProgress/create`, `client/registerCapability`) with a null
  result. Real servers block on these; the fixture now sends one during
  `initialize` and reports whether it was answered.
- **Notifications are queued, bounded and matchable.** `wait_notification()`
  waits on the wire, `diagnostics(uri)` matches
  `textDocument/publishDiagnostics` by URI so a burst for another file cannot
  satisfy the wrong wait; past `MAX_PENDING_NOTIFICATIONS` (256) the server is
  killed as misbehaving.
- **Document lifecycle:** `open_document(path|uri, text, language_id)` sends
  `didOpen` (default text = the file's bytes, capped at 2 MB, language id by
  suffix), then `document_symbols`, `definition`, `references`.
- **CLI scenario mode:** `--scenario diagnostics|documentSymbol|definition|references
  --file <path> [--line --character]`; the legacy `--method/--params` form still
  works. Exactly one mode is required.
- URI helpers (`file_uri`, `path_from_uri`, `language_id_for`) and a `pid`
  accessor so an auditor can read the server's own `/proc/<pid>/environ`.

### The proving image (`section4/worker-lsp.Dockerfile`)

`FROM harness-engine-worker:vdev-local`, then **pinned**
`python-lsp-server[pyflakes,rope]==1.12.2`, `pyflakes==3.2.0`, `rope==1.13.0`,
and the client baked at `/opt/harness-lsp/lsp_client.py`. The default worker
stays language-neutral: a language server is a supply-chain and size decision,
not a default.

### The gate (`scripts/e2e/w7_lsp_real_drill.py` + `w7_lsp_probe.py`)

A ``--network none`` container, uid 10001, workspace mounted **read-only**,
`DEEPSEEK_API_KEY`/`HARNESS_WORKER_TOKEN` injected into the *container* env so
the scrub is observable. The probe runs the four operations and the W4 limits;
the drill turns the observation into 16 checks.

## Outcome (2026-10-28)

**Real language server: 16/16 checks PASS.**
`docs/briefs/evidence/eval/w7-lsp-real-1793197850.json`, runner record
`baseline-20261028T143120Z.json` (`revisionVerified: true`, 2/2 executed — this
task and `ast-lsp-confinement`, 4.11 s / 4.8 s):

| Check | Observation |
|---|---|
| image client = worktree | sha256 of `/opt/harness-lsp/lsp_client.py` equals the host file |
| pinned server | `python-lsp-server 1.12.2`, `pyflakes 3.2.0`; capabilities include definition/references/documentSymbol |
| diagnostics | `'os' imported but unused` (sev 2, line 0) and `undefined name 'undefined_name'` (sev 1, line 10) |
| clean file | publishes `[]` |
| symbols | `RETRY_BACKOFF_SECONDS`, `compute_backoff`, `broken` |
| definition | `file:///work/pkg/retry.py` line 5 |
| references | import (jobs.py:0), call site (jobs.py:2), declaration (retry.py:5) |
| server environment | `HOME`, `LANG`, `PATH`, `TMPDIR` only; HOME/TMPDIR under `/tmp/lsp-home-*`; no credential-shaped key |
| workspace write | `OSError: [Errno 30] Read-only file system`; no file appears |
| network | `OSError: [Errno 101] Network is unreachable` |
| deadline | server SIGSTOPped, 1.0 s deadline → timeout, killed in 1.008 s |
| tree / route | byte-identical before and after; no route in the container |

Timings on this host: `initialize` 0.9 s, diagnostics 0.5 s, symbols 0.5 s,
definition 0.04 s, references 0.004 s. Image digest
`sha256:0daea5ab7d9671d97cd4b69c7ff2cf801169d444ccbf63adc8b6362152ff9382`.

**Editor: 11/11 checks PASS at this revision.**
`clients/vscode` typecheck and build are clean and `npm test` is 29/29.
The live gate `pilot-prep/w7-ide-1793198120.json` ran the extension core against
the stack (`harness-engine-worker:vdev-local`, container confinement, worker
read-only root on an internal-only network): plan approved in-editor, a diff
shown, a wrong digest refused with `409 CHANGE_STALE`, approval recorded with
the reviewed digest, and the applied file is **`ide_note.txt`** with exactly the
reviewed post-image bytes.

## Defects found and fixed

1. **Local edits applied under a corrupted filename.** `normalize_diff()`
   stripped the absolute root but left git's `b`/`a` prefix glued to the name,
   so `+++ b//abs/run/ide_note.txt` became `+++ bide_note.txt` and the editor
   wrote `bide_note.txt`. The historical PASS masked it: the check compared the
   file's *text* against a phrase, not the path against the reviewed one.
   Fixed by stripping `a`/`b` **with** the root and keeping the conventional
   `a/`/`b/` prefix; regression test added; the live drill now asserts the
   applied path equals the reviewed path.
2. **The launcher default image was stale.** `preflight()` defaulted to
   `harness-engine-worker:p0`, which predates the W11 vision patch. With the
   shipped `.env` (`DSH_PATCH_FILES=/opt/harness/patches/openrouter-vision.patch.yml`)
   every host-side editor run failed inside the worker — "DSH_PATCH_FILES entry
   not found" — before any model call. The default now tracks
   `HARNESS_IMAGE_TAG` (i.e. `harness-engine-worker:vdev-local`, the same tag
   compose uses); `HARNESS_ENGINE_IMAGE` still overrides. Tests pin both.
3. **The gate was over-specified.** `appliedBytesMatch` compared the file to
   the literal phrase `reviewed in the editor`; a correct run that included the
   sentence's trailing period failed. It now compares to the reviewed
   post-image and the reviewed path.
4. **A 1 ms deadline proved nothing.** A cached pylsp answer can arrive inside
   1 ms, so the first drill "passed" the request instead of the deadline. The
   probe now SIGSTOPs the server and uses a 1 s deadline; the observation is
   `timedOut: true` at 1.008 s with the process killed.

## Gate position

| W7 exit claim | State |
|---|---|
| LSP diagnostics, symbols, definitions, references with timeout/output/process limits | **pass** — 16-check drill at this revision |
| One real language-server fixture works within the sandbox | **pass** — pinned pylsp on a `--network none`, read-only, unprivileged container |
| Editor package verified | **pass** — typecheck, build, 29 tests |
| Editor round-trip through the policy gate and digest-bound review | **pass** — live 11/11 at this revision |
| Packaged `.vsix` and a real-window smoke | **not run** — no packaging tooling installed, no display |
| Hermes H3 (language server for `.hms`) | **blocked** — H0 paperwork still absent, no `.hms` corpus and no `hermesc`; the W7 LSP proof is Python, not Hermes |

## Limitations

- **LSP is not yet an agent tool.** The client and the drill live in the
  sandbox package; the engine's tool roster is unchanged and no prompt receives
  LSP results. Wiring it in needs its own boundary review (what the model may
  ask, how output is capped in the transcript), and is not claimed here.
- **One language, one proving image.** Python only. The overlay adds pylsp,
  pyflakes and rope to a worker image; adopting it as a default is a
  supply-chain and image-size decision that has not been made.
- **The deadline proof stops the process first.** SIGSTOP makes "cannot answer"
  deterministic; the client's deadline/kill path itself is unchanged from W4,
  where a silent server was killed without help.
- **`revisionVerified` does not cover the pylsp image.** The runner hashes the
  orchestrator/worker runtime files; the overlay is verified by its client-file
  hash check and its recorded image digest instead.
- **The editor adapter is typechecked, not opened.** No `.vsix`, no marketplace
  metadata, no real VS Code window; the drill drives the same core the adapter
  calls.
- **The editor gate is a live model run** and is not a corpus task: the runner
  executes Python drivers only.
- **The editor drill now costs two model runs' worth of history** — the failing
  run is kept as evidence of defect 1/3, not of a product regression.

## Carry-over

- **W8:** concurrency work.
- **LSP adoption:** if the harness is to expose LSP to the model, specify the
  tool surface, output caps and transcript treatment first.
- **H0:** university policy statement and teammate acknowledgment before H2/H3.
- **H3:** remains blocked; no `.hms` support is claimed.
- **M01:** native-tool confinement still open; nothing here widened access.
- **W11:** current-revision re-run still owed for the live vision task.
