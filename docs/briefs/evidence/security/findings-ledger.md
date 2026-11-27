# Open-findings ledger — P0 (2026-11-21)

One ledger merging: S-01…S-04 (startup audit), R-03 residuals + R-02d, G1–G14
(sandbox review), A-01 deployed-tag items, capability-matrix partials (26, 31,
51–58, 61), STATUS known limits, PROJECT-STATE §6, and P0 session findings.
Status vocabulary: OPEN / PARTIAL / FIXED (needs redeploy) / RISK-ACCEPTED
(owner name+date+scope+expiry) / OUT-OF-SCOPE (written reason). Severity uses
CVSS-style reasoning in one line. Evidence paths are repo-relative.

## P0 session findings (new today)

| ID | Sev | Finding | Status | Owner | Evidence / next action |
|---|---|---|---|---|---|
| F-SEC-01 | High (availability of all security CI: scanners, drift check, native drill blocked) | CI startup broken: the four startup jobs (determine-touch, static gates, vitest+tsc, security scan) fail with **zero steps** on runs 44924965887 / 44924500849 / 44921981769 / 44918292744; persists after b9890e1's fix attempt; red since 972e7b6 (CI-minutes diet). | OPEN | agent | `.github/workflows/ci.yml`; diagnose the startup-level break (likely invalid workflow-level expression/permission), land a minimal fix; P1 depends on it. |
| F-SEC-02 | Medium | `scripts/ops/stack_tags.py` service-name dict collides when demo+RC stacks run concurrently: demo containers shadow RC ones, so "AGREE (vw24b1)" describes the demo while RC runs vv90r6. Read today while both stacks were up. | OPEN | agent | Key the report by compose project (container-name prefix), not service name; then the G-A drift check can trust it. |
| F-SEC-03 | Medium (alerting down on RC) | `harness-rc-alertmanager-1` crash-looping since boot: "set HARNESS_ALERT_EMAIL_TO" (refuses to start without an email target). Documented as by-design in step3-operations-checklist §1, but it leaves RC with no alert path = G-I gap live. | OPEN | owner (one env value) | Set HARNESS_ALERT_EMAIL_TO in the RC env; agent verifies alert fires end-to-end (P8). |
| F-SEC-04 | Medium (drift-blindness) | A-01 behavioral re-probe of the deployed gate did not execute this session: Docker daemon stopped mid-P0. Byte-identity (sha256 2d6337ce…) was re-probed live; the four A-01 deployed-tag negatives remain behaviorally unverified on vv90r6. | ~~OPEN~~ **CLOSED** (4bac7d46) | agent (needs Docker up) | Re-run the a01 matrix probe in harness-rc-worker-daemon-1 when the daemon returns; then A-01 items 3–6/9–10 downgrade from "assumed open on deployed" to measured. |
| F-SEC-05 | High (deployed set missing two P2 sandbox fixes; canary leak live-confirmed) | The vv90r6 set predates the P2 sandbox commit `188fef36`: deployed gate sha `2d6337ce…` is the pre-P2 gate in BOTH `harness-backend:vv90r6` and `harness-worker-daemon:vv90r6`; deployed `confinement.py` has zero `CREDENTIAL_DENIES` and the gate has no `_sanitize_own_environment`. Native-Linux re-drill on the deployed backend image: the `/etc/ssh` canary **read in full through the deployed gate** (exitCode 0, "CANARY" in output) while an unconfined control read proves the file was readable — the leak is the missing deny, not a mount artifact. G2's "fixed + live-proven" is true at HEAD only. | OPEN (fix at HEAD; reaches the deployed set only on redeploy) | owner (redeploy runbook; no agent deploys) | Evidence: `pilot-prep/native-linux-drill-deployed.json` (result pass-with-drift-finding), script `scripts/native_linux_drill_deployed.sh`. Redeploy the RC set from the current tip (the existing runbook), then re-run the drill — its exit 0 is the closure test. The S-01 core (deny-by-default boundary) passes 4/4 on the deployed tag, so this is a missing-carveout finding, not a boundary failure. |
| F-SEC-06 (2026-11-22, **CLOSED 2026-11-23**) | High at discovery (test-semantics drift — **no cross-tenant leak proven**; availability/semantics questions open) | PG-gated authz matrix test is red at HEAD when unskipped: `section1/tests/test_authz_matrix.py::test_matrix_covers_every_route_and_pins_the_cells` (1 failed / 1354 passed, first surfaced by X-02 `make test-all` — the flag-gated suites had never run routinely). Two classes: **(1)** `/api/v1/orgs/{org_id}/…` (execution-control, history, invites, members) return **401 to sessioned principals** though they use the same `get_principal` dependency as passing routes (`orgs.py:345+`) — fixture-vs-route drift or wrong status semantics; **(2)** foreign principals get **200** on alpha-scoped list routes (`/agents`, `/notifications`) — verified team-scoped in the handler (`agents.py:287` `WHERE team_id=:t`): scoped-query 200+empty design, not a cross-tenant read; the test's "no 2xx for foreign" pin is stale against that design. | **CLOSED** (2026-11-23) | — | Root cause + fix: `docs/briefs/evidence/10x/fsec06-authz-matrix-2026-11-23.md`. **Class 1 was probe-token poisoning**: the walk called `DELETE /api/v1/orgs/me/identity` — self-service account deletion that deactivates the caller and kills every session/seat (`orgs.py:466-500`) — with each actor's REAL token, so every later org route answered 401 for a principal the walk had just deleted (the session layer working correctly). Fixed by probing that route with throwaway twins (one per actor). **Class 2 was stale pins**: the listed reads (agents, notifications, sessions, workspaces, orgs) are team/user-scoped own-data by code, and class 1 had masked it; the matrix now pins own-data 200+empty and adds an `own_data_write` class for `read-all`. `whoami` is pinned as a service-identity door (user bearer 401 by design). No product code changed. Matrix green unskipped: 3/3 PG, 3/3 sqlite; section1 full PG 1351 passed / 1 failed / 3 errors, all four Docker-container-dependent (`harness-db-1` absent), 1355 tests total in both runs. |

## Startup audit S-01…S-04

| ID | Sev | Finding | Status | Owner | Evidence / next action |
|---|---|---|---|---|---|
| S-01 | High | forbidden_paths read-confidentiality bypass via allow-listed interpreters. Closed for **modify** at OS level (bind RO mounts + Seatbelt deny + gate refuse + post-run hash). Read half = separate hiddenPaths concept, not built. | PARTIAL (modify closed; boundary re-drilled on the deployed tag 2026-11-22 — 4/4, see session update; read = G2 + hiddenPaths) | agent | startup-audit-2026-10-21.md §S-01 + status update; hiddenPaths remains the unbuilt read half (owner policy); CI-native bare-metal drill row follows F-SEC-01 billing. |
| S-02 | Med | Any writer mutates process-wide role→model default. | **CLOSED** (verified 2026-11-22 at HEAD `d98bf832`): `/models/config` and `/models/prefs` pass `team_id=principal.teamId` (`section3/src/harness_router/api.py:578`, `:623-624`) — the process-wide `role_models` dict is unreachable from the API; only the in-process per-team override is written. | — | Two-team regression test the audit asked for exists and passes: `section3/tests/unit/test_s02_s03_model_integrity.py::test_team_config_does_not_change_another_teams_model` (team A's override leaves team B and the global default untouched); 2/2 passed. |
| S-03 | Med | `opts.extra` overrode the model sent; response/cost billed the requested model. | FIXED (verified at HEAD: providers.py:283-285 filters `model`/`messages` from extra) | — | Regression test to confirm exists in P3; matrix pins it. |
| S-04 | Med | MCP start_run auto-approves plans. | **CLOSED** (verified 2026-11-22 at HEAD `d98bf832`): `_tool_start_run` is propose-only — it calls `POST /plans`, returns `state: AWAITING_PLAN_APPROVAL, approvalRequired: true` and never touches approve/start (`mcp/src/harness_mcp/tools.py:40-71`). Stricter than the planned flag-gate: no auto-approve path exists at all. | — | Pinned by `mcp/tests/test_tools.py::test_start_run_stops_at_human_approval_gate` (asserts the only upstream URL is `/plans` and no `/approve`/`/start` is ever called); MCP suite 18/18 passed. |

## Sandbox review G1–G14 (p04-sandbox-review-2026-11-11.md §5)

| ID | Sev | Finding | Status | Owner | Next action |
|---|---|---|---|---|---|
| G1 | High | Landlock enforces no network boundary (ABI v1); live-confirmed 2026-11-21 (g1-landlock-network-noop json). | OPEN (live-confirmed) | owner decision → agent | P2: container/internal-network boundary or Landlock ABI≥4 or named acceptance. |
| G2 | High | Process-mode read confidentiality: HOME grant + /proc environ; live-confirmed macOS half (canary read in full); Linux /proc half pending. | OPEN (live-confirmed macOS) | owner (BYO host policy) → agent | P2: cred-dir denies, env strip, /proc block, BYO policy doc; Linux negative drill. |
| G3 | High | worker-daemon root-equivalent via host main Docker socket. | OPEN | owner (accept vs rootless/separate daemon) | P2: design + named acceptance with single-purpose-host rule. |
| G4 | Med | Shared egress network: siblings reachable; proxy web port 3129 reachable cross-network. | OPEN | agent | P2 cheap fix: per-task networks / bind web port. |
| G5 | Med | DockerSandboxRunner bind paths assume runner fs == daemon host fs. | OPEN (undrilled) | agent | P2 disposition with the dedicated-worker design review. |
| G6 | Med | Forbidden-path masks launch-time only (Docker runner; no per-exec re-mask). | OPEN | agent | P2: per-exec re-mask or documented acceptance. |
| G7 | Med | BYO attestation self-reported (policyDigest format-checked, never compared). | OPEN | owner decision | P2: pin expected gate digest or accept trusted-host model. |
| G8 | Med | Daemon control plane plaintext HTTP on shared network (token + provider key in clear). | OPEN | owner decision | P2: mTLS / isolated control network / named acceptance. |
| G9 | Low-Med | Egress proxy: no tunnel caps, no header deadline, denials unlogged, no resolved-IP validation. | OPEN | agent | P2 cheap fix: caps + denial logging. |
| G10 | Low | Landlock HOME root re-exposes siblings if SANDBOX_STORAGE_ROOT moved under HOME. | OPEN | agent | P2: document constraint or carve out storage root. |
| G11 | Low | Landlock does not mediate unix sockets; /run readable → docker.sock usable if co-located. | OPEN (deployment constraint) | agent | P2: rule doc (never co-locate socket with Landlock-only). |
| G12 | Low | worker-daemon has no compose healthcheck. | OPEN | agent | P2 cheap fix: /version healthcheck. |
| G13 | Low | Docker runner exec cancellation budget-bounded (≤300 s), not immediate. | OPEN | agent | P2: accept or exec-kill on cancel. |
| G14 | Info | Landlock-in-container usability runtime-dependent; mandatory probe on intended pilot host. | OPEN (drill gate) | owner (host) → agent | P2: run probe on intended host. |

## A-01 deployed-tag items (a01-dispositions-2026-11-21.md)

| ID | Sev | Finding | Status | Owner | Next action |
|---|---|---|---|---|---|
| A-01/3–6, 9–10 | High (decision-layer bypasses on the then-deployed gate) | Deployed gate vw24b1 predates argument checks; negatives 3–6 executed, 9–10 assumed open by vintage. vv90r6 (current RC) carries the checks — gate byte-identity to HEAD re-verified live 2026-11-21 — but the behavioral re-probe is pending (F-SEC-04). | FIXED (needs redeploy already done) — behavioral confirmation pending | agent | F-SEC-04 re-probe closes the loop; A-06-style redeploy no longer needed since vv90r6 postdates e24b830. |

## Pilot remediation residuals

| ID | Sev | Finding | Status | Owner | Next action |
|---|---|---|---|---|---|
| R-02d | Med-High | Master-key rotation implemented (rotate_master_key.py, dual-read KDF) but not drilled end-to-end on a throwaway DB. | PARTIAL | agent | P4 drill + runbook update. |
| R-03 | PARTIAL | R-03a/b/d/e/f fixed; R-03c narrowed (egress proxy) but Landlock path has no network hooks (= G1); not drilled against live Docker daemon. | PARTIAL | see G1 | P2. |
| R-04 | see row | Prompt-injection boundaries: recorded-not-enforced items remain (assignment scope not bound to child engine; repeated-stop re-emits paused event; synthesis unbuilt). | PARTIAL | agent | P6 threat model + replay fixtures. |

## Capability-matrix partials / blocked (rows cited by the rubric)

| Row | State | Missing for the rubric |
|---|---|---|
| 03 | blocked | Real pilot cohort + measured baseline (G-L owner part). |
| 26 | implemented | `--real` smoke needs OpenRouter key (owner). |
| 28 | verified (limits) | Pager integration; one-host file storage. |
| 31 | partial | SDK shell lacks Section 5 allow-list; residency deployment-wide. |
| 37 | verified (cap 8) | Live SSO on hosted IdP (G-J). |
| 51 | partial | Physical residency deployment-wide (G-K/G-L packet). |
| 52 | partial (cap 8) | Deployment encryption/roles unverified; independent pen test (G-K). |
| 53 | partial (cap 8) | Cloud-bucket restore; deployed failure drill (G-H). |
| 55 | partial | Row flip pending only with live-import evidence — DONE 2026-11-21 per step3 checklist; re-check and flip in P8 with the Grafana session evidence. |
| 56 | partial | Screen reader; pages beyond two; zoom/visual; English-only (not scored by this rubric). |
| 57 | partial | In-flight spend estimate-based (G-H/G-I adjacent). |
| 58 | partial (cap 8) | Bucket-lifecycle expiry under holds; counsel review (G-L). |
| 61 | blocked | Cross-domain verdict; this scorecard is the security lane's input. |

## STATUS known limits + PROJECT-STATE §6 roll-up (unscored here, tracked)

Alerts one email + one webhook, no pager; PITR not cloud-bucket; no measured
baseline (03); operator-present rules manual; live SSO unproven (37); no pen
test; no screen reader; `--real` unrun; GitHub OAuth token keeps write scope
once opted in (49); audit anchor file only as safe as its storage (11);
session-state change ≤30 s sweep (40); timed-out engine call's tool calls
unrecorded (44); store volume not in backup scripts (P7); offsite backup not
configured (owner).

## Session update (2026-11-21, after P1–P10)

| ID | Change |
|---|---|
| F-SEC-01 | ROOT-CAUSED: GitHub billing refusal ("recent account payments have failed or your spending limit needs to be increased" — check-run annotations). Owner action; the code was never the bug. |
| F-SEC-02 | **CLOSED** (275aef0e): stack_tags per-project agreement; 18 tests + live rc+demo output verified. |
| F-SEC-03 | Still OPEN (owner env value HARNESS_ALERT_EMAIL_TO); re-flagged by the tabletop as the top owner action. |
| F-SEC-04 | **CLOSED** (4bac7d46): A-01 behavioral re-probe on live vv90r6 — c3/c4/c5/c6 DENIED, control executes. |
| G2 | **FIXED at HEAD + live-proven on Linux**: credential-dir denies (Seatbelt+Landlock) + gate self-sanitization; token probe BYPASS-LEAKED → gate-env-clean; /etc/ssh canary Permission denied. BYO policy decision doc prepared. **NOT yet in the deployed set** — F-SEC-05: vv90r6 predates `188fef36`, cred-dir canary leaks through the deployed gate (live, 2026-11-22). |
| G1, G3 | Decision docs prepared (docs/security/byo-host-policy.md, worker-daemon-isolation-design.md); owner decides. |
| G9 | **FIXED**: proxy caps + denial sampling. G12 **FIXED**: /version healthcheck. G6 **FIXED**: post-launch forbidden-path guard. |
| A-01/3–6,9–10 | **CLOSED on deployed tag** (byte-identity + behavioral probe). |
| New: OTP lockout audit event | verify_otp consumes the row on cap but emits no audit event — recorded; P4 follow-up. |
| Fuzz exit check | **DONE**: 100,000 cases, 0 violations (shell-gate-fuzz-drill.json). |
| S-03 | verified fixed at HEAD (providers.py filters model/messages). S-02/S-04 verification still owed. |
| down -v hazard | guard suite landed (test_pitr_drill_volume_guard.py, 5 tests). |
| Retention | verified unattended on live RC (partitions through 2027_01). |

## Session update (2026-11-22 — FUTURE PLAN 2.0 "next tasks" lane)

| ID | Change |
|---|---|
| S-02 | **CLOSED by verification** (row above): fix confirmed at HEAD; the audit's two-team regression test exists and passes (2/2 in `section3`). |
| S-04 | **CLOSED by verification** (row above): propose-only MCP gate confirmed; suite 18/18. |
| OTP lockout audit event | **IMPLEMENTED**: `verify_otp` now emits `auth.otp_locked` (new action in `APPEND_ONLY_ACTIONS`, helper `emit_auth_otp_locked` in `audit_emit.py`) on both cap refusals — the already-at-cap LOCKED path (reason `already_at_cap`, attempts read before the consume commit) and the wrong guess that reaches the cap (reason `cap_reached`). Emission happens after the consume commit so a failed audit write can never un-consume the row; `_scrub` keeps the code out and no code value is passed at all. Tests: `test_otp_atomic.py` 5/5 (two new lockout-audit cases + a no-event case for wrong-under-cap / rival-consumed / success), `test_audit_emission.py` 13/13, auth/ratelimit/authz-matrix 22/22, sso/session11 30 passed + 9 live-PG skips (environmental). |
| S-01 (native re-drill, G-B pending item) | **EXECUTED on the deployed tag** (`scripts/native_linux_drill_deployed.sh` → `pilot-prep/native-linux-drill-deployed.json`): image digest re-verified against the vv90r6 manifest before running; Landlock ABI usable in-container; the four drill checks all PASS on the deployed gate (workspace write through the gate + content verified on the volume, workspace read, sibling read denied — sibling mounted read-WRITE so only Landlock could stop it, /tmp escape write denied). Honest caveat: kernel is the Docker Desktop Linux VM (linuxkit 6.12.76), not bare metal — the CI native (ubuntu) row stays owed until F-SEC-01 billing is fixed; canaries ride docker volumes, never virtiofs bind mounts (Landlock cannot mediate virtiofs). |
| F-SEC-05 (new) | **DISCOVERED by the same drill** (row in the P0 findings table): the deployed vv90r6 set predates the P2 sandbox fix — the G2 credential-dir canary was read in full through the deployed gate. The drill script exits 1 on this drift so automation flags the redeploy need; the redeploy itself is the owner's runbook action. |

## Session update (2026-11-22 — 10X Wave A: G1/G3 decided; first gitleaks run dispositioned)

| ID | Change |
|---|---|
| G1 | **DISPOSITIONED by owner decision** (`docs/decisions/2026-11-22-g1-g3-sandbox-dispositions.md`): policy-layer network boundary accepted for the pilot — macOS Seatbelt denies network; Linux runs keep the per-run internal network + DNS kill + allow-list CONNECT proxy. No Landlock network claim may appear anywhere. Revisit on a Landlock ABI with network hooks or untrusted Linux egress. |
| G3 | **RISK-ACCEPTED by owner decision** (same document): pilot daemon on a single-purpose host, stays an acknowledged High; rootless/isolated daemon still required before production (10.14 gate unchanged). |
| gitleaks (G-E) | **FIRST EXECUTION** (`ci-local/2026-11-22T0451Z-ab29e8c9`, redacted rerun at v8.30.1, 871 commits): 7 findings → all 7 inspected at source the same day and confirmed synthetic redaction-test fixtures (literals + source:line recorded in `.gitleaks.toml` comments). Allowlist extended; `gitleaks detect` exits 0. The scanner's blocking path is proven end-to-end locally. |
| X-01 interim (ci-local) | Full local CI matrix green: static-gates 6/6; pytest green everywhere (s1 1293/62 skip, s4 2402/60 skip, s5 544/12 skip, s2 61, s3 149, s6 123, s8 104, s10 96, s11 60, mcp 18, language 367 — zero failures); frontend tsc+vitest ok; TS dist current; readiness manifest coherent. Evidence: `quality/ci-local/2026-11-22T0451Z-ab29e8c9/summary.md`. pip-audit WARN 18 (WARN posture parity). |

## Session update (2026-11-22, later — 10X Wave A: X-02 executed, F-SEC-06 surfaced)

| ID | Change |
|---|---|
| F-SEC-06 (new) | **DISCOVERED by the first routine run of the flag-gated suites** (X-02 `make test-all`): the PG-gated authz matrix test is red at HEAD (row above). No cross-tenant leak proven — the foreign-principal 200s are scoped-query design; the org-route 401s need the security lead. |
| X-02 (`make test-all`) | **Executed partially, honestly**: pg-probe-integration PASS, section1 real-PG suite 1354 passed / 1 failed (F-SEC-06) — `make` halted there by design (first failure stops). Docker P0, promtool and landlock steps run on the rerun after F-SEC-06 is dispositioned. The target did exactly its job: a never-routinely-run gated test was red on first contact. |

## Session update (2026-11-23 — F-SEC-06 closed; X-02 unblocked)

| ID | Change |
|---|---|
| F-SEC-06 | **CLOSED** (same-day root cause, no product change). Reproduced on a throwaway PG 18 at 5499: 41 violations. **41 → 0** by fixing the test's own semantics. Class 1: `DELETE /orgs/me/identity` deactivates the caller and kills all their sessions/seats, so probing it with the probe actors deleted them mid-walk — every later org route's 401 was the session layer being correct. Fixed with per-actor throwaway twins. Class 2: the foreign-200 reads are `WHERE team_id=:t` / `WHERE user_id=:u` own-data queries (agents, notifications×3, sessions×2, workspaces×3, orgs); pinned as `own_data`, plus `own_data_write` for `notifications/read-all` and a `service_door` class for `orgs/service/whoami`. Matrix: **3/3 PG + 3/3 sqlite**. Section1 full PG: **1351 passed / 1 failed / 3 errors** — all four need `docker exec harness-db-1` and no Docker daemon is running here; test-count math (1355 both runs) proves the only delta vs X-02 is the matrix. |
| X-02 | **Unblocked**: the PG-gated suite no longer halts on an authz failure. The Docker P0 / promtool / landlock steps now run whenever their prerequisites exist; this session's Docker daemon is absent, so they are honestly skipped, not failed. |

## Sorted by severity (what to fix first)

1. F-SEC-01 (CI startup) — unblocks scanners + drift check + native drill. (Root-caused to GitHub billing — owner action.)
2. **F-SEC-05 (new, 2026-11-22): redeploy the RC set from the current tip** — the deployed vv90r6 gate predates the P2 sandbox fixes; cred-dir canary leaks through the deployed gate. Fix exists at HEAD; the drill script's exit 0 after redeploy is the closure test.
3. G1, G3 — **DONE 2026-11-22**: both dispositioned by owner decision (`docs/decisions/2026-11-22-g1-g3-sandbox-dispositions.md`).
4. ~~F-SEC-06 — PG-gated authz matrix red at HEAD~~ **CLOSED 2026-11-23**: root cause was probe-token poisoning by the self-deleting `/orgs/me/identity` route (plus stale own-data pins); test semantics re-pinned, matrix green unskipped, no product change. Evidence: `10x/fsec06-authz-matrix-2026-11-23.md`.
5. S-02/S-04 verification + OTP caps + authz matrix build (G-C) — **DONE with the F-SEC-06 caveat**: S-02/S-04 verified closed 2026-11-22 (session update above); OTP lockout pins landed in P3 and the lockout now emits `auth.otp_locked`; the 157-path matrix landed in P3 but its routine (unskipped) execution is exactly what F-SEC-06 surfaced.
6. F-SEC-02/F-SEC-03 — F-SEC-02 closed; F-SEC-03 owner one-liner (HARNESS_ALERT_EMAIL_TO).
7. Everything routed to P4–P10 as phased.
