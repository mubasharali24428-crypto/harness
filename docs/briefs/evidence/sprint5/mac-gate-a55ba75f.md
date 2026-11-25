# Owner-Mac truth run — `make test-all` + `make gate` at `a55ba75f`

- Date (UTC): 2026-11-25. Host: owner Mac (Darwin 25.6), Docker Desktop 29.5.3, compose PG 18.
- Tree: `a55ba75f` (Tasks 31–39 from cloud + Tasks 33–36 from this Mac), clean except untracked artifacts.
- Purpose: re-run what the cloud could not (Task 38/39): section1/4/5, gitleaks, `check-ts-dist`, Docker P0.

| Command | rc | Result |
|---|---|---|
| `HARNESS_TEST_PG=1 TEST_ALL_DOCKER=1 make test-all` | **0** | integration 3 passed / 1 skipped (checkpoint-1 happy path, pending redesign) · section1 on PG **1361 passed** (incl. the `docker exec` FK-deletion + runs-index tests the cloud could not run) · migrate selftest PASS 79 (0001→0081) · section4 on PG **2504 passed**, 19 skipped · Docker P0 **17 passed**, 1 skipped · promtool **37 passed** · landlock SKIP (Linux-only host rule) |
| `make gate` | **0** | `GATE GREEN` — section1 1299 · section2 61 · section3 149 · section4 2463 · **section5 553 passed, 4 xfailed** · section6 123 · section8 111 · section10 96 · section11 60 · mcp 18 · language 367 · vitest 1243 (122 files) |
| `make check-ts-dist` | **0** | `clients/ts dist matches a fresh build (26 tracked files)` |
| `make ci-local CI_LOCAL_ARGS="--only security-scan --only frontend"` | **0** | GREEN: gitleaks (local `zricethezav/gitleaks`, full history from HEAD) ok; frontend incl. `check-ts-dist` ok. Summary: `docs/briefs/evidence/quality/ci-local/2026-11-25T0452Z-a55ba75f/` |

## Cloud failures, re-run here

| Cloud result (Task 38/39) | Mac result |
|---|---|
| section1: 8–12 env failures (`docker exec`, sqlite variable limit) | PASS (gate 1299; PG 1361) |
| section4: 4 env failures (no daemon, root uid, zombie) | PASS (gate 2463; PG 2504; Docker P0 17) |
| section5: 32 failures (no usable sandbox boundary) | PASS (553, Seatbelt on macOS) |
| gitleaks: needs docker API | PASS |
| `check-ts-dist`: `clients/ts` deps missing | PASS |

## Not covered

- GitHub Actions itself: blocked by account billing (job annotation: "recent account payments have
  failed or your spending limit needs to be increased"). This file is local truth, not CI.
- `readiness-drift` (manifest 0076 vs disk 0081) not re-run: needs the RC redeploy + manifest rewrite.
- Landlock confinement tests only run on Linux.
- The Task 36 live journey is a **FAIL** with defects D2/D3 (separate evidence); the gate does not run e2e.
