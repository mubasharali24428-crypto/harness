# Threat-model evidence (S1 Lane D, part 29), 2026-10-18

No source file was modified. Probe scripts live in the session scratchpad (`.../scratchpad/probe/p.py`, `p2.py`, `p3.py`) and used temp dirs only. Full suites were not run (lane A).

## Named tests run (all pass)

| Command (cwd = section dir) | Result |
|---|---|
| `section4: .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_collab_backend.py tests/unit/test_staging_digest_binding.py tests/unit/test_isolation_and_staging.py tests/unit/test_run_isolation_and_stop_truth.py tests/unit/test_container_boundary.py tests/unit/test_retention.py tests/unit/test_collab_denial_audit.py` | 134 passed, 0 failed (7.4 s) |
| `section1: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ws_auth.py tests/test_c5_ws_hygiene.py tests/test_ws_fingerprint.py tests/test_ownership.py tests/test_auth_verification.py` | 56 passed, 1 skipped |
| `section5: .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_s1_r01_authz.py tests/unit/test_diff_engine.py tests/conformance/test_tree_api.py tests/unit/test_confinement.py` | 35 passed, 1 skipped |

Not run: `test_collab_chaos_live_pg.py` (needs `HARNESS_TEST_PG`), `section4/tests/integration/test_container_p0.py` (needs Docker), section1 `test_ws_replay_cap.py` and `test_sso_oidc.py` (outside the named set). Claims about those rest on reading them.

## Probes (confirm gaps; `-I` isolated Python, section5 venv)

1. `p.py` (runner level, `LocalSandboxRunner(confinement="off")`):
   - `create_task("../victim", ..., team_id="evil")` gave workspace `<tmp>/store/../victim/workspace` (outside the storage root).
   - Two `create_task("t1")` calls for teams A then B left `active_tasks["t1"]["team_id"] == "B"`.
   - `destroy()` on the `../victim` handle removed `<tmp>/victim` (`victim dir survives destroy: False`).
2. `p2.py` (HTTP via `TestClient`, principals overridden per team):
   - `A create 201`, `B create same id 201`, `A exec after takeover: 404`, `B exec` reached the shared workspace (blocked only by the `secret` filename rule, not by ownership).
   - `traversal create 201 True`: `taskId "../escaped"` created `<tmp>/escaped`.
3. `p3.py` (`apply_unified_diff`, forbidden `["secrets/"]`): `secrets/x.txt`, `./secrets/x.txt`, `a/../secrets/x.txt` blocked; `link.txt` (symlink to `secrets/x.txt`) was APPLIED and the forbidden file now reads `PWNED`.

## Code reads backing "MITIGATION NOT FOUND" (no probe)

- T7b: `section5/src/harness_sandbox/confinement.py:90-104` (writes to `gettempdir()`, `file-read*` global) and `:183` (landlock allows temp), `config.py:11` (`storage_root=/tmp/harness_sandboxes`), `docker_runner.py:4-7` (empty subclass).
- T10b: `section1/src/harness_identity/app.py:793-845` (no re-check after `accept`), `ephemeral.py:64-100` (role stored at mint).
- T4b: `section4/src/harness_orchestrator/collab.py:210-215` vs `:660-668`.
- T11b: `collab.py:355-364`.
- T13b: `section1/src/harness_identity/auth.py:167-224`.
- T14: grep `auth/logout` across `section1/tests` found only `test_g5a_api_hardening.py:172` (route list).

## Greps used to decide UNTESTED

- Forged actor fields: grep for `forg|spoof|requesterUserId|"actor"` in section4 unit tests found only `test_guidance_cannot_close_its_frame_or_forge_a_leader_line` (guidance text, not identity).
- Section5 `taskId`/traversal: `tests/` hits for `../` are all for path-edit, sha and tree-listing guards, none for `taskId`.
