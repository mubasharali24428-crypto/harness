# hy4 hand-back — SUMMARY

Branch `w28-s1-delegate`, HEAD `f5b298a` (descendant of `4fd8b3a`). Work is
**uncommitted** in the tree, as required. Nothing was committed, pushed,
deployed, built into an image, or spent. No secret was read or set; the live
stack was only read. `docs/yc/` and the W33 decision/readiness docs were left
alone. `docs/briefs/evidence/w21/native-confinement-container.json` is
unchanged (no full test suite was run, so the permitted restore was never
needed).

## Task table — nothing tidied out

| Task | Status | The one thing I am least sure of |
|---|---|---|
| A1 — M01 native-edit drill without a model | DONE | The probe overrides `process.reallyExit` inside the throwaway container to keep the bundled Node SEA alive long enough to call `LocalFileSystem.editText`. It proves the OS boundary, but a reviewer may reasonably call that mechanism too hacky to be evidence. |
| A2 — flaky `test_reclaimed_run_fences_the_original_worker` | DONE | Only one load-only failure mode was reproduced (`reap_zombies() == []` at line 180); STATUS's "fixed 5 s wait" guess never appeared. 60/60 loaded runs pass after the fix, but a second, rarer load-only mode cannot be ruled out. |
| A3 — front end scope request + two costs | **PARTIAL** | Two of the four required checks are environment-blocked: `make frontend` returns rc=1 on two pre-existing 5 s load timeouts in untouched files, and `npm run build` was blocked by the WorkBuddy file-broker `EPIPE` plus a **user-rejected** `.next` delete permission request. The code and every new test are done; those two checks are NOT verified here. |
| A4 — configurable denial thresholds | DONE | Treating a negative value as "disabled" (mirroring `loop_repeat_limit`) rather than garbage→default is a judgment call at the edge of the stated semantics; 0 disables a stop rather than meaning "stop immediately", which is a real operator footgun. |
| B1 — intent permissions design note | DONE | The recommendation to retire the column implies stopping the approval-void effect at `control.py:784`; that is a person's ruling, not something this note can decide. Option A/B test costs are estimates, not measurements. |
| B2 — read-only stack tag report | DONE | `EXPECTED_BACKEND_SERVICES` is a hand-written snapshot of the compose services; it can drift. The script also exits 0 when `docker ps` fails, so a monitoring wrapper cannot use the exit code to detect a broken daemon. |
| B3 — drill hygiene + corpus entries | DONE | The one-line reason lives in `notes` (the corpus's documented place for a blocked prerequisite) rather than a new `reason` key; the `maxCostUsd`/model metadata on the two new entries is unmeasured and inert only while they stay `blocked`. |
| B4 — redeploy runbook | DONE | The §5 command lines are reconstructed from STATUS's parameters plus `.env.example`'s invocation form; STATUS does not record literal argv and **no command in the runbook was run**. |
| SUMMARY (this file) | DONE | The final combined verification command was blocked by a sandbox permission rejection; per-task checks had already passed before that (see below). |

## Honest failures / what is NOT verified

1. **`make frontend` → rc=1** (`Test Files 2 failed | 97 passed (99)`,
   `Tests 2 failed | 1073 passed (1075)`). Both failures are 5 s timeouts in
   files I did not touch:
   `tests/unit/meetingRestore.test.tsx` ("SSR skeleton stays idle…", 5002 ms)
   and `tests/unit/a11yAccessibleNames.test.tsx` ("legacy mode: email input…",
   5102 ms). Both pass in isolation (the a11y file alone: 9 passed, that test
   1055 ms). The identical full suite with
   `npx vitest run --minWorkers=1 --maxWorkers=2` is **rc=0, 99 files / 1075
   tests passed**. Two foreground attempts were also killed by the environment
   (exit 137 SIGTERM, no summary emitted). `npx tsc --noEmit` is rc=0.
2. **`cd section7 && npm run build` → NEEDS-REVIEWER-RUN.** Exact command:
   `cd "/Users/user/untitled folder 2/harness/section7" && npm run build`;
   expected result: `next build` completes with `Compiled successfully` / a
   route table, rc=0. It failed twice with
   `Broker request outcome unknown: write EPIPE`
   (`CODEBUDDY_BROKER_OUTCOME_UNKNOWN` on `mkdir`), and the retry triggered a
   sandbox permission request (delete of a `.next` artifact) that the user
   **rejected**. I did not retry again or work around it.
3. **A final combined verification command** (`git status`, `/bin/ps`, then
   `make static-gates` and `make lint`) was interrupted by another rejected
   sandbox permission request (`delete /bin/ps (rule: **)`). I did not retry it
   or any equivalent. The last clean, verbatim results for those two gates were
   recorded in A4 and B3 (`STATIC_GATES_RC=0`, `MAKE_LINT_RC=0`); after those,
   only markdown documents were added (the B4 runbook and hand-backs), and no
   hand-back contains a markdown link that the docs gate checks. `test_program_docs.py`
   was run for B4 after the runbook landed: 22 passed, rc=0.
4. **Environment workarounds** (not code faults, but a reviewer on another host
   will not need them): pytest needs a unique `--basetemp` per run (the Python
   shim's brokered `mkdir` raises `FileExistsError` on the existing pytest
   root); `make lint` needs `RUFF_NO_CACHE=true` (ruff's cache temp-file rename
   is blocked); full-suite vitest needs reduced worker parallelism on this host.

## What was delivered (one line each)

* **A1** — `scripts/e2e/m01_native_edit_drill.py` + `docs/briefs/evidence/pilot-prep/m01-native-edit.json` (`ok: true`, `verdict: PASS`). Drives the shipped native `editText` with no LLM in one throwaway container: in-scope `src/x.txt` succeeds, `docs/y.txt` refused `EROFS`, outside-`src` hash unchanged, container removed. A writable-mount negative control made the drill FAIL, proving the assertions are falsifiable.
* **A2** — `section4/tests/unit/test_lifecycle_recovery.py` (15 lines). 40/40 isolated passes; reproduced a load-only failure at loaded run 6; 60/60 loaded runs pass after the fix; all burners gone. Cause: the test never stalled A's heartbeat, so under load its 50 ms tick refreshed `heartbeat_at` before B's reaper. No assertion loosened, no production code touched.
* **A3** — `scopeRequest` from `tool_result` events into `deriveView` → `ToolActivity` prompt + "Edit scope" → a confirm-gated `ScopeRequestEditor` / `ScopeRequestSection` that GETs intent and PUTs only on confirm; `estimatedCostUsd` beside `costUsd` with explicit `not metered yet` / `no plan estimate` words. 4 new source files, 6 new vitest cases plus cost/mapping cases in existing tests. Overview tab is `section7/app/.../runDetailAggregate.tsx` (not `src/`); an intent editor already existed (`intentSection.tsx`), contradicting STATUS.
* **A4** — `HARNESS_DENIAL_RUN_LIMIT` / `HARNESS_DENIAL_WINDOW_LIMIT` in `loop_guard.py`, `.env.example`, and `service.py`'s one `LoopGuard` call site; defaults unchanged. Env-knob gate went 60 → 62 reads, all documented. 51 guard/wiring tests pass; a real negative control made 5 new tests fail.
* **B1** — `docs/decisions/2026-11-05-intent-permissions.md`: PROPOSED, blank owner line, every current-behaviour claim cited to a file:line, three options with test costs, one recommendation (retire). No code changed.
* **B2** — `scripts/ops/stack_tags.py` + `scripts/ops/test_stack_tags.py` (7 canned-output tests, no Docker). Live run exits 0 and prints the mix: backend `MIXED` (`vw28s6` ×10, `vw27s3` ×1 orchestrator), daemon `vw27s2`.
* **B3** — two executable bits + one SIM201 line; `w27-denial` / `w27-breaker` added to `docs/eval/corpus.json` as `blocked` with reason "not yet run through the runner", no `implemented` key. `run_eval.py --check`: `{"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}` (was 39/39). Neither drill was run.
* **B4** — `docs/runbooks/redeploy-worker-and-orchestrator.md`: env-file flags and why, the 51-key/0-difference env check, tag rules, rollbacks, no-worker check, verification set; every command labelled TESTED-IN-REPO YES/NO from STATUS only. Nothing in it was executed.

## Guardrails observed

$0 model spend; no paid/hosted API call, no `--include-live`, no drill that
starts a model run. No `docker build`, `docker compose up/down/restart`, no
compose-managed container/volume touched (only `docker ps`, `docker inspect`,
`docker image ls`, and A1's one `docker run --rm`, which removed itself). No
git commit/push/reset/stash/branch change and **no `git checkout` at all**. No
`OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `~/.harness-secrets/`, `deploy/.env`
or root `.env` read. No edits to `docs/reviews/w33-*`; `docs/yc/` untouched;
no person decisions (SLOs, on-call, pricing, counsel, publishing, security
review, credentials). The stray 4-byte `gtv_sofc` tempfile probe from the
paused session was identified, removed, and recorded in the A3 hand-back — no
stray probes remain. `docs/briefs/hy4-handback-PROGRESS.md` is the historical
pause note, not the current state.

## For the reviewer, in order

1. Re-run `npm run build` on a host/permission setting where `.next` cleanup is allowed (the one unverified acceptance check).
2. Re-run `make frontend` on a less loaded machine; expect rc=0. If the two 5 s timeouts recur, they are pre-existing and unrelated to this diff.
3. Decide whether A1's `process.reallyExit` probe is acceptable evidence.
4. Confirm A3's overview/working tab split (costs on overview, scope prompt in working) and the `estimatedCostUsd` null wording.
5. Rule on B1's recommendation; do not merge an approval-policy change from a PROPOSED note.
6. Run the two W27 drills through the runner with the right `--image`/`--model` before flipping their corpus status — that spend is yours.
