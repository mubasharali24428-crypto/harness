# P2: hardening and review, evidence (2026-10-18)

**Status: P2 work items implemented and verified. P3 and the pilot gate remain outstanding.**
Every fix below had a failing test first (except 2.5, which P1 had already delivered).

## 2.1 Security review (lanes reviewed: staging, guidance, approvals/digests, run scoping)
| Finding | Severity | Fix | Test |
|---|---|---|---|
| Agent replaces a workspace directory with a symlink to a host directory; the orchestrator's `staging.stage()` then wrote/unlinked **through the link, deleting a file outside the workspace** (reproduced before the fix) | High | `_inside()` containment check before every stage write/unlink; fails closed with `StagingError` | `test_dir_replaced_by_symlink_cannot_make_staging_touch_outside_files` |
| Guidance text could close the `<team_guidance>` frame or forge a "scope change (leader)" line via newlines | Medium | Collapse to one line per entry; neutralise frame tags | `test_guidance_cannot_close_its_frame_or_forge_a_leader_line` |
| Digest replay, cross-team and cross-run approval IDs, hostile paths, stale digests | Reviewed | Already covered by existing tests (`test_collab_backend.py`); no change needed | existing |

Not reviewed or not changed: Section 5 sandbox routes, cross-project access inside one team (access is team-level by design), worker-side filesystem confinement (covered by the P0 container suite). This is a focused review of the shared-run lanes, not a full audit.

## 2.2 Leader self-approval
A requester can no longer approve their own request (403 `SELF_APPROVAL`, audited as `approval.self_denied`, request stays pending). Rejecting or withdrawing your own request is still allowed. Policy is configurable: `HARNESS_ALLOW_SELF_APPROVAL=1` restores the old behaviour for single-person teams. Staged-change approvals are filed by `system:staging`, so the normal flow is unaffected.
Test: `test_leader_cannot_approve_own_request_unless_policy_allows`.

## 2.3 Audit events
`POST /runs/{id}/stop` now writes `run.cancel_requested` before cancelling and `run.cancel_confirmed` only once the worker is confirmed gone (a "stopping" response writes no confirmation). Apply (`approval.claimed`, `approval.completed`) and reject (`approval.rejected`) were already audited. New actions are added to the Section 1 append-only allowlist (`run.cancel_requested`, `run.cancel_confirmed`, `approval.self_denied`).
Tests: `test_s1_r05_lifecycle_audit.py` (two stop tests updated, one added).

## 2.4 Chaos on Postgres (not SQLite)
`section4/tests/unit/test_collab_chaos_live_pg.py` (opt-in `HARNESS_TEST_PG`, full migration chain, throwaway DB each): 8 concurrent leader claims give exactly one leader; concurrent approve/reject give one winner; a pending approval and the guidance cursor survive a restart (new engine on the same database).
The **guidance cursor is now persisted** (previously memory-only, so a restart re-delivered all guidance) as an append-only `guidance.delivered` event written after the engine call returns; a crash causes a re-delivery, never a loss. It is hidden from collaboration history. No schema migration.
Not covered on Postgres: "kill worker mid-edit" (covered by the P0 live container stop checks, not a PG test).

## 2.5 Done/PR blocked while staged approvals are pending
Delivered and tested in P1 (Section 4 done gate, Section 2 draft-PR gate, UI). Re-verified by the browser smoke below.

## Verification (after all P2 changes)
- Section 4, all opt-ins (Docker, Ollama, PG, free OpenRouter): **628 passed, 0 skipped** (`evidence/p2/section4-final.junit.xml`).
- Section 1: 640 passed, 10 skipped (PG-gated; 650/0 with PG as recorded in P1). Ruff clean in Sections 1 and 4.
- Browser smoke on a fresh PG18 database re-run: 1 passed, zero browser errors.
- `git diff --check` clean.

## Still outstanding
P3 (contract/openapi drift, retention, metrics, release plan) and the supervised-pilot gate.
