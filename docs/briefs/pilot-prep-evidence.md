# Pilot prep evidence (2026-10-18)

| Item | Result | Check |
|---|---|---|
| 9 `KNOWN_UNSPECCED` gaps | Closed; list empty and test-enforced | `make static-gates` exit 0; `test_spec_drift.py` 5 passed |
| Non-orchestrator specs | Regenerated from deployed app factories (6 services + `section10/openapi.json`), SDKs regenerated | drift check compares operations per spec |
| Real sign-in | OTP form with `HARNESS_REQUIRE_OWNERSHIP=true`; code from identity log | `--real` browser test passed (23s) |
| Real worker | Real engine in container, `liquid/lfm-2.5-2.6b:free`; `engine_ok.txt == 'engine ok'` on disk | `evidence/pilot-prep/real-result.json` |
| Original synthetic smoke | Still passes after runner edits | 1 passed |
| Suites | Section 4: 644 passed, 1 skipped (live-OpenRouter opt-in, covered by `--real`); vitest 526 passed | run this session |
| Key hygiene | Scanned docs, deploy, scripts, e2e, test-results and run logs for the key value: no hits | script, value never printed |

## What the 9 gaps were
- 5 checker artefacts (`/billing`, `/keys`, `/health`, `ws`, sandbox tree): served by code or already recorded, but the checker mishandled URLs cut off by template suffixes, URLs in comments, and websockets.
- 2 stale recorded specs (identity SSO start/complete). The same regeneration also added the identity audit and team SSO-config routes, github disconnect and `/ready` on every service.
- 2 routes in services outside the deployed stack (`context/search` in section 11, `onboarding/checklist` in section 10). They are now checked from their routers, and `section10/openapi.json`, which lacked `/checklist`, was regenerated.

## Found by the real run
`staging.ensure_baseline` requires the run workspace to exist before the first start, and nothing creates it (the stand-in stack hid this). Since fixed: `engine.provision_workspace` creates the workspace on first start (optionally seeded from `HARNESS_WORKSPACE_SEED`), and `--real` passes without the proof-stack workaround. A real GitHub checkout is still not built.

## Still not covered
Real inbox delivery (SMTP), multi-user handoff/approval on the real model, an independent security audit, the supervised two-week pilot, and real baseline numbers.
