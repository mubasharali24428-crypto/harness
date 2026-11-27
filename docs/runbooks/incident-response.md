# Runbook — Incident response (P8, G-I)

> Status: written 2026-11-21 (security master prompt P8). On-call decided
> 2026-10-31 (see alerting.md §6b: primary = Mubashar Ali, no escalation
> chain during the pilot). This runbook covers what to do in the first hour
> of each severity, comms, evidence preservation, and disclosure.

## 1. Severity levels

| Sev | Definition (pick the highest that applies) | Examples |
|---|---|---|
| **S1 — Critical** | isolation violation, secret leak, data loss/corruption, auth bypass; pilot-affecting | `HarnessIsolationViolation` firing; a provider key found in a log/export; forbidden-path WRITE succeeded; DB restore needed |
| **S2 — Major** | a surface is down or wrong for a team, no data at risk | one backend 5xx-looping (`HarnessServiceDown` >30m), webhook delivery dead, SSO login broken |
| **S3 — Minor** | degraded, workaround exists | event-delivery duplicates, alert noise, one drill failed |

## 2. First hour (S1)

1. **Stop the bleeding:** pause new runs (`workspace_gate` pause / pilot.md
   stop conditions), cancel affected runs (`POST /runs/{id}/cancel`), do NOT
   `docker compose down -v` anything (the live-volume law).
2. **Preserve evidence BEFORE restarting anything:**
   `docker logs <container> > /tmp/evidence-<c>.log 2>&1` for every involved
   container; snapshot `agent_events` + `audit_log` rows for the run window
   (`GET /api/v1/audit/export`); keep the sandbox workspace directory of the
   affected run (mounted under `harness_runs`); note image tags + digests
   (`python3 scripts/ops/stack_tags.py`, `docker image inspect <ref> --format {{.Id}}`).
3. **Rotate what could have leaked:** provider keys (per-team, via the keys
   API), runner tokens (`POST /api/v1/team-runners/{id}/rotate`), session
   revocation if auth is involved (`auth_sessions` delete for the principal),
   master key ONLY with the rotation runbook (secret-rotation.md) — never ad hoc.
4. **Page** (S1 = the `page` severity path): owner's phone-push
   (`HARNESS_ALERT_WEBHOOK_URL`) + email. If the alert didn't fire, post it
   manually to Alertmanager (alerting.md §7 curl) so the timeline is recorded.
5. **Verify isolation before resuming:** re-run the sandbox negative probes
   (probes/g1_probe.py shape) on the deployed tag; if a boundary is broken,
   the pilot stays stopped (pilot.md stop conditions) until re-verified.

## 3. Comms templates

- **Internal (pilot teams), S1:**
  "We identified <issue> affecting <surface/window>. Actions taken: <stopped
  runs / rotated keys>. Data impact: <none/known scope>. Next update by
  <time+2h>." — no speculation, no blame, no unverified scope claims.
- **Internal, S2:** one line + workaround + ETA.
- **Do not** name un-fixed bypasses in public surfaces (owner rule: the
  harness is private; working-bypass detail goes to the private handback file).

## 4. Disclosure

- A sandbox escape or auth bypass that COULD touch partner data is disclosed
  to the affected pilot team within 72 h with: what happened, window, data
  classes, fixes, and what changed to prevent recurrence. Counsel reviews the
  wording when the packet (P10) is live; until then the owner writes it.
- Record every incident as a findings-ledger entry (id F-SEC-xx, severity,
  timeline, evidence paths) — an incident without a ledger row did not happen.

## 5. Evidence preservation checklist (S1/S2)

- [ ] container logs (all involved services, before restart)
- [ ] `audit/export` JSON for the window (hash-chained rows)
- [ ] sandbox workspace + tmpdir of the affected run(s)
- [ ] image digests of everything running at the time
- [ ] the alert timeline (Prometheus/Alertmanager screenshots or JSON dumps)
- [ ] git revision of every service (deploy manifest)

## 6. Drills (required, quarterly minimum — see tabletop reports)

1. **Leaked provider key** (canary appears outside the vault).
2. **Reported sandbox escape** (external researcher email).
3. **Provider outage / degraded mode** (models 5xx; degraded-mode.md).

Each drill: timeline, what broke in the RESPONSE (not just the incident),
runbook diffs, evidence-preservation gaps. Reports:
`docs/briefs/evidence/security/tabletop-2026-11-21.md`.
