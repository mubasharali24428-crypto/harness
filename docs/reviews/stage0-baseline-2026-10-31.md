# Stage 0 — Stabilize and baseline (Roadmap 3.0) — completion record

Taken 2026-10-31. This closes **Roadmap 3.0 Stage 0** per its exit criteria:
*clean reviewable baseline; test/skip ledger; no unresolved merge conflicts;
current evidence linked to revision; do not overwrite other work.* It is a
baseline snapshot, **not** a readiness claim — the product verdict stays
**NOT READY** and Stage 1+ recovery work is still in flight.

## Exact revision

- Baseline commit (reconciliation point): **`8531033`** — the last committed
  state before this baseline. Stage-0 completion adds: the W19 admission
  bootstrap fix and this record.
- Branch: `w1-engine-tools`. Unresolved merge conflicts at baseline: **0**
  (`git ls-files -u` empty). The previously-staged `section7/app/login/page.tsx`
  merge resolution is now handled — see "Login review" below.

## Login review (Stage 0 immediate-action 1)

The staged login change added a **local auto-sign-in that removes authentication**
(module-level guard, silently logs in as `harness@local`). Per Roadmap 3.0, this
"is not evidence of production authentication readiness" and must be reviewed
before deployment. Decision: **it is operator-local convenience, not a baseline
or product-auth artifact.** It was removed from the working tree/index and
preserved recoverably so it cannot leak into the product path or a deployment:

- Preserved as stash `STAGE0-CARVEOUT: operator-local login auto-sign-in …`
  (`git stash list`; also saved to `/tmp/login-autosignin.local.patch`).
- Restore intentionally: `git stash pop` (or apply the patch) — a local-only
  choice, never a shipped default.
- Baseline now builds against the **real** auth path: section7 `tsc --noEmit`
  clean and `vitest` **1015 passed / 93 files** with the real login in place.

## W22 concurrent work — inventory and owner (not overwritten)

The W22 recovery lane (same author `Mubashar Ali`, driven by a separate agent)
has **uncommitted in-flight changes** that this baseline deliberately does NOT
stage, revert, or commit. They belong to Stage 1 and their owner must land them:

- `section4/src/harness_orchestrator/worker.py` (+19/-1), `container_runtime.py`
  (+18/-3) — durable-reaper / orphan-window work.
- `section4/tests/unit/test_w22_orphan_window.py` (new, untracked).
- `scripts/e2e/w22_daemon_kill_drill.py` (+53), `w22_response_cut_drill.py` (+7).
- `docs/m2-continuation-plan.md` (+60), `deploy/docker-compose.yml` (+4).
- 12 untracked evidence JSONs (`w22-dkill-*`, `replay-load-*`).
- Reliability rate on the in-flight image `vw22orphan1`: **5 PASS / 3 FAIL**;
  orphans reaped in 17–37 s on passes, one FAIL at 37 s and one at **214 s**.
  The Stage-1 "repeat where reliability is claimed" gate is therefore **not met**;
  failures are retained (correct per doctrine).

## Deployed topology and images (capture)

Live stack on the one Docker daemon (`desktop-linux`), DB = compose `harness-db-1`
(Postgres-18, loopback :5499). Model = `anthropic/claude-haiku-5.5` via OpenRouter,
`ENGINE_LIVE_MODE=live` (last-wins in `.env`). **Image tags are mixed** — this is
the W22 lane mid-deploy, not a coherent single-revision build, and is flagged as a
Stage-1 cleanup:

| Image | sha256 digest (truncated) |
|---|---|
| harness-backend:vw22orphan1 | `550ce80463195b85…` |
| harness-backend:vw22fix4 | `1d8d33aef58afad7…` |
| harness-worker-daemon:vw22orphan1 | `1d073c43e0005540…` |
| harness-frontend:vw22fix3 | `03bac4ccdae0d9f8…` |

Orchestrator + worker-daemon run `vw22orphan1`; other backends run `vw22fix4`;
frontend runs `vw22fix3`. `vw22fix4` was built clean from `git archive 0df3e5a`.

## Test / skip ledger — environmental vs defect, tracked

Run at this baseline with live Postgres where relevant; skip reasons via `-rs`.

| Suite | Passed | Skipped | Failed | Skip classification |
|---|---|---|---|---|
| section1 (`HARNESS_TEST_PG=1`) | 1287 | 1 | 0 | ENV — `promtool not installed` (proved via the pinned `prom/prometheus:v2.53.0` image last session: 18 rules valid) |
| section4 (`HARNESS_TEST_PG=1`) | 1980 | 19 | 0 | ENV/opt-in — 6 opt-in Docker, opt-in live OpenRouter, opt-in real local model; **no PG skips remain** |
| section11 | 59 | 0 | 0 | — |
| section7 | 1015 (93 files) + tsc clean | 0 | 0 | — |

- **Defect skips: none.** Every skip is environmental or explicitly opt-in.
- **Baseline defects found: 1, and fixed here** (see below).
- **No new defects outstanding** at the committed baseline once the fix lands.

## Defect found during baseline + fix (my W19 work)

`section4/tests/test_context_resolver.py::test_resolve_roundtrip_against_real_section11_index`
failed: `sqlite3.OperationalError: no such table: project_index_reservations`.
Root cause — my W19 admission (migration `0072`, commit `cb88801`) writes the
reservation in `start_index`, but the table was only created by `ensure_schema`
and the section11 conftest, so any bootstrapper that only ran `DDL_SQLITE`
(a cross-section driver, or a partial dev bootstrap) hit a missing table. This
was a real regression in committed work that the Stage-0 suite caught.

Fix: `ContextStore.acquire_reservation` now self-provisions the table with an
idempotent `CREATE TABLE IF NOT EXISTS` (the repo's existing dev-bootstrap
posture) before the lease upsert, so admission works regardless of who created
the schema. Verified: the cross-section test passes, section11 stays 59 green,
ruff clean.

## Stage 0 exit — status against each criterion

| Criterion | Met? | Evidence |
|---|---|---|
| Clean, reviewable baseline | ✅ | HEAD `8531033`; the login auth-removal carved out; the one defect fixed; suites green |
| Test/skip ledger, env vs defect | ✅ | table above; zero defect-skips; one defect found+fixed |
| No unresolved merge conflicts | ✅ | `git ls-files -u` empty |
| Current evidence linked to revision | ✅ | this doc names commit + image digests; prior W22/W13/W19 evidence at `0df3e5a`/`8531033` linked in the reconciliation + work-order docs |
| Do not overwrite other work | ✅ | W22 lane's in-flight files and stash left intact; commits are pathspec-scoped |

Stage 0 is complete **as a snapshot at this revision**. It does **not** advance
Stage 1: the W22 durable-reaper/orphan-window reliability rate is still in flight
(5/8 passes), the deployed image set is mixed, and the human/owner gates (SLO,
on-call, security, legal, release) remain — none of which Stage 0 closes.
