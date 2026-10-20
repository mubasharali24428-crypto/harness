# P3 exit evidence — contracts, data rules, metrics, release (2026-10-18)

Not committed. Pilot gate (a supervised 2-week pilot with real teams) is **still outstanding**.

| # | Deliverable | Evidence |
|---|---|---|
| 3.1 | Drift check: `scripts/spec_drift.py` regenerates the orchestrator OpenAPI from the four routers (api, runs_index, plan_gate, demo), compares `section4/openapi.json` and the sdk-gen spec, and checks every UI API call resolves to a route. Wired into `make static-gates`. Regenerated spec, Python and TS clients. | `section4/tests/unit/test_spec_drift.py`; `scripts/spec_drift.py` → "specs and UI calls match the router"; `python3 contracts/ts/generate.py --check` → up to date |
| 3.2 | Retention: `harness_orchestrator.retention` (dry-run default, `--apply`, `--days`, `--include-workspace`). Removes staging baseline, withheld files, engine session dir, approval diffs, collab events for finished runs older than N days with no open approval. Audit log and approval metadata never purged. Path-safe (id allowlist, realpath containment). | `test_retention.py`; real-PG case in `test_collab_chaos_live_pg.py`; policy in the [release plan](multiplayer-pilot-release-plan.md) |
| 3.3 | Metrics: `harness_orchestrator.pilot_metrics <team>` — created/finished/stopped/accepted, elapsed, redirects, approvals + latency, cancel confirmations. Real numbers from the browser smoke: `evidence/p3/pilot-metrics-synthetic-run.json` (1 run, ≈20.1 s, approval decision ≈2.5 s, 1 redirect, 1 cancel confirmed). | `test_pilot_metrics.py`. **No baseline exists**; output says so and supports no ROI claim |
| 3.4 | Release plan, rollback, support runbook, known limits. Code-level per-team flag `HARNESS_PILOT_TEAMS` added and tested over HTTP (404 for unlisted teams on all 15 routes). | [multiplayer-pilot-release-plan.md](multiplayer-pilot-release-plan.md) (draft, needs human review) |
| 3.5 | Checkpoint and deliverables updated | roadmap, readiness, session checkpoint |

## Final regressions (run separately, 2026-10-18; not one combined suite)
| Suite | Result | Evidence file |
|---|---|---|
| Section 4, all opt-ins (`HARNESS_OPENROUTER_KEY_FILE` pointing at the gitignored key file, `HARNESS_P0_DOCKER=1`, `HARNESS_P0_LIVE=1`, `HARNESS_LIVE_OPENROUTER=1` with `liquid/lfm-2.5-2.6b:free`, `HARNESS_TEST_PG` on scratch PG18; worker images rebuilt first) | **643 passed, 0 skipped** (re-run after all changes below) | `evidence/p3/section4-final.junit.xml` |
| Section 4 default (no opt-ins) | 617 passed, 19 skipped (all opt-in) | — |
| Section 1 (`HARNESS_REQUIRE_OWNERSHIP=true`, PG) | **650 passed, 0 skipped** | `evidence/p3/section1-final.junit.xml` |
| Section 2 | 39 passed | `evidence/p3/section2-final.junit.xml` |
| Section 5 | 182 passed, 1 skipped | `evidence/p3/section5-final.junit.xml` |
| Section 7 vitest | 526 passed (59 files) | — |
| Lint / hygiene | ruff clean (sections 1, 4, deploy/pilot); `git diff --check` clean | — |

Browser smoke re-run after the final code changes on a fresh PG18 DB: 1 passed (see `evidence/p1/` and `evidence/p3/pilot-metrics-synthetic-run.json`).

## Verification beyond the suites
- Drift check proven to fail: removing a path from `openapi.json` gave exit 1 ("missing POST /api/v1/orchestrator/demo/seed"); restored → exit 0. `make static-gates` exits 0.
- Found and fixed: `make static-gates` was failing on the isolation guard (a Section 4 test defaulted to a `section3/` key path). The key file now comes only from `HARNESS_OPENROUTER_KEY_FILE`.
- Retention and metrics CLIs run as real processes against a migrated scratch PG: dry-run deleted nothing; `--apply` removed only the 45-day-old run's staging and session dirs; second apply was a no-op; metrics printed JSON. Found and fixed: negative durations (event before run creation) are now dropped from timing summaries.
- Second-pass review (own code, beyond P2). Fixed: (1) **retention could delete the dirs of a resumed run** whose old finish event was past the cutoff but which had newer events: eligibility now also needs the latest event of any type to be old (test + real-PG CLI check); (2) **oversized team guidance was truncated while the delivery cursor skipped past it**, silently losing teammates' direction: the cursor now advances only past what was delivered, the rest follows on the next call (test). Noted, not fixed: a leader can mark a staged approval complete without applying (changes stay withheld); no rate limit on collaboration posts beyond per-event size caps; teammate guidance is prompt input by design (framed, tag-neutralised).
- Drift ratchet: UI-called URLs for every other service must be in a recorded spec or in `KNOWN_UNSPECCED` (9 pre-existing gaps found); a new gap, or a listed gap that got fixed, fails the gate (test).
- Flag UX: the flag's 404 now carries `PILOT_NOT_ENABLED`; the UI shows a plain notice and stops polling (vitest).
- Baseline: `pilot_metrics --baseline FILE` and `pilot-baseline-template.json`; refuses to claim under 5 tasks per side (tests + real-PG CLI run).
- Key hygiene: key value not found in docs, sources or e2e files.
Note: Section 1 live-PG tests need `HARNESS_TEST_PG` as a plain `postgresql://` DSN and `HARNESS_DATABASE_URL` as `postgresql+asyncpg://`.

## Limitations (also in the release plan)
Synthetic sign-in and stand-in worker in the browser proof; spec regeneration covers the orchestrator only (others get UI-coverage checks); metrics have no baseline; P2 review was focused, not a full audit; `agent_runs.state` deliberately unmaintained (derived from events + audit); real baseline numbers and a real pilot (tooling only); one Section 5 skip.
