# DRAFT — STATUS.md and PROJECT-STATE.md text for batch 1 (NOT APPLIED)

Prepared 2026-11-05 for the reviewer to check and paste. Written from
`docs/briefs/hy4-handback-A1.md` … `hy4-handback-B4.md`,
`docs/briefs/hy4-handback-SUMMARY.md`, `STATUS.md`,
`docs/briefs/PROJECT-STATE.md`, `docs/roadmap-3.0.md` and the reviewer re-runs
listed below. **Nothing in this file has been applied**: `STATUS.md`,
`PROJECT-STATE.md`, the capability matrix and every spend figure are untouched.
Every number and command below appears in a batch 1 hand-back, in the repository,
or in the batch 2c prompt's reviewer re-run list.

## Two states, kept apart in every entry

* **uncommitted in the working tree** — all eight batch 1 tasks live only as
  edited or new files on branch `w28-s1-delegate` at HEAD `f5b298a`. No commit,
  push, image build or service recreate was made for any of them.
* **deployed** — **nothing from batches 1 or 2 is deployed.** The image tags
  recorded in `STATUS.md` (`harness-backend:vw28s6`, `orchestrator` on
  `vw27s3`, `worker-daemon` on `vw27s2`, `frontend` on `vw24b2`) are earlier
  deploys by other work; the batch 1 and batch 2 diffs have not reached them.
  A "PASS" below is a test, drill or gate result in the working tree, never a
  statement about the running stack.

## Reviewer re-runs (mark: "reviewer re-ran"; these are not my runs)

Batch 1 checks, as reported in the batch 2c prompt:

* reviewer re-ran `make frontend` rc=0 (99 files, 1075 tests).
* reviewer re-ran `npm run build` rc=0.
* reviewer re-ran `test_reclaimed_run_fences_the_original_worker` 30/30 under
  CPU load while the old version failed 1 of 30.
* reviewer re-ran A4's negative control: 23 failed / 28 passed without the
  change and 51 passed with it.
* reviewer re-ran A3's negative control: `ScopeRequestEditor.test.tsx` failed
  without the `scopeRequest` assignment.
* reviewer re-ran the A1 drill: PASS, and FAIL with a writable workspace mount.

Batch 2 checks, as reported in the batch 2c prompt (recorded here because the
batch 2b prompt asks the draft to carry them):

* reviewer re-ran A1's seven mutations; each failed exactly one test.
* reviewer re-ran `make frontend` rc=0 (1083 tests), `npm run build` rc=0 and
  `make static-gates` rc=0 on the A1 diff.
* reviewer re-ran `make frontend` rc=0 and `make lint` rc=0 later with A2 in
  the tree.

## 1. Draft STATUS entries — one per batch 1 task (A1–A4, B1–B4)

### 2026-11-05 — batch 1 A1: M01 native-edit drill without a model

Built `scripts/e2e/m01_native_edit_drill.py` (executable) and the evidence
`docs/briefs/evidence/pilot-prep/m01-native-edit.json` (`ok: true`,
`verdict: "PASS"`). The drill calls the *shipped* `LocalFileSystem.editText`
path (`@deepseek-ai/dsh-tool-fs` → `@deepseek-ai/dsh-fs-local`) in one
throwaway container with `--network none`, no LLM and no provider: in-scope
`src/x.txt` becomes `"after"`, out-of-scope `docs/y.txt` is refused with
`EROFS`, 2 files outside `src` are hashed unchanged, and the container removes
itself. It took 0.68 s, extracted 10,232 JS/JSON files from the runtime SEA, and
spent $0. Checked: the drill `DRILL_RC=0` and `ok= True verdict= PASS`;
`make lint` rc=0; root ruff on the new script `All checks passed!`. The
negative control (only the workspace mount changed from `readonly=true` to
`readonly=false`) returned `CONTROL_RC=1` / `verdict: "FAIL"` — the OS refusal
is falsifiable. reviewer re-ran the A1 drill: PASS, and FAIL with a writable
workspace mount. **Not verified:** it reproduces the daemon's mounts by reading
`confine.py` and does not exercise the daemon's own `worker_daemon` →
`wrap_command` spawn path; no live-stack run, no model call, no `make test` or
full suites; only 2 files are hashed (no directory metadata or symlinks);
`--network none` is stricter than the daemon's private run network. The probe
overrides `process.reallyExit` inside the throwaway container to keep the Node
SEA alive, extracts into a 256m tmpfs and uses only the JS/JSON subset of the
SEA archive — all three are reviewer judgement calls. **Rollback:** none; no
image was built and no service was recreated, and the throwaway container
removed itself. **State:** uncommitted in the working tree. **Deployed:**
nothing.

### 2026-11-05 — batch 1 A2: the flaky `test_reclaimed_run_fences_the_original_worker`

Fixed the flake *in the test*, not in production: 15 lines in
`section4/tests/unit/test_lifecycle_recovery.py` now monkeypatch
`service_mod.heartbeat_run` so A's ticks park on an `asyncio.Event` (B's ticks
still call the real function) and call `resume_heartbeat.set()` after B holds
the claim. No assertion was changed, loosened or removed; no production code
was touched. The reproduced failure was `assert await replica_b.reap_zombies()
== [RUN]` at line 180 (`[] == ['life-run']`), **not** the 5 s wait that
`STATUS.md:927`/`:963` guesses: under CPU load A's 50 ms heartbeat refreshes
`heartbeat_at` between `_age_claim` and `reap_zombies`. Checked: 40/40
isolated runs before the change; a loaded loop under 12 `yes` burners failed
first at run 6 (`LOADED_RUNS=6 PASS=5 FAIL=1`); after the change
`POST_FIX_LOADED_RUNS=60 PASS=60 FAIL=0`; ruff rc=0 and
`RUFF_NO_CACHE=true make lint` rc=0. reviewer re-ran
`test_reclaimed_run_fences_the_original_worker` 30/30 under CPU load while the
old version failed 1 of 30. **Not verified:** no full section4 suite or
`make test` (that would rewrite the tracked W21 evidence file); no per-call
trace of `heartbeat_run`, so the mechanism is inferred from `service.py`,
`run_queue.py` and the reproduced failure; only one load-only failure mode was
seen; plain `make lint` did not complete here after the edit. **Rollback:**
revert the one test file. **State:** uncommitted in the working tree.
**Deployed:** nothing.

### 2026-11-05 — batch 1 A3: W27 scope request and the two costs (PARTIAL in my run)

Built a confirm-gated scope-request flow: new `section7/src/lib/scopeRequest.ts`,
`section7/src/components/ScopeRequestEditor.tsx`,
`section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx` and
`ScopeRequestEditor.test.tsx`; `deriveView.ts` parses `scopeRequest` from
`tool_result`; `ToolActivity.tsx` shows `needs scope: <path>` with an
`Edit scope` button; `runsApi.ts` and `runDetailAggregate.tsx` show
`estimatedCostUsd` beside `costUsd` with explicit `not metered yet` /
`no plan estimate` wording for null. The container GETs intent on open and
PUTs only on confirm. No backend code or contract regeneration was needed.
The Overview tab lives under `section7/app/`, not `section7/src/`, and an
intent editor already existed at `intentSection.tsx` — both contradict
`STATUS.md:974`/`:978`; recorded, not forced. Checked: `tsc --noEmit` rc=0
(after fixing one missing `scopeRequest: null` fixture field);
`cd section7 && npx vitest run --minWorkers=1 --maxWorkers=2` rc=0 (99 files /
1075 tests); `make static-gates` rc=0 (26 route modules, 282 src files, TS
mirror up to date, spec drift clean, `run_eval.py --check` 39/39, 60 env knob
reads). The real negative control (removing
`call.scopeRequest = parseScopeRequest(payload);`) produced `1 failed | 5
passed (6)`; after restore, 5 affected files / 74 tests passed. reviewer
re-ran A3's negative control: `ScopeRequestEditor.test.tsx` failed without the
`scopeRequest` assignment; reviewer re-ran `make frontend` rc=0 (99 files,
1075 tests) and `npm run build` rc=0. **Not verified in my run:** `make
frontend` at default worker parallelism was rc=1 on two pre-existing 5 s
timeouts in untouched files (`meetingRestore.test.tsx`,
`a11yAccessibleNames.test.tsx`), both passing in isolation; `npm run build`
was blocked by the WorkBuddy file-broker `EPIPE` plus a user-rejected `.next`
delete permission, so it was `NEEDS-REVIEWER-RUN` here; no browser/visual
check. **Rollback:** revert the section7 files; no image was built and no
service was recreated. **State:** uncommitted in the working tree.
**Deployed:** nothing.

### 2026-11-05 — batch 1 A4: configurable denial thresholds and their edges

Made the two W27 denial stops configurable without changing defaults:
`section4/src/harness_orchestrator/loop_guard.py` gained `_denial_limit`,
`denial_run_limit()`, `denial_window_limit()`, two `LoopGuard` constructor
defaults and an `_observe_result` that consults the instance fields;
`service.py`'s single `LoopGuard` call site passes both; `.env.example`
documents the 3 / 10 defaults and the unset/garbage→default,
empty/off/none/0/negative→disabled semantics; `test_loop_guard.py` and
`test_loop_guard_wiring.py` were appended to (all pre-existing tests
byte-for-byte unchanged). `DENIAL_WINDOW` (50) is unchanged and not tunable;
the JS plugin is untouched. Checked: `51 passed` with a unique `--basetemp`;
`scripts/check_env_knobs.py` `env knob catalogue complete: 62 runtime reads,
all in .env.example` (baseline 60); `make static-gates` rc=0;
`RUFF_NO_CACHE=true make lint` rc=0 after fixing one I001 in a new test; root
ruff on the 4 changed Python files rc=0. The real negative control (restoring
the original constant comparison in `_observe_result`) gave `5 failed, 46
passed`; restore gave `51 passed`. reviewer re-ran A4's negative control:
23 failed / 28 passed without the change and 51 passed with it. **Not
verified:** the exact check command without `--basetemp` is rc=1 here (9
shim-induced `tmp_path` fixture errors, no assertion failures); no deploy, so
the knobs were exercised only against the service test harness; no full
section4 suite, so the W21 evidence file is unchanged. **Rollback:** revert
the five files. **State:** uncommitted in the working tree. **Deployed:**
nothing. **Reviewer decision:** `HARNESS_DENIAL_RUN_LIMIT=0` *disables* the
3-in-a-row stop rather than meaning "stop immediately" — a real operator
footgun, and negative values are treated as "disabled" by analogy with
`loop_repeat_limit()`.

### 2026-11-05 — batch 1 B1: `permissions` design note (decision input only)

Wrote `docs/decisions/2026-11-05-intent-permissions.md`: PROPOSED, blank
`Decision owner:` line, three options (tool allow-list / network-off / retire
the column), what each changes, what each costs to test, and one recommendation
(retire). Every current-behaviour claim cites a file and line read at HEAD
`f5b298a` (`control.py`, `collab.py`, `service.py`, `engine.py`,
`shell_gate.py`, `intentSection.tsx`, `agent_tree.py`, `delegation.py`); the
two unrelated "permissions" concepts are called out. No code, no matrix row,
no `docs/reviews/w33-*` file, no readiness row. Checked:
`section4/.venv/bin/python -m pytest section4/tests/unit/test_program_docs.py -q`
`22 passed` both with and without `--basetemp`. No new tests, so no negative
control applies; the note's falsifiability is its citation list. **Not
verified:** no owner has ruled; Option A/B test costs are engineering estimates,
not measurements; no implementation, migration or API change was tested.
**Rollback:** delete the note. **State:** uncommitted in the working tree.
**Deployed:** nothing. **Reviewer decision:** retiring the column implies
stopping the approval-void effect at `control.py:784` — an approval-policy
change that must be ruled by a person, not merged from a PROPOSED note.

### 2026-11-05 — batch 1 B2: a read-only stack tag report

Wrote `scripts/ops/stack_tags.py` (executable; its only external command is
`docker ps --format`) and `scripts/ops/test_stack_tags.py` (7 tests fed canned
`docker ps` text, no Docker daemon contacted). `parse_ps` / `image_tag` /
`stack_report` are pure; `main()` prints and always returns 0. The 11
`EXPECTED_BACKEND_SERVICES` names come from `deploy/docker-compose.yml:158`
onwards, excluding one-shot `migrate`/`backend` and the loadtest profiles.
Checked: `7 passed`; root ruff initially found `EXE001` and `I001` in the two
new files (fixed by `chmod +x` and import formatting, then `All checks
passed!`); `RUFF_NO_CACHE=true make lint` rc=0. One real read-only run
(`REAL_RUN_RC=0`) printed `Backend services: 11 of 11 expected running`,
`Agreement: MIXED` (`vw28s6` × 10, `vw27s3` × 1 `orchestrator`) and
`Worker daemon: harness-worker-daemon:vw27s2`. The real negative control
(making `image_tag` always return `vw28s6`) gave `3 failed, 4 passed`;
restoring the two lines gave `7 passed`, `RUFF_RC=0` and a second real
`MIXED` run at rc=0. **Not verified:** the "all agree" and "missing" branches
were exercised only with canned output; `docker inspect` was not used; no
redeploy. The mixed tags reported above are the *existing deployed* stack and
are not a batch 1 deployment. **Rollback:** delete the two new files.
**State:** uncommitted in the working tree. **Deployed:** nothing.
**Reviewer decision:** the script exits 0 even when `docker ps` fails, so a
monitoring wrapper cannot detect a broken daemon from the exit code; the
hand-written tuple can drift from compose (batch 2's B2 later added a
compose-alignment test for exactly this).

### 2026-11-05 — batch 1 B3: drill hygiene and two blocked corpus entries

Fixed two `EXE001` shebang/mode findings (`m07_daemon_drill.py` and
`w27_denial_drill.py` → `100755`) and one `SIM201` line at
`w27_denial_drill.py:39` (`not p.relative_to(root).parts[0] == "src"` →
`p.relative_to(root).parts[0] != "src"`). Added `w27-denial` and
`w27-breaker` to `docs/eval/corpus.json` as `blocked`, each with
`notes: "not yet run through the runner"`, no `implemented` key and empty
driver `args`, so no hosted model invocation is encoded. Checked: root ruff
went from `Found 3 errors` to `All checks passed!`; `python3
scripts/eval/run_eval.py --check` gave
`{"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}` (baseline 39/39 —
task count +2, implemented unchanged, so neither entry is claimed as passing);
`make static-gates` rc=0. The real negative control (revert the SIM201 line
and clear both executable bits) reproduced all three findings; restore was
ruff rc=0 and the W21 evidence file diff was empty. **Not verified:** neither
W27 drill was executed, so no PASS/FAIL is claimed; `budget.maxCostUsd: 0.01`
and the tool-call/timeout numbers are unmeasured metadata copied from drill
docstrings, inert while blocked; `w27-breaker`'s `model` is the literal
`"stack-configured live model"`. **Rollback:** restore the two modes and the
one line, and remove the two corpus entries. **State:** uncommitted in the
working tree. **Deployed:** nothing. **Reviewer decision:** the reviewer keeps
those two live/paid drill runs and only flips `status` after running them
through the runner with the right `--image`/`--model`/`--base-url`.

### 2026-11-05 — batch 1 B4: runbook for redeploying the worker daemon and orchestrator

Wrote `docs/runbooks/redeploy-worker-and-orchestrator.md` (10,452 bytes), the
only file B4 changed. It documents the orchestrator's
`--env-file deploy/.env --env-file .env` flags and the daemon's
`--env-file .env` (from `STATUS.md:979` and `:972`, including the
`HARNESS_WORKER_DAEMON_TOKEN is required` crash loop), the 51-environment-key
/ 0-difference equality check, tag rules, both rollbacks, the "no worker
running" check and the verification set. Every §5 command carries an explicit
**TESTED IN THIS REPO: YES / NO** label from STATUS alone. It resolves the
`STATUS.md:972` vs `:977` contradiction about drill `--image` defaults in
favour of the code (`daemon_engine_image(...)`). Checked: the only command run
for B4 was `section4/.venv/bin/python -m pytest
section4/tests/unit/test_program_docs.py -q` → `22 passed`; the W21 evidence
diff was empty; no hand-back introduces a markdown link the docs gate checks.
**Not verified:** no command in the runbook was executed — no `docker
compose`, build, `docker run`, rollback, `--verify-only`, health probe or
drill; the reconstructed §5 argv is not claimed to be the exact command STATUS
used; §2's env-equality shell command is deliberately absent because STATUS
records no such command; rollbacks are untested. **Rollback:** delete the
runbook. **State:** uncommitted in the working tree. **Deployed:** nothing.

## 2. Proposed PROJECT-STATE.md changes — diff-style, NOT applied

The blocks below are a proposal for the reviewer to paste after checking. They
do not edit `docs/briefs/PROJECT-STATE.md`. Both preserve the short-brief
limit (`test_program_docs.py` allows 130 lines; the file is 112 lines today).

### §2 "Where the work is now: Roadmap 3.0 (2026-11-05)" — one added sentence

```diff
@@ PROJECT-STATE.md §2, final sentence of the W27/W28 paragraph @@
- candidate measurement not done. Verdict unchanged: NOT READY. Newer detail is in `STATUS.md`.
+ candidate measurement not done. Verdict unchanged: NOT READY. Newer detail is in `STATUS.md`. Batch 1 of the 2026-11-05 zero-cost engineering pass (`hy4-handback-SUMMARY.md`) is uncommitted in the working tree, not deployed: the W27 scope-request editor and actual-versus-estimated cost display, configurable denial thresholds, a model-free M01 native-edit drill, a read-only stack tag report, two blocked W27 corpus entries and a redeploy runbook; STATUS text is drafted in `hy4-draft-status-batch1.md`, not pasted.
```

### §4 "Open work, in order" — proposed item list

```diff
@@ PROJECT-STATE.md §4 (replace items 4-9; items 1-3 stay verbatim) @@
- 4. Row 56: no screen reader; only two pages walked by the browser check; English only (contrast and target size closed 2026-10-21).
+ 4. Row 56: no screen reader; only two pages walked by the browser check; English only (contrast and target size closed 2026-10-21). Batch 1 A3 adds a confirm-gated scope-request editor and the two cost lines in `section7`, uncommitted and not deployed, with no browser/visual check; this does not close the row.
- 5. Part 57: in-flight engine spend is an estimate. Part 51: residency is deployment-wide.
- 6. Parts 31/58: backups expired outside the scripts (bucket lifecycle rules) are not covered by holds.
- 7. Multi-replica (closed 2026-10-21 on one host, `evidence/s12/part54.md`): artifacts, exports and diffs share one Docker volume across orchestrator replicas, and 1000 sockets over two identity replicas delivered every envelope. Several hosts need NFS or an object store; the store volume is not in the backup scripts. Part 28: alerts go to one email address plus one optional webhook (`HARNESS_ALERT_WEBHOOK_URL`); no pager integration. Part 53: point-in-time restore is drilled from the live file archive and through self-hosted S3 (SeaweedFS; `evidence/s12/part28-55.md` sections 5-6), not yet against a cloud bucket.
- 8. Smaller limits: a session state change reaches running runs within 30 s, by sweep (40); only intent `scopePaths` are engine-enforced, at argument level (42); a timed-out engine call's tool calls are not recorded (44); GitHub OAuth token keeps write scope once a project opts in (49); the audit anchor file is only as safe as its storage (11).
- 9. Externally gated: 03 (real pilot cohort and baseline), 37 (live identity provider), 52 and 58 (pen test, counsel), 53 (real restore), 61.
+ 5. Part 57: in-flight engine spend is an estimate. Part 51: residency is deployment-wide.
+ 6. Parts 31/58: backups expired outside the scripts (bucket lifecycle rules) are not covered by holds.
+ 7. Multi-replica (closed 2026-10-21 on one host, `evidence/s12/part54.md`): artifacts, exports and diffs share one Docker volume across orchestrator replicas, and 1000 sockets over two identity replicas delivered every envelope. Several hosts need NFS or an object store; the store volume is not in the backup scripts. Part 28: alerts go to one email address plus one optional webhook (`HARNESS_ALERT_WEBHOOK_URL`); no pager integration. Part 53: point-in-time restore is drilled from the live file archive and through self-hosted S3 (SeaweedFS; `evidence/s12/part28-55.md` sections 5-6), not yet against a cloud bucket.
+ 8. Smaller limits: a session state change reaches running runs within 30 s, by sweep (40); only intent `scopePaths` are engine-enforced, at argument level (42); a timed-out engine call's tool calls are not recorded (44); GitHub OAuth token keeps write scope once a project opts in (49); the audit anchor file is only as safe as its storage (11).
+ 9. Externally gated: 03 (real pilot cohort and baseline), 37 (live identity provider), 52 and 58 (pen test, counsel), 53 (real restore), 61.
+ 10. W27 slice 3 UI and knobs (batch 1 A3/A4): uncommitted, not deployed. A3's `make frontend` at default parallelism returned rc=1 on two pre-existing 5 s timeouts and `npm run build` was blocked by a broker `EPIPE` plus a rejected `.next` delete here; reviewer re-ran `make frontend` rc=0 (99 files, 1075 tests) and `npm run build` rc=0. A4's knobs were exercised only in the service test harness; `0` disables a stop rather than meaning "stop immediately".
+ 11. `permissions` (batch 1 B1): `2026-11-05-intent-permissions.md` is PROPOSED with a blank owner line. Nothing in the engine or shell gate reads the intent column; retiring it would stop the approval-void effect at `control.py:784`. Ruling belongs to a person.
+ 12. Stack tag report (batch 1 B2): `scripts/ops/stack_tags.py` exits 0 when `docker ps` fails, and its hard-coded `EXPECTED_BACKEND_SERVICES` can drift from compose. Both are reviewer decisions, uncommitted and not deployed.
+ 13. W27 live drills (batch 1 B3): `w27-denial` and `w27-breaker` are `blocked` in `docs/eval/corpus.json` with empty driver `args`. Neither was run; the reviewer keeps their spend and the corpus status change.
+ 14. Redeploy runbook (batch 1 B4): `docs/runbooks/redeploy-worker-and-orchestrator.md` records STATUS's flags and rollbacks, but no command in it was executed and its §5 argv is a reconstruction, not a recorded command.
```

The `-` lines are the current text of `PROJECT-STATE.md` §4 items 4-9, copied
verbatim; the `+` lines are the proposed rewrite. Items 1-3 and the closing
"Not a gap" line are unchanged and therefore not shown. The proposed list adds
five items and grows the file by about 12 lines, staying under the 130-line
limit. **None of it is applied.**
