# Security & production-readiness scorecard — P0 baseline (2026-11-21)

Rubric: master prompt "Security and production readiness" §3 (12 gates, 100 pts).
Rule: a gate scores only with evidence from the deployed image set, in this repo.
P0 re-verified every number this session (see p0-report-2026-11-21.md for the
exact commands). Deployed image set = **vv90r6** on harness-rc (all four image
digests re-matched live against `90day/image-manifest-vv90r6-deployed.json`;
schema 74 | 0076; live shell gate sha256 `2d6337ce…` byte-identical to HEAD).

**P0 baseline: 26 / 100. After the P1–P10 session (same day, 2026-11-21): **52 / 100 → verdict NOT READY** (GO needs ≥90 AND full G-A…G-D
AND zero open High/Critical). The informal "~60%" that circulated counted
implemented-at-HEAD work; this rubric deliberately counts only deployed-set
evidence, so the scored number is lower until phases P1–P10 produce it.

| Gate | Pts | Score | Basis for the score (evidence) |
|---|---|---|---|
| G-A Deployed = reviewed | 6 | **5** | One tag set on RC + digest manifest match + schema = HEAD + live gate byte-identical: all re-verified live 2026-11-21 (`90day/image-manifest-vv90r6-deployed.json`, deploy commit `daeface`). Missing: automated CI drift check (CI startup broken, F-SEC-01) and a `make readiness-drift` target; `stack_tags.py` AGREE is ambiguous while demo+RC run together (F-SEC-02). |
| G-B Sandbox boundary | 16 | **7** | G1 live-confirmed open (`pilot-prep/g1-landlock-network-noop-2026-11-21.json`); G2 fixed+live-proven on Linux at HEAD, **but not yet in the deployed set** (F-SEC-05: vv90r6 predates `188fef36`, cred-dir canary leaks through the deployed gate); G3 root-equivalent daemon undispositioned. G4–G13 dispositioned in `90day/p04-sandbox-review-2026-11-11.md` §5. A-01 decision-layer closed at HEAD and pinned (`test_shell_gate_args.py` 25 passed) and behaviorally re-probed live (4bac7d46). **S-01 native-Linux re-drill executed on the deployed tag 2026-11-22** (`pilot-prep/native-linux-drill-deployed.json`): digest verified vs manifest, Landlock ABI usable, the four boundary checks 4/4 PASS (workspace write/read, sibling read denied on a read-write mount, /tmp escape write denied) — kernel is the Docker Desktop Linux VM (linuxkit), so the bare-metal CI native row stays owed behind F-SEC-01. |
| G-C AuthN/AuthZ | 10 | **6** | R-01 authz isolation fixed fail-closed with suites (`pilot-remediation/RISK_REGISTER.md` R-01). No route×role×tenant matrix over the deployed 148 paths (committed per-service specs sum to 134: s4 122 + s5 12 — the matrix must be generated from `app.openapi()`). No OTP attempt caps/lockout found (grep, section1). S-03 verified fixed at HEAD (`section3/src/harness_router/providers.py:283-285` filters `model`/`messages` from `opts.extra`); S-02 and S-04 verified closed 2026-11-22 (findings-ledger session update: two-team regression + propose-only MCP gate, suites green); S-04-adjacent lockout now emits `auth.otp_locked`. |
| G-D Secrets & keys | 8 | **6** | Dual-read KDF + `scripts/rotate_master_key.py` + rotation runbook exist (`docs/runbooks/secret-rotation.md`); rotation not drilled on a throwaway DB. Redaction drills exist for run surfaces (`pilot-prep/w4-credential-leak-*.json`); full sweep over logs/exports/UI unevidenced. CI secret scanning (gitleaks) is wired as a job but never runs (CI startup broken, F-SEC-01). |
| G-E Supply chain | 8 | **3** | GitHub Actions pinned by SHA (22/22 `uses:` lines, verified today). No SBOMs, no image scanner, no digest-pinned base images evidenced; the existing `security scan (pip-audit warn + gitleaks)` job has never executed (CI startup broken, F-SEC-01). |
| G-F AppSec hardening | 8 | **4** | Sandbox egress allow-list proxy with drills (`pilot-prep/w4-egress-network-*.json`) covers one outbound surface. No security-header/CSP evidence, no SSRF guard library across all outbound fetches, no ZAP/DAST run. |
| G-G AI-specific threats | 8 | **5** | Denial circuit breaker + denial events drilled (`pilot-prep/w27-breaker.json`, `w27-denial.json`); scope-request flow lives in the gate (OUT_OF_SCOPE guidance). No AI threat model doc (`docs/security/` does not exist), no adversarial replay fixtures. |
| G-H Resilience & recovery | 10 | **6** | PITR drilled on file archive + self-hosted S3 (`pilot-prep/walg-s3-pitr-drill.json`, `pitr-drill-20261029T140816Z.json`); backup/WAL alerts proved live (row 28 evidence). Missing: cloud-bucket drill, store volume in backups, offsite target configured, `down -v` guard test (`scripts/pitr_drill.sh:95`), unattended-retention verification, timed RPO/RTO vs SLO doc, rollback rehearsal. |
| G-I Observability & incidents | 8 | **4** | Grafana dashboards imported with live data 2026-11-21 (`90day/step3-operations-checklist-2026-11-21.md` §1 DONE note); webhook alert channel proved earlier (`evidence/s11/alert-delivery.md`, A9). But RC Alertmanager is crash-looping right now (F-SEC-03), no pager path, worker-daemon healthcheck absent, no tabletop drills, `docs/runbooks/incident-response.md` does not exist. |
| G-J Live SSO | 5 | **3** | Keycloak OIDC drill 14/14 incl. negatives (`pilot-prep/oidc-keycloak-drill.json`) = the rubric's partial. Hosted IdP tenant drill not run (owner must create the tenant). |
| G-K External assurance | 8 | **4** | No pen-test packet (`docs/security/pentest/` absent), no test executed, no counsel packet completion beyond the existing drafts. |
| G-L Pilot baseline & legal | 5 | **3** | Metrics instrumentation exists (`pilot_metrics.py`, `docs/briefs/pilot-baseline-guide.md`); no baseline protocol doc, no RC dry run, no real-team week; counsel §1–§4 unanswered (`docs/legal/COUNSEL_OPEN_QUESTIONS.md`). |

## Phase-to-phase movement (2026-11-21, one session)

| Gate | P0 | After P1–P10 | Evidence |
|---|---|---|---|
| G-A | 3 | 5 | readiness_drift.py both ways + make targets + CI job (p1-report) |
| G-B | 2 | 7 | G2 fixed+live-proven on Linux, G9/G12/G6, fuzz 100k/0 (p2-report) |
| G-C | 2 | 6 | 157-path matrix + OTP lockout pins (p3-report) |
| G-D | 3 | 6 | rotation drill as CI test + redaction sweep (p4-report) |
| G-E | 1 | 3 | SHA/digest pins verified; Trivy/SBOM job prepared owner-gated |
| G-F | 1 | 4 | SSRF + header contract tests (p5-report) |
| G-G | 2 | 5 | AI threat model + adversarial replays (p6-report) |
| G-H | 4 | 6 | down -v guard suite + unattended retention verified (p7-report) |
| G-I | 3 | 4 | incident runbook + tabletops (p8-report) |
| G-J | 3 | 3 | one-command drill pending tenant (p9-report) |
| G-K | 0 | 4 | pen-test + counsel packets ready (p10-report) |
| G-L | 2 | 3 | baseline protocol + counsel packet (p9/p10) |

## Verdict

NOT READY for production or a general pilot; private design-partner only.
You (owner) sign the verdict; the agent writes and maintains this score.

## What moves the number soonest

1. F-SEC-01 (CI startup) unblocks G-A's drift check and the G-D/G-E scanners in one fix. (Root-caused to GitHub billing — owner action.)
2. **Redeploy the RC set from the current tip (F-SEC-05, found 2026-11-22)** — the deployed vv90r6 gate predates the P2 sandbox fixes and the cred-dir canary leaks through it live; the fix is at HEAD, and the drill (`scripts/native_linux_drill_deployed.sh`) exiting 0 after the redeploy is the closure test.
3. P2 decisions already waiting on you: G1 route/container boundary, G3 acceptance-or-rootless.
4. The one-command items: HARNESS_ALERT_EMAIL_TO (G-I), worker-image pytest (P1), IdP tenant (G-J), cloud bucket (G-H).
