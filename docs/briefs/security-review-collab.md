# Independent collaboration security review — 2026-10-18

Scope: read-only review of collab.py, api.py, staging.py, retention.py, service.py collaboration integration and migration 0035. No production fixes. This is a source review plus targeted filesystem reproduction, not a penetration test or proof that races are absent. Shared checkout can change after this snapshot.

## High — CONFIRMED: approval digest does not bind all staged bytes

**Status: fixed 2026-10-18.** Staged-group digests now include a hash of every path's full old/new bytes (`staging._groups`, `collab.proposal_digest(content=...)`); regression tests in `section4/tests/unit/test_staging_digest_binding.py`; the repro below now stops at its first assertion because the digest changes. The apply step also reads each withheld file once and both checks the digest on, and lands, that same snapshot (`staging.apply_group`; test `test_bytes_swapped_after_the_check_are_not_the_bytes_that_land`), so a writer racing the check no longer changes what lands. Residual: non-staged, requester-supplied approvals keep the old display-only digest.

Locations: `section4/src/harness_orchestrator/staging.py:125` (binary placeholder), `:180` (diff truncation), `:248` (digest comparison), `:271` (copy).

Scenario: after reviewing a staged binary modification, replace its withheld bytes with different binary bytes. The displayed diff remains “Binary file … differs”; the digest remains identical. Likewise, changing text beyond the first 200,000 diff characters leaves the digest unchanged. Applying the original approval lands the replacement bytes. An actor needs access to withheld files or another process that changes them; this reproduction does NOT establish access from the confined worker. Nevertheless the claimed tamper detection is false.

Evidence: `section4/.venv/bin/python docs/briefs/evidence/security/repro_staging_digest.py` exited 0:

```
CONFIRMED binary: changed bytes retain digest and are applied
CONFIRMED truncated_text: changed bytes retain digest and are applied
```

The standalone script uses temporary directories, calls the actual staging functions, asserts unchanged digests and verifies the replacement bytes land. No models, credentials or production workspaces are involved.

Suggested fix: bind approval to a canonical manifest containing full old/new byte hashes, entry types, paths and symlink targets. Truncate only the display. Apply the exact immutable snapshot hashed, with serialization against staging and run execution. Add binary and truncated-text regression cases. Coordinate any contract changes separately.

## Route-by-route examination

Every one of the 15 routes was examined. All call `_pilot_gate`; all use authenticated RunReader/RunWriter dependencies which check team ownership. No missing gate was found. Paths below are relative to `/runs/{runId}`.

| Method/path | Additional control examined |
|---|---|
| POST join | Reader; viewer becomes observer; durable run required |
| GET participants | Reader; same-team roster |
| POST leader | Writer plus engineer/admin; unique leader index |
| POST redirect | Writer plus participant; leader for scope/permission change |
| GET collab-history | Reader; ordered cursor and limit clamped to 500 |
| POST handoff | Writer plus current leader; joined engineer/admin target |
| POST handoff/{handoffId} | Writer plus named recipient; pending CAS; proposer still leader |
| POST approvals | Writer plus participant; action/path/content bounds |
| GET approvals | Reader; scoped run lookup |
| PUT approvals/{approvalId} | Writer plus requester; pending/approved only; resets digest/decision |
| POST approvals/{approvalId}/decision | Writer plus leader; self-approval denied by default; digest/TTL CAS |
| POST approvals/{approvalId}/claim | Writer plus requester/current leader; approved/digest/TTL CAS |
| POST approvals/{approvalId}/complete | Writer plus requester/current leader; executing CAS |
| POST staged/approvals | Writer; server-derived staged groups |
| POST approvals/{approvalId}/apply | Writer; claim then filesystem validation then completion |

## Rule coverage and limits

- **Teams/roles:** examined API dependencies and authz.py as supporting code. Foreign/unknown runs use a generic 404; viewer writes fail role checking. Internal collab functions rely on route ownership guards. No cross-team or viewer-write bypass reproduced. Leadership checks are separate reads; concurrent handoff/decision interleavings were not stress-tested.
- **Replay/digest/TOCTOU:** examined decision, amendment, claim and complete SQL, migration unique indexes and service engine/completion gates. CAS prevents straightforward repeated claims; digest weakness above is confirmed. Claim, filesystem apply and completion are separate transactions; crash recovery and simultaneous workspace mutation were not dynamically verified. Do not interpret CAS as filesystem atomicity.
- **Pilot gate:** checked all routes individually; unset/empty allowlist is open by design, so pilot configuration must supply a nonempty list.
- **Guidance injection:** examined framing, whitespace normalization, literal tag neutralization, delivery cursor and service invocation. Permission changes are not injected. No separate privilege bypass established; text framing is not a security boundary.
- **Traversal/symlinks:** examined validate_paths, staging `_inside`, apply path/link checks, retention `_SAFE_ID` and `_rm`. Ordinary absolute/parent traversal and direct escaping symlinks have checks. Concurrent symlink swaps and filesystem deletion races were not reproduced; checks and filesystem operations are separate. Staging relies on callers for trusted run IDs/root directories.
- **SQL:** collab uses bound values. Retention's formatted SQL uses fixed internal enum tuples; no user-controlled SQL interpolation found. Migration constraints and indexes examined.
- **Bounds:** redirect 4,000 characters, reason 500, paths 100 × 512, API diff 200,000, history 500; baseline cap 200 MiB. Roster/approval list and workspace walks are not paginated/bounded in aggregate; resource exhaustion was not tested. Amendment service relies on API text limits.
- **Audit:** sensitive joins, leadership, redirects, handoffs, decisions, claims and completions append audit rows. **Update 2026-10-18 (denial audit implemented, reviewed):** every refused request on the 15 collab routes by an authenticated principal now leaves one `authz.denied` row (or its existing specific row) with the stable error code and `METHOD route` (`collab.audit_denial`, `api._authz_dep(audit=True)`); 57 tests in `test_collab_denial_audit.py` pin it, including that an audit-store failure or a 5,000-character run id never changes the denial response. Not audited: pydantic body-validation failures, `PILOT_NOT_ENABLED`, and non-denial 409/503 responses. **New Low finding (SUSPECTED, reasoned from code):** any authenticated user can append one audit row per request by probing unknown run ids (`target_id` is the attacker-supplied id), and no job purges the audit log, so a hostile or buggy client grows it without bound; the only limit is the (documented) absence of request rate limiting. Suggested: rate-limit denial rows per principal or aggregate repeats.
- **Errors:** examined stable collab errors and generic ownership 404 mapping. Staging error strings include path names for authorized callers. No cross-team error disclosure reproduced; unexpected OS/database exception handling was not fault-injected.
- **Retention/service:** reviewed finished/latest-event/open-approval eligibility, default dry-run, audit preservation, engine pending gate, post-call staging and completion gate. Concurrent retention versus resume/approval creation remains unverified.

All requested routes and review categories above were examined; “no reproduced bypass” does not mean a clean security certification. The explicitly excluded known issues are not reported as new findings. The confirmed digest binding defect is now fixed (see the status line under the finding); the denied-action audit gaps are pinned by strict xfail tests in `section4/tests/unit/test_collab_denial_audit.py` and `collab-denial-audit-matrix.md`.
