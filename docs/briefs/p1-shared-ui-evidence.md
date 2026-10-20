# P1: shared UI end-to-end exit evidence

**Status: P1 complete (2026-10-18).** P2/P3 and the pilot gate remain outstanding.

## Browser proof (roadmap 1.1, 1.3, 1.4)
`section4/.venv/bin/python deploy/pilot/run_smoke.py` builds the test image, creates a
fresh disposable PostgreSQL 18 database, migrates it, starts real identity/orchestrator/sandbox
services plus Next, then runs `section7/e2e/multiplayer.e2e.ts` (retries=0) with two separate
browser contexts and no API mocks: login, propose/approve plan, join, leader claim, guidance,
handoff, start, withheld deletion shown, approve, apply, Done unblocks, stop, summary refresh.
Result: **1 passed (21.8s), zero console/network/page errors**; the approved deletion was
verified in the real workspace; processes and database removed afterwards.
Evidence: `evidence/p1/browser.junit.xml`, `multiplayer-complete.png`, `live-result.json`.

Limits: synthetic dev sign-in (no email delivery proof); the worker is the deterministic
Docker stand-in (`deploy/pilot/worker.py`, test target only). The real runtime is covered
by the P0 hello-world and the OpenRouter test below.

## Fixes found by the flow (1.2)
Returning login reuses a single team; handoff accepts ORM `ENGINEER`; done and draft-PR
blocked while staged approvals are unapplied (fail closed); owned project with no checkpoint
repo lists empty (foreign still denied); provider errors preserved; run-page summary now
polls until terminal (it went stale after stop); login waits for hydration.

## Final regression (run separately, not one combined command)
| Suite | Result |
|---|---|
| Section 4, all opt-ins (Docker, live Ollama, PG 5814, free OpenRouter `liquid/lfm-2.5-2.6b:free`) | **620 passed, 0 skipped** (`evidence/p1/section4-final.junit.xml`) |
| Section 1 (`HARNESS_REQUIRE_OWNERSHIP=true`) | 640 passed, 10 skipped by default (all PG-gated); with `HARNESS_TEST_PG` + `HARNESS_DATABASE_URL` on scratch PG18: **650 passed, 0 skipped** |
| Section 2 | 39 passed |
| Section 5 | 182 passed, 1 skipped (landlock is Linux-only; expected on macOS) |
| Section 7 vitest (2 workers) | 525 passed |
| Ruff: sections 1, 2, 4, 5, `deploy/pilot` | clean |
| `git diff --check` | clean |

`tsc --noEmit` reports one error in generated `.next/types` about the run page's `params`
union type; the `params` line is identical at HEAD, so it predates this work.

## Skip audit: the original 25 Section 4 skips
- 18 opt-in Docker tests (`HARNESS_P0_DOCKER`): pass, see `evidence/p0/`.
- 6 real-PostgreSQL tests (`HARNESS_TEST_PG`; cost rollup, demo seed): pass on PG18.
- 1 real-provider test (`HARNESS_LIVE_OPENROUTER`): passes on a model whose catalog price is
  zero (checked by the test before sending); uses `tests/fixtures/openrouter.patch.yml`
  because the bundled adapter speaks Anthropic Messages, which OpenRouter rejects.
Final combined run shows none skipped.

## Still outstanding
P2 (security review, leader self-approval, audit events, Postgres chaos), P3 (contracts/openapi
drift, retention, metrics, release plan). Provider-side checks need Docker, Ollama and keys,
so the opt-in tests skip by default on hosts without them.
