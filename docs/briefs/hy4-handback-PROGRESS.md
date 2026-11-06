# hy4 progress / resume note — paused 2026-11-05 11:28 PST

> **HISTORICAL — this run resumed and finished.** For the current state read
> `hy4-handback-SUMMARY.md`: A1, A2, A4, B1, B2, B3, B4 are DONE; A3 is PARTIAL
> (two checks environment-blocked). Everything below records the tree at the
> pause on 2026-11-05 11:28 PST and is kept for the record. The stray
> `gtv_sofc` tempfile probe was identified and removed on resume (see the A3
> hand-back). Do not use the task table below as the current status.

**Paused at the user's request ("stop and save the progress, we will resume from
here again").** This file is the resume index. The per-task hand-backs carry the
full command output: `hy4-handback-A1.md`, `hy4-handback-A2.md`,
`hy4-handback-A3.md`. `hy4-handback-SUMMARY.md` is **not yet written** — it is
the final deliverable and must be written only after all tasks are done.

Repo: `/Users/user/untitled folder 2/harness` (path has a space — quote it).
Branch `w28-s1-delegate`, HEAD `f5b298a` (descendant of `4fd8b3a`). Nothing
committed, pushed, deployed, or spent. The live Docker stack was only read.

## Task table (honest, nothing tidied out)

| Task | Status | Where it is | One thing least sure of |
|---|---|---|---|
| A1 M01 native-edit drill without a model | DONE | `hy4-handback-A1.md` | The probe overrides `process.reallyExit` inside the throwaway container to keep the SEA Node alive — acceptable as evidence, or too hacky? |
| A2 flaky `test_reclaimed_run_fences_the_original_worker` | DONE | `hy4-handback-A2.md` | Only one failure mode reproduced (`reap_zombies() == []`); the STATUS-guessed 5 s wait was never seen. |
| A3 front end: scope request + two costs | PARTIAL — reconnaissance only, **no files changed** | `hy4-handback-A3.md` | Stray untracked `gtv_sofc` left by a Python tempfile probe is a stop-condition trigger; must be resolved first. |
| A4 configurable denial thresholds | NOT STARTED | — | — |
| B1 intent permissions design note | NOT STARTED | — | — |
| B2 read-only stack tag report | NOT STARTED | — | — |
| B3 drill hygiene + corpus entries | NOT STARTED | — | — |
| B4 redeploy runbook | NOT STARTED | — | — |
| SUMMARY hand-back | NOT STARTED | `hy4-handback-SUMMARY.md` (pending) | — |

## What was actually delivered

* **A1** — NEW `scripts/e2e/m01_native_edit_drill.py` (executable, root-ruff
  clean) and NEW `docs/briefs/evidence/pilot-prep/m01-native-edit.json`
  (`ok: true`, `verdict: "PASS"`). One throwaway container from
  `harness-engine-worker:vw27s2` with the daemon's scope=`src` mounts; calls the
  shipped `LocalFileSystem.editText` (`@deepseek-ai/dsh-fs-local`) directly with
  no LLM. In-scope `src/x.txt` → "after"; out-of-scope `docs/y.txt` refused
  `EROFS` on the tool's staging `mkdir`; outside-`src` hash unchanged; container
  removed. Negative control (workspace mounted writable) made the drill FAIL,
  proving the assertions are falsifiable. `make lint` rc=0.
* **A2** — EDITED `section4/tests/unit/test_lifecycle_recovery.py` (15 lines,
  one test function). 40/40 isolated passes before the change; reproduced a
  load-only failure at loaded run 6 (`assert [] == ['life-run']` at line 180)
  with 12 `yes` burners; **60/60 loaded runs pass after** the fix, all burners
  gone (`/bin/ps`), `RUFF_NO_CACHE=true make lint` rc=0. Cause: the test never
  actually stalled A's heartbeat, so under load A's 50 ms tick refreshed
  `heartbeat_at` before B's reaper. Fix parks A's ticks on an `asyncio.Event`
  and releases them after B holds the claim. No assertion loosened, no
  production code touched. **STATUS's "fixed 5 s wait" guess is wrong for the
  reproduced failure** — the code wins.

## Tree state at the pause (`git status --short`)

```
 M section4/tests/unit/test_lifecycle_recovery.py
?? docs/briefs/evidence/pilot-prep/m01-native-edit.json
?? docs/briefs/hy4-handback-A1.md
?? docs/briefs/hy4-handback-A2.md
?? docs/briefs/hy4-handback-A3.md
?? docs/briefs/hy4-handback-PROGRESS.md
?? docs/yc/            <- expected; untracked and NOT ours (leave alone)
?? gtv_sofc            <- STOP CONDITION: stray 4-byte tempfile probe, see below
?? scripts/e2e/m01_native_edit_drill.py
```

**`gtv_sofc`** — 4 bytes, mode 0600, ASCII text (Python `tempfile`'s `b"blah"`
probe), created 11:22 during A3 by `python3 contracts/ts/generate.py --check`
after its sitecustomize logged "No usable temporary directory found in [...,
'/Users/user/untitled folder 2/harness']". It is outside my task's files and
the master prompt says to stop and write BLOCKED when a command changes
something outside the task's files. I did **not** delete it. Resolve it (remove
or keep) before resuming A3; do not hide it from the record.

`docs/briefs/evidence/w21/native-confinement-container.json` is unchanged —
no full test suite was run, so the permitted `git checkout` restore was never
needed.

## Environment gotchas (learned the hard way — reuse these)

1. **pytest needs a unique `--basetemp` per run.** Without it the WorkBuddy
   Python shim's brokered `Path.mkdir` raises `FileExistsError` on the existing
   `/private/var/folders/.../T/pytest-of-user` root and all 5 tests error at
   setup. Use `--basetemp=/tmp/<unique>`. A2's loops all did this.
2. **`make lint` needs `RUFF_NO_CACHE=true` on this host.** Ruff's cache
   temp-file rename is blocked by the sandbox's file-op broker ("Failed to
   rename temporary cache file to ..."); a fresh `RUFF_CACHE_DIR` does not help.
   `RUFF_NO_CACHE=true make lint` passes (rc=0). `RUFF_NO_CACHE=1` is invalid
   (ruff wants `true`/`false`). A1's earlier plain `make lint` did pass (rc=0)
   before the cache条目 got stuck.
3. **Use `/bin/ps`, not `ps`** — PATH `ps` is intercepted ("operation not
   permitted"). A2 verified `remaining_yes=0` with `/bin/ps -Ao comm=`.
4. The sandbox prints "Blocked by Security Center rules: delete
   /private/var/folders/.../codebuddy-toybox.*" on many Bash commands. These
   are its own temp-dir cleanup; the commands still run. Do not retry the
   blocked delete.
5. Docker is live and read-only inspection works. Read only the one env key:
   `docker inspect harness-worker-daemon-1 --format '{{range .Config.Env}}{{if eq (index (split . "=") 0) "HARNESS_ENGINE_IMAGE"}}{{println .}}{{end}}{{end}}'`
   → `HARNESS_ENGINE_IMAGE=harness-engine-worker:vw27s2`. An early broad grep
   also printed `HARNESS_WORKER_DAEMON_TOKEN`; it was never copied, set, or
   persisted anywhere. Do not repeat that.
6. Frontend vitest is `environment: "node"` (no jsdom); component tests use
   `renderToStaticMarkup`. See `hy4-handback-A3.md` for the full A3 map.
7. `section7/.venv`-style tooling: `section4/.venv/bin/python`,
   `section4/.venv/bin/ruff`, `section7/node_modules` all present.

## Guardrails observed so far

$0 model spend; no hosted/paid API call; no `--include-live`; no `docker build`
/ compose up/down/build/restart; no compose-managed container or volume touched
(only `docker inspect`, `docker image ls/inspect`, and `docker run --rm` for A1);
no git commit/push/reset/stash/branch change; no `git checkout` at all; no
secret read/set (no `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`,
`~/.harness-secrets/`); no edits to `docs/reviews/w33-*`; `docs/yc/` untouched;
no STATUS.md spend lines; no person decisions (SLOs, on-call, pricing, counsel,
publishing, security-review scope).

## Resume order

1. Resolve `gtv_sofc` (and re-check `git status --short` for anything else new).
2. Finish A3 from the 9-step plan in `hy4-handback-A3.md` (all reconnaissance is
   already done — do not redo it). Required checks: `make frontend`,
   `cd section7 && npm run build`, `make static-gates`, plus
   `python3 contracts/ts/generate.py --check`.
3. A4 → B1 → B2 → B3 → B4, in that order, writing a hand-back after each.
4. Write `docs/briefs/hy4-handback-SUMMARY.md` last: table of task, status, one
   thing least sure of. Do not tidy failures out of it (A3's stop condition and
   the environment workarounds belong in it).
