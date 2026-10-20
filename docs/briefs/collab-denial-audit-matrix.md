# Collaboration denial audit matrix — 2026-10-18

Real HTTP routes and SQLite audit rows, with authorization dependencies intact. Route paths below follow `/api/v1/orchestrator/runs/r1`. `a` and `h` are fixture-generated approval/handoff IDs.

**Policy (implemented):** every refused collaboration request by an authenticated principal (foreign run, role, participant/leader/requester/state/recipient refusal) writes exactly one audit row. Refusals not already audited by their own site (`approval.self_denied`, `approval.expired`, leader `authz.denied`) get `authz.denied` with `reason` = stable error code, `status`, and `attempt` = `METHOD route`. Tests measure only new audit rows after setup and require exactly one expected action; status/code mismatches call `pytest.fail`. Not audited: pydantic body validation (never reaches a handler), `PILOT_NOT_ENABLED` (non-allowlisted teams, UI polls), and non-denial 409/503 infrastructure responses. Cross-team probing of unknown run ids is audited under the caller's team; volume is bounded only by the existing rate limits.

| Route | Denial | HTTP / code | Audit written | Test name |
|---|---|---|---|---|
| POST /join | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[join]` |
| GET /participants | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[roster]` |
| POST /leader | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[leader]` |
| POST /leader | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[leader]` |
| POST /redirect | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[redirect]` |
| POST /redirect | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[redirect]` |
| GET /collab-history | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[history]` |
| POST /handoff | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[handoff]` |
| POST /handoff | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[handoff]` |
| POST /handoff/h | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[handoff_decide]` |
| POST /handoff/h | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[handoff_decide]` |
| POST /approvals | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[request]` |
| POST /approvals | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[request]` |
| GET /approvals | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[list]` |
| PUT /approvals/a | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[amend]` |
| PUT /approvals/a | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[amend]` |
| POST /approvals/a/decision | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[decision]` |
| POST /approvals/a/decision | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[decision]` |
| POST /approvals/a/claim | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[claim]` |
| POST /approvals/a/claim | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[claim]` |
| POST /approvals/a/complete | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[complete]` |
| POST /approvals/a/complete | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[complete]` |
| POST /staged/approvals | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[staged]` |
| POST /staged/approvals | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[staged]` |
| POST /approvals/a/apply | cross-team | 404 / NOT_FOUND | yes (authz.denied) | `test_cross_team_audit[apply]` |
| POST /approvals/a/apply | viewer write | 403 / ROLE_DENIED | yes (authz.denied) | `test_viewer_write_audit[apply]` |
| POST /redirect | redirect_nonparticipant | 403 / NOT_PARTICIPANT | yes (authz.denied) | `test_collab_denial_audit[redirect_nonparticipant]` |
| POST /approvals | request_nonparticipant | 403 / NOT_PARTICIPANT | yes (authz.denied) | `test_collab_denial_audit[request_nonparticipant]` |
| PUT /approvals/{a} | amend_nonrequester | 403 / NOT_REQUESTER | yes (authz.denied) | `test_collab_denial_audit[amend_nonrequester]` |
| POST /redirect | scope_nonleader | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[scope_nonleader]` |
| POST /redirect | permission_nonleader | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[permission_nonleader]` |
| POST /handoff | handoff_nonleader | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[handoff_nonleader]` |
| POST /approvals/{a}/decision | decision_nonleader | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[decision_nonleader]` |
| POST /approvals/{a}/decision | self_approval | 403 / SELF_APPROVAL | yes (approval.self_denied) | `test_collab_denial_audit[self_approval]` |
| POST /approvals/{a}/decision | decision_expired | 409 / EXPIRED | yes (approval.expired) | `test_collab_denial_audit[decision_expired]` |
| POST /approvals/{a}/decision | decision_stale | 409 / DIGEST_MISMATCH | yes (authz.denied) | `test_collab_denial_audit[decision_stale]` |
| POST /approvals/{a}/decision | decision_replay | 409 / NOT_PENDING | yes (authz.denied) | `test_collab_denial_audit[decision_replay]` |
| POST /handoff/{h} | handoff_wrong_recipient | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[handoff_wrong_recipient]` |
| POST /leader | leader_exists | 409 / LEADER_EXISTS | yes (authz.denied) | `test_collab_denial_audit[leader_exists]` |
| POST /handoff | handoff_self | 422 / SELF_HANDOFF | yes (authz.denied) | `test_collab_denial_audit[handoff_self]` |
| POST /handoff | handoff_bad_recipient | 422 / BAD_RECIPIENT | yes (authz.denied) | `test_collab_denial_audit[handoff_bad_recipient]` |
| POST /handoff | handoff_pending | 409 / HANDOFF_PENDING | yes (authz.denied) | `test_collab_denial_audit[handoff_pending]` |
| POST /handoff/missing | handoff_missing | 404 / NOT_FOUND | yes (authz.denied) | `test_collab_denial_audit[handoff_missing]` |
| PUT /approvals/{a} | amend_terminal | 409 / NOT_AMENDABLE | yes (authz.denied) | `test_collab_denial_audit[amend_terminal]` |
| POST /approvals/{a}/complete | complete_pending | 409 / NOT_EXECUTING | yes (authz.denied) | `test_collab_denial_audit[complete_pending]` |
| POST /approvals/{a}/claim | claim_expired | 409 / NOT_APPROVED | yes (authz.denied) | `test_collab_denial_audit[claim_expired]` |
| POST /approvals/{a}/claim | claim_stale | 409 / NOT_APPROVED | yes (authz.denied) | `test_collab_denial_audit[claim_stale]` |
| POST /approvals/{a}/claim | claim_pending | 409 / NOT_APPROVED | yes (authz.denied) | `test_collab_denial_audit[claim_pending]` |
| POST /approvals/{a}/apply | apply_expired | 409 / NOT_APPROVED | yes (authz.denied) | `test_collab_denial_audit[apply_expired]` |
| POST /approvals/{a}/apply | apply_stale | 409 / NOT_APPROVED | yes (authz.denied) | `test_collab_denial_audit[apply_stale]` |
| POST /approvals/{a}/apply | apply_pending | 409 / NOT_APPROVED | yes (authz.denied) | `test_collab_denial_audit[apply_pending]` |
| POST /approvals/{a}/claim | claim_nonexecutor | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[claim_nonexecutor]` |
| POST /approvals/{a}/complete | complete_nonexecutor | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[complete_nonexecutor]` |
| POST /approvals/{a}/apply | apply_nonexecutor | 403 / NOT_LEADER | yes (authz.denied) | `test_collab_denial_audit[apply_nonexecutor]` |

Runs: `section4/.venv/bin/python -m pytest tests/unit/test_collab_denial_audit.py -q`: 57 passed (no xfails): 54 matrix cases plus 3 review probes (audit-store failure does not mask the denial; a 5,000-character run id still gets the plain 404; `PILOT_NOT_ENABLED` writes no row). Latest QA-agent run, Section 4 with Postgres and ownership on: 702 passed, 19 skipped (opt-in Docker, local model, live OpenRouter), 0 xfailed. Reviewed by Claude 2026-10-18; the implementing round was Codex's.

Coverage is the requested authorization/approval-state denial categories, plus leader conflict and handoff recipient/state validation. Concurrent CAS loser branches, infrastructure failures, arbitrary malformed-request validation, and staging filesystem failures are not exhaustively fault-injected by this matrix. Apply denial tests configure a staging root but never reach filesystem application. Existing chaos tests exercise concurrency separately. Denial auditing is implemented in `api.py` (`_authz_dep(audit=True)`, `_collab`) and `collab.audit_denial`.
