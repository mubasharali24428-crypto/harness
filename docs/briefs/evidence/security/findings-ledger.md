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
| F-SEC-04 | Medium (drift-blindness) | A-01 behavioral re-probe of the deployed gate did not execute this session: Docker daemon stopped mid-P0. Byte-identity (sha256 2d6337ce…) was re-probed live; the four A-01 deployed-tag negatives remain behaviorally unverified on vv90r6. | OPEN | agent (needs Docker up) | Re-run the a01 matrix probe in harness-rc-worker-daemon-1 when the daemon returns; then A-01 items 3–6/9–10 downgrade from "assumed open on deployed" to measured. |

## Startup audit S-01…S-04

| ID | Sev | Finding | Status | Owner | Evidence / next action |
|---|---|---|---|---|---|
| S-01 | High | forbidden_paths read-confidentiality bypass via allow-listed interpreters. Closed for **modify** at OS level (bind RO mounts + Seatbelt deny + gate refuse + post-run hash). Read half = separate hiddenPaths concept, not built. | PARTIAL (modify closed; read = G2) | agent | startup-audit-2026-10-21.md §S-01 + status update; P2 G2 closure + native-Linux re-drill on deployed tag. |
| S-02 | Med | Any writer mutates process-wide role→model default. | OPEN (fix not re-located at HEAD this session; grep empty in s1/s4) | agent | Re-verify whether fixed; if not, P3: per-team role models + two-team regression test. |
| S-03 | Med | `opts.extra` overrode the model sent; response/cost billed the requested model. | FIXED (verified at HEAD: providers.py:283-285 filters `model`/`messages` from extra) | — | Regression test to confirm exists in P3; matrix pins it. |
| S-04 | Med | MCP start_run auto-approves plans. | OPEN (unverified this session) | agent | P3: test MCP start leaves run AWAITING_PLAN_APPROVAL; flag-gated only. |

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

## Sorted by severity (what to fix first)

1. F-SEC-01 (CI startup) — unblocks scanners + drift check + native drill.
2. G1, G2, G3 (+ A-01 behavioral confirmation, F-SEC-04) — the sandbox core.
3. S-02/S-04 verification + OTP caps + authz matrix build (G-C).
4. F-SEC-02/F-SEC-03 — cheap, same-session fixes with owner's one-liners.
5. Everything routed to P4–P10 as phased.
