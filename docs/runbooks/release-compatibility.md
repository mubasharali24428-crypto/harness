# Release, migration and compatibility checklist

This procedure consolidates the current staged-pilot, API/event compatibility and rollback rules.
It describes release controls; it does not prove a release or migration was performed.

## 1. Release stages

1. **Preflight:** review the current capability matrix, open security findings, dependency pins,
   `docs/runbooks/pilot.md`, [client support](client-support.md), and the release evidence report.
2. **Local/integration:** run targeted tests, type checks, lint, static contract/spec drift checks,
   and isolated deployment smoke tests. Record exact image/source revision and results.
3. **Internal canary:** use a named internal team and non-production data; keep automatic merge off,
   human review required, and operator present. Do not use a live provider or sensitive repository
   without its separate approval and controls.
4. **Supervised pilot:** only after P0–P3 prerequisites, set `HARNESS_PILOT_TEAMS` to an explicit,
   non-empty allowlist, require ownership, use container confinement, and have a named operator
   present. An empty/unset allowlist opens collaboration to all teams.
5. **Expansion:** no automatic expansion. Review incidents, task feedback, measured pilot/baseline
   data, SLO/alert state, and open matrix rows; a human records the decision and stop conditions.

## 2. Migration and backfill

- Add a new numbered SQL file under `section1/migrations/`; never edit an applied migration.
- Apply the one-shot migration runner before app servers. Its schema drift gate requires the
  database's applied migration set to be a subset of the migrations declared by the image.
- Migrations are forward-only. Describe lock duration, table size, default/backfill strategy,
  old/new application compatibility, and expected deployment order in the release note.
- Prefer additive expand/dual-read/backfill/contract steps when old and new app images could overlap.
  This is a release design rule; verify each migration's actual locking and compatibility rather
  than assuming all additive-looking DDL is safe.
- For large or policy-sensitive data conversions, run a read-only/shadow comparison first and
  retain counts and mismatch examples without customer payloads.
- Validate the migration chain on a scratch PostgreSQL instance. No migration is approved for a
  shared or production database based on SQLite-only evidence.

## 3. API, events and client compatibility

- HTTP uses `/api/v1`; breaking request/response changes require an API version/contract decision.
- Frozen `harness-contracts` changes require a version bump and consumer/mock updates in the same
  change. Regenerate and check the TypeScript mirror; update OpenAPI and client specs together.
- Keep a superseded wire form for at least one release when the current documented deprecation
  policy applies. Announce removal in release notes and identify the first release where removal is
  allowed.
- Event schema versions and unknown-event behavior must preserve replay safety. Test mixed-version
  clients against additive event fields and the documented stale-client behavior before rollout.
- Supported client versions must be stated in release notes. Generated SDKs and UI clients must be
  tested against the deployed spec, not just their local handwritten types.

## 4. Rollback and stop-expansion rules

- App-only rollback is allowed only when the previous image understands the applied schema and
  event stream. The drift gate must pass before old code starts.
- There are no schema down-migrations. If new schema/data makes the old image unsafe, restore from
  a verified pre-deploy backup/PITR into a separate target and follow
  [`rollback.md`](rollback.md). Never edit the migration ledger by hand.
- Stop expansion on any cross-tenant access, secret exposure, unapproved high-impact action,
  unconfirmed run stop, missing safety audit, sustained error/latency/queue alert, or repeated
  unexplained failure. Remove the affected team from the explicit allowlist and stop active runs
  with confirmation; do not empty the allowlist.
- Preserve image tags, migration ledger, alert snapshot, request IDs, event cursors, incident
  timestamps and human decision in release evidence. Do not capture prompts/artifacts unless the
  incident evidence policy explicitly authorizes that content.

## 5. Release note and administrator notice

Every release notice records: date/build ID; supported client versions; user-visible changes;
contract/event changes and deprecations; migration order/backfill and expected downtime; pilot
eligibility and prerequisites; known gaps; monitoring/stop conditions; rollback path; operator and
support contact; and the evidence links. For internal-only releases, retain the same notice in the
release evidence folder.

## Current verification status

The existing pilot, rollback, API versioning and contract docs describe these controls. The
combined staged rollout and image-plus-schema PITR path is not currently verified on the live
deployment. Consult `docs/briefs/evidence/s10/readiness-report.md` before enabling a broader rollout.
