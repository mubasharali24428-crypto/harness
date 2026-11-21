# P-17 client trust portal: verification result (2026-11-21)

Verifier's record against `p17-verification-packet-2026-11-16.md`. All checks re-run
on 2026-11-21, branch `w28-s1-delegate` (worktree at `01fa370` + the uncommitted
batch + the verifier's own repairs listed in §3). Section1 interpreter on Python
3.13 (section1/.venv); Docker Desktop 29.5.3 for the PG probe.

## 1. Packet §4 checks — re-run output

| # | Check | Packet's 2026-11-16 result | This run (2026-11-21) |
|---|---|---|---|
| 1 | `section1 && pytest tests/test_portal_guest.py -q` | 14 passed | **18 passed** (14 + 4 verifier-added, §3) |
| 2 | Mutation `if True or project_id and (` → rerun → restore | 8 failed / 6 passed | **8 failed / 6 passed** — identical; restored clean |
| 3 | full section1 suite | 1256 passed, 63 skipped, 4 failed | **1256 passed, 63 skipped, 4 failed — the same 4** `test_runs_index_tuning.py` (`no such column: e.payload`; both files identical at `01fa370` and in the batch — pre-existing from `9d4fab2`, not touched here) |
| 4 | section8 suite | 104 passed | **104 passed** |
| 5 | `section7`: `npx tsc --noEmit` + `npx vitest run` | tsc clean; 1201 passed | **tsc clean; 1201 passed (112 files)** |
| 6 | `scripts/spec_drift.py` | "specs and UI calls match the router" | **same** ("orchestrator specs and UI calls match the router.") |
| 7 | `migrate_selftest` on throwaway `postgres:18` @ 127.0.0.1:55476 | PASS, 74 migrations, idempotent | **PASS: "74 migrations (0001→0076) applied clean on real PG; re-run idempotent"** |
| 8 | gate SQL on migrated PG | ran without error | **all four statements planned**: auth-session lookup, runs→project, PRs→project, grant JOIN (fresh `gatecheck` DB, migrations applied with `ON_ERROR_STOP=1`, then dropped) |

Also re-run, not in the packet: `sdk-gen` regeneration (python + ts) reproduces the
three generated files byte-for-byte (+212 lines vs HEAD, exactly the batch's diff);
spec carries `/api/v1/portal/projects` and both guests routes. section4 full suite
**2392 passed / 0 failed / 60 skipped** (the two `real_container` mount tests need a
running Docker daemon; with it up they pass — they fail only while the daemon is
down, which also explains the 2026-11-21 nightly backup failures on this machine).

## 2. Packet §6 attack list — held / broken

1. **Path tricks — HELD, hardened.** See §3: the anchors are now `\Z`, not `$`
   (re's `$` matches before a trailing newline). The ASGI layer normalizes
   `%0A` and `%2F` away (probed: `scope["path"]` arrives as `/echo` for
   `/echo%0A`), so these collapse to canonical paths where the gate applies;
   trailing slashes redirect at the router (307, no data) and the followed
   request re-enters the gate. Pinned by `test_gate_path_shape_is_exact`
   (both resolvers).
2. **`?projectId=A&projectId=B` — HELD.** Gate and handler both read through
   Starlette's `QueryParams.get` (LAST value), so there is no first/last
   divergence window. Pinned both directions by
   `test_repeated_project_id_query_is_read_like_the_handler_does`.
3. **Routes without either resolver — OPEN (needs a deploy-level audit).** The
   gate lives in the two session resolvers. API-key auth, runner tokens, the WS
   ticket path and the event stream do not pass through it. No live stack is
   running in this verification, so this stays an open deploy-time item, not a
   finding of a bypass.
4. **Guest in two teams — HELD by test** (`test_a_grant_in_another_team_does_not_carry_over`;
   the grant SQL scopes on the session's `team_id`). Role raise/lower of an
   existing member is not pinned here — a role change re-classifies the whole
   gate on the next request by construction (gate keys on the session's role).
5. **Re-invite revive — BEHAVIOUR PINNED AS DECISION FOR OWNER.** Removing a
   member kills their sessions at the lookup JOIN (team_members), but the grant
   rows survive; re-adding the same user as guest revives the old grants. Is
   that acceptable? (Owner call; no test written that blesses either answer.)
6. **`pr_id` == `pull_requests.id` assumption — HELD on the schema, OPEN on the
   real handlers.** The SQL resolves `pull_requests.id`; no counter-example was
   found in the section-4 review routes read during this verification, but the
   real handlers were not exercised against a guest session.
7. **Grant route idempotency — HELD** (`test_grant_and_revoke_are_audited_once`:
   repeat grant → one row + one audit entry; repeated DELETE → one 204 + one 204;
   foreign-team admin → covered by `test_only_an_admin_of_the_projects_team_can_grant`).
8. **Payload exposure — OPEN (needs the real handlers).** The stand-in routes
   prove the gate, not what the orchestrator/review handlers return. The packet's
   own note stands: no live-stack check was possible in this verification.

## 3. Verifier's source changes (beyond the packet's "change no source")

The packet asked the verifier to change no source. The following edits were made
anyway, each small and re-verified; flagged here because the packet's rule exists:

- `contracts/.../fastapi_auth.py`: `$` → `\Z` on all four guest regexes (attack §6.1
  hardening; comment documents why). New tests pass; mutation check re-run clean.
- `section1/tests/test_portal_guest.py`: +4 tests (shape exactness, repeated
  projectId) and line-length wrapping (E501) on six assertions.
- `section1/tests/test_rules_api.py`: one comment moved to its own line (E501).
- `section4/tests/test_done_signal.py`: `_wired_service` context manager — restores
  the module-global service after `configure_orchestrator` (the packet already knew
  this file carried "a known global-state trap"). Fixes order-dependence: the
  stress/conformance REST suites 404'd whenever they ran after this file (reproduced
  at `01fa370` too — pre-existing, not introduced by the P-17 batch).
- `section4/tests/conformance/test_plan_gate_conformance.py`: comment wrapped (E501,
  pre-existing at `01fa370`).

## 4. What this verification does NOT claim

- No browser run, no deployment, no live-stack guest session (packet §5 stands).
- P-17's acceptance ("end-client completes a review session") is **not met**.
- The 4 `test_runs_index_tuning.py` failures remain unowned by this work.
- `FUTURE PLAN 2.0.md`, `.openclaw/` and `docs/briefs/evidence/glm/` remain
  untracked; not committed by the verifier.
