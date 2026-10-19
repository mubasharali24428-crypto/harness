# Session 3 evidence — organizations, permissions, workspaces (parts 37, 38, 39)

Date 2026-10-18/19. Lanes: O (part 37), P (part 38), W (part 39), each with its own file here. Lead integration below.

## Lead integration (all run by the lead)
- Mounted `orgs`, `policy`, `workspaces` routers in `section1/src/harness_identity/app.py`.
- `policy.require_stepup` / `require_team_stepup` extracted from `_gate`; wired to `PATCH /teams/{id}/members/{user}` (`role.change`)
  and `GET /audit/export` (`export`) for org-linked teams only. Test: `test_policy_api.py::test_stepup_gates_legacy_role_change_and_export_for_org_teams`.
- `sso._mint_session` records SSO assurance (`require_sso` satisfiable). Test: `test_orgs.py::test_sso_session_mint_records_sso_assurance`.
- New live-Postgres test `section1/tests/test_session3_pg.py`: all 40 migrations, then org create/link, step-up-gated policy
  document, explainable check, decision log, workspace create/settings/pause/archive/restore through the real app, then
  `verify_chain` over every audit row written. Passed.
- Defect found and fixed in lane W's test: `test_batch_claim_path_also_gated` stalled in fixture teardown (10 s to forever; hung two
  full-suite runs for 20 min) because a spawned engine task still held sqlite connections at `engine.dispose()`. The fixture now awaits
  spawned tasks first; 8 consecutive runs at 0.8 s.
- E2E flake found by the smoke run: the peer-offline alert is shown by the UI's 20 s safety poll once the push socket is up, but the
  test waited 15 s. Timeout raised to 30 s (`section7/e2e/multiplayer.e2e.ts`). Smoke then passed.
- `scripts/spec_drift.py --write` and sdk-gen (python, ts) regenerated for the new routes. Regenerating `section5/openapi.json`
  would drop hand-written 400/409 response docs (commit 5414a14), so that file was left as committed.

## Verification record
See the final table in `docs/briefs/PROJECT-STATE.md` section 5 (counts are filled in from the final run).

## Honest status
- Part 37: verified at cap 8. Live SSO/Stripe unproven; no MFA factor; provider API keys not user-bound.
- Part 38: **partial**. The engine and its controls are tested, but most legacy routes and all section4 run/approve/steer paths still use
  the old role check, so "every read and mutation" (38.005) is not met.
- Part 39: verified with listed gaps (no purge job, running run not stopped on archive, quota check race, residency is a label).
- Not run: `make gate`, `make e2e*` (only the multiplayer leg via smoke), pip-audit, Linux Landlock, `run_smoke.py --real`.
