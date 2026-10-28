# Marathon 2.0 W1 — baseline and evaluation (M2-W1)

Follows the 2026-10-28 **Marathon 2.0 roadmap** (Desktop file:
`Harness Marathon 2.0 Roadmap.md`; this plan only executes its W1 row).
This is **Marathon 2.0 W1**, distinct from the historical W1 in
[week-1-plan.md](week-1-plan.md): the historical W1–W11 gates are evidence to
reuse, not work to redo.

Governing rule inherited from [the marathon execution plan](marathon-execution-plan.md):
*an unchecked gate stays open when a dependent week begins*, and a passing
narrow milestone is not a production-readiness verdict.

## Position

The historical stack closed W1–W11 on the original roadmap (latest:
`w11-vision-1793183487.json`, PASS 22/22). Marathon 2.0 adds an evaluation
foundation before more capability: a fixed task set, a reproducible baseline,
and a runner whose verdicts are independent of the agent's own report. W2–W20
compare against what this week measures.

`STATUS.md` still says **NOT READY** for production or a general pilot. Nothing
here changes that verdict.

## Audit before the plan (2026-10-28)

| Area | What exists today | Where | Why it matters here |
|---|---|---|---|
| Evaluation runner | none — 27 bespoke drills, each with its own evidence schema | `scripts/e2e/` | W1 needs a corpus + one runner, not a 28th drill |
| Quality baseline | throughput ruler only (VU, RPS, engine wait) | `docs/benchmarks/BASELINE.md` | leadership numbers, not task success; the new baseline must measure outcomes |
| M-gate ledger | M00, M03, M04, M06, M07 closed; **M01 open**; M02 landed in code (`1.0.0rc3/rc4`) with milestone evidence owed; M05 partly landed (history strip, explicit token ceiling) with evidence owed | `docs/marathon-execution-plan.md:55-108` | W1's exit names exactly these before closure |
| Deployed revision | backend `harness-backend:vdev-local` built 09:17Z, worker `harness-engine-worker:vdev-local` built 09:06Z | `docker image inspect` | **drift found**: `engine.py` and `image_gate.py` match HEAD, but `harness-images.js` is the pre-nudge build (source added the "64 points in one call" text at 02:35, after the image). A current-revision claim on this stack would be false |
| Section 11 | chunking, embeddings, store, search, AST chunking, API | `section11/src/harness_context/` | not assessed yet; W2 audits it before any new index |
| Checkpoint/rewind | live drills for stop, partial write, rewind | `scripts/e2e/w3_*`, `w8_*` | reusable as tasks; stale-edit semantics have no fixture yet |
| Cost of a baseline | live drills are 5–15 min each and spend model tokens | drill docstrings | the first baseline must be explicit about what ran and what was deferred, with reasons |

### Revision drift, recorded rather than papered over

The 09:17Z backend and 09:06Z worker contain the W11 gate, but the plugin text
the model actually saw is older than HEAD: the source nudge that tells a model to
batch up to 64 sample points landed after the build. The W11 evidence remains
valid for the revision it names; it is not evidence for HEAD. W1 slice 1 rebuilds
the images from HEAD and proves the deployed files match by hash before any
baseline number is quoted.

## The gate, expanded into claims that must be shown

1. **A protocol exists.** `docs/eval/PROTOCOL.md` defines the task schema,
   categories, reproducibility fields, measures, promotion rule, evidence
   location and failure retention.
2. **A fixed corpus exists.** `docs/eval/corpus.json` holds 20–30 versioned
   tasks across the categories the roadmap names, with an explicit held-out
   split and a fixture/revision for every task. Tasks whose fixture is not built
   are marked `fixture-owed` and cannot produce a pass.
3. **The runner verifies independently.** `scripts/eval/run_eval.py` executes a
   task, records the independent verifier for it, and writes one normalized
   result per task: verdict, duration, model, image digest, git revision, budget,
   and the failure reason. The agent's own answer is never the verdict.
4. **The baseline is real.** At least the `none`/`infra` tasks run against images
   rebuilt from HEAD; live tasks either run or are deferred with a reason and a
   named owner. The baseline file keeps failures.
5. **Current-revision smoke.** Backend, worker and daemon images are rebuilt from
   HEAD; the runtime files inside them hash-match the working tree; the stack
   restarts healthy; one live run completes.
6. **Nothing new is reachable.** No tool, skill, server or model is added this
   week. M01 (confinement spike) stays open, so no access is widened while it is.

## Decisions

1. **Reuse the drilled gates as corpus tasks.** Each task cites a driver and the
   independent check that driver already performs. The evaluation runner wraps
   them; it does not re-author 20 fixtures in one week. New fixtures are added
   only for categories no drill covers (stale edit, code search, refactor,
   tests), and are marked `fixture-owed` until they exist.
2. **Pin revision and images, not dates.** Every result records the git HEAD, the
   image digests and the model id. A result produced on a stale image is labelled
   stale, not promoted.
3. **Cost classes gate execution.** `none` (no model, local or no engine),
   `infra` (docker/API only) and `live` (model spend). A baseline runs `infra`
   first, then a small `live` sample; it never silently spends to look complete.
4. **n=1 is labelled n=1.** Nondeterministic live tasks are run once for the
   first baseline and marked `repeats: 1`; promotion requires repeated runs.
5. **Failures are retained, including this week's.** A protocol that only records
   wins is a demo, not an evaluation.

## Slices

1. **Rebuild and verify.** Rebuild backend, worker and daemon from HEAD; compare
   `engine.py`, `image_gate.py` and `harness-images.js` hashes inside the images
   with the working tree; restart; record digests; run one live smoke.
2. **Protocol.** `docs/eval/PROTOCOL.md`.
3. **Corpus.** `docs/eval/corpus.json` with 24 tasks, categories, held-out flags,
   cost classes and fixture status.
4. **Runner.** `scripts/eval/run_eval.py`: `--list`, `--task`, `--category`,
   `--cost`, `--out`; executes implemented tasks, normalizes results, records
   environment and limitations.
5. **Baseline.** Run the executable `infra`/`none` subset plus a small live
   sample, write `docs/briefs/evidence/eval/baseline-<stamp>.json`.
6. **Close.** Outcome here, CHANGELOG entry, commit.

## What W1 will not claim

- No production readiness, no general model quality, no external-benchmark parity.
- No baseline over tasks whose fixtures do not exist; those are listed as owed.
- No universal isolation from the existing three-run concurrency evidence.
- No Rust decision; profiling is W14.
- No access widening while M01 is open.

## Known constraints

- Live drills spend tokens and take minutes; the first baseline is partial by
  design and says so per task.
- The M01 confinement spike and M05 evidence are still owed; W1 records them as
  open dependencies rather than assuming closure.
- Image rebuilds are expensive until the layer-order fix in `docs/backlog.md`
  lands; that fix is not part of this week's gate.

## Milestone reconciliation (2026-10-28)

| Milestone | State after this audit | Evidence checked this week |
|---|---|---|
| M00 | closed 2026-10-26 (`vm00-1026`, `df4096e`) | image/source pin recorded; this week's images are newer and hash-verified |
| M01 | **open** | confinement fixture is `fixture-owed` in the corpus; no access widened |
| M02 | code + fixtures landed (`contracts 1.0.0rc4`, `tool_call`/`tool_result`/`context_compacted` goldens) | `section1/tests/test_conformance.py` — 7 passed (including the golden event fixtures) |
| M03 | closed 2026-10-26 (scope mounts + Landlock) | unchanged this week |
| M04 | closed 2026-10-26 (streamed native read/write) | unchanged this week |
| M05 | history strip and explicit token ceiling landed | `test_seeded_history_keeps_commits_and_drops_executable_or_escaping_git_state`, `test_a_gitdir_pointer_file_is_never_followed` — 2 passed |
| M06 | landed 2026-10-26; live image path proven via M07 | unchanged this week |
| M07 | closed 2026-10-26 (daemon topology + API run + vision) | unchanged this week |

M02 and M05 are **landed with unit/conformance evidence**, not re-proven live
this week; the W1 baseline records that distinction instead of upgrading them.


## Outcome (2026-10-28) — gate PASSED for a partial, current-revision baseline

Evidence: `docs/briefs/evidence/eval/baseline-20261028T111147Z.json` — **3/3
executed tasks PASS, `revisionVerified: true`**, 22 tasks explicitly deferred
(6 blocked, 6 fixture-owed, 10 implemented but not selected). This is a partial
baseline by design: it is the first W1 measurement, not the full 25-task set.

### Current revision, proven by hash

HEAD `57aaba2`. The full Dockerfile rebuild was started twice and interrupted
mid-Chromium both times, so the deployed images are **thin overlays built from
the previous bases plus the one runtime file that had drifted**
(`harness-images.js`; the source nudge that tells a model to batch 64 sample
points landed after the 09:17Z build). The overlay images are:

- backend `sha256:8fd637dc…`
- engine worker `sha256:9c867f15…`
- daemon `35109361e476` (already carried the current plugin)

`run_eval.py --verify-only` compares the deployed bytes with the worktree for
`engine.py`, `image_gate.py` and `harness-images.js` in both the orchestrator
container and the worker image: **all six match**. The claim is therefore
"deployed runtime bytes equal HEAD for the files that changed since the base
image", not "built by the Dockerfile from a clean tree". A clean rebuild remains
owed and is tracked by the layer-order backlog entry.

The worktree is `dirty: true` in the evidence because this week's docs, corpus
and runner are uncommitted while the baseline runs, and `AGENTS.md` carries a
tool-generated context note. No runtime file is uncommitted.

### What ran

| Task | Verdict | Time | Independent check |
|---|---|---|---|
| `stack-compose-journey` | PASS | 1.2 s | 13 checks: 20 containers healthy, readiness, OTP login, idempotent demo seed, replay ordering, audit page, sandbox lifecycle, cross-tenant 404s, metrics, frontend |
| `w4-egress-deny` | PASS | 1.4 s | allow-listed 200; unlisted host/port/method 403; sandbox peer CONNECT denied |
| `m07-vision-api` | PASS | 26.5 s | uploaded screenshot reaches the vision model and is named back; a second fill gives a different answer |

The vision task is also this week's current-revision **live smoke**: a real run
completed through the stack after the overlay restart.

### What did not run, and why

- **Blocked (6):** `m03-scope`, `m04-streaming`, `m07-api-run`,
  `m07-daemon-topology`, `w3-rewind`, `w3-partial-write`. Each defaults to a
  local Ollama chat endpoint, and this host has only `nomic-embed-text`; a chat
  model must be pulled or a compatible endpoint pinned before they run.
- **Fixture-owed (6):** `stale-edit`, `code-search`, `multi-file-refactor`,
  `tests-written`, `tenant-isolation-api` (broader matrix; shares and the
  cross-tenant 404 leg already run), `native-tool-confinement` (M01).
- **Implemented, not selected (10):** the remaining live gates (`w4-web-fetch`,
  `w4-install`, `w4-mcp-client`, `w4-credential-leak`, `w8-concurrency`,
  `w8-crash-cleanup`, `w9-share-deploy`, `w10-browser-repro`, `w11-vision`,
  `cli-parity`). They are declared with budgets and verifiers and are the next
  baseline increment.

### Findings, kept

1. **Revision drift was real, not hypothetical.** The deployed plugin lacked the
   "pass up to 64 points in one call" nudge; the W11 passing run used 25 sample
   calls against a tool that could batch. The baseline now refuses to call a
   stale image current-revision.
2. **The local-model tasks are blocked by an environment gap, not code.** The
   six blocked tasks are cheap and model-light; pulling one local chat model
   turns them on.
3. **The runner had two defects at first contact, both fixed in this week:**
   a `None` dereference in failure parsing that masked a driver error, and a
   deferral classifier that labelled fixture-owed tasks as "not selected". A
   preflight caught both before the baseline file was written.
4. **M02 and M05 have unit/conformance evidence, not live re-proof this week:**
   `section1/tests/test_conformance.py` 7 passed; the two seeded-history tests
   2 passed. M01 remains open.

### Limitations

- n=1 for every executed task; single passes are existence proofs, not rates.
- The baseline is 3 of 25 tasks. The remaining 10 implemented tasks are the
  next increment; they are not silently counted as passing.
- The overlay images are current by content hash, not a clean Dockerfile build.
- No external benchmark, no model-quality claim, no production-readiness claim.
