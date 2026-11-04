# Week 9 plan — artifacts, preview/share, deployment approval (W9)

Follows [Week 8](week-8-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Position

W8 is closed with live evidence on both claims (three concurrent runs isolated
and reviewable; a crashed run's boundary reclaimed while its neighbour stayed
untouched — `w8-concurrency-1793123190.json`, `w8-crash-cleanup-1793123310.json`).
The worktree question is answered by construction and recorded in
[artifacts](artifacts.md). W7's LSP half and the W4 third-party MCP leg remain
blocked on prerequisites this repo does not contain (Hermes/H0–H2 and an
external server, respectively) — both named, neither hidden.

The roadmap row this week must satisfy (`marathon-execution-plan.md`, W9):

> **W9 artifacts/preview/deploy** — Preview and share URLs enforce access;
> deployment approval binds reviewed revision; revocation works.

## The gate, expanded into claims that must be shown

1. A preview/share URL carries its own authority: it is served **without** an
   `Authorization` header, and still refuses a foreign team's artifact, an
   unknown token, a revoked token, an expired token and a deleted artifact.
2. A shared artifact is served as **data**, never as a document: the response
   keeps the attachment disposition, `nosniff`, and never becomes HTML.
3. Revocation is immediate and durable: one call stops every copy of the link,
   and a second call reports the same state instead of resurrecting it.
4. A deployment approval **binds the reviewed revision**: the digest of the
   reviewed changes, the head sha it was approved against, and the plan version.
   If any of them moves, the approval is refused — it does not carry over.
5. A deployment approval can be **revoked**, and a revoked approval can never be
   turned into an approved one.
6. Everything above is audited, so "who showed this file to whom, and who
   approved which revision for which environment" is answerable from the log.

## Audited state (2026-10-27)

| Area | What exists today | Where | W9 verdict |
|---|---|---|---|
| Artifact store | Content-addressed blobs, 10 MiB/artifact, 100 MiB/200 rows per run, 90-day retention, quarantine, delete→tombstone | `section4/src/harness_orchestrator/artifacts.py:52-55`, `artifact_store.py:45-85`, `section1/migrations/0049_artifacts.sql:25-58` | reuse; no change needed |
| Artifact access | `read/write/approve` deps → `_authz_dep` (Bearer + team/run ownership, uniform 404) | `artifacts.py:186-188`, `api.py:299-319`, `artifacts.py:338-347` | the model to preserve for every new route |
| Preview | `GET …/artifacts/{id}/preview` — JSON only, text previews never HTML | `artifacts.py:778-811` | today's preview is **authenticated**; the gate asks for a URL that works without a header |
| Share links | **absent** — no token, expiry, audience, revocation record, or shares table | audit: no `share` route under any router prefix; `0049_artifacts.sql` creates no shares table | **build** |
| URL-borne tokens | one-time WS ticket + one-time OAuth start ticket, both single-use | `harness_identity/ephemeral.py:64-100`, `section7/src/lib/api.ts:670-678` | precedent only: shares need multi-use + revocation, so they get a registry |
| Deployment | **absent** — no deployment/environment/release entity, no endpoint | audit: no `deploy*`/`release*` table in `section1/migrations`; `control.py:1088-1103` is lease/control revocation | **build the record, not an executor** |
| Reviewed-change binding | `review_change(diffSha)` CAS + `409 CHANGE_STALE`/`CHANGE_SUPERSEDED` | `artifacts.py:1186-1232` | reuse the shape for deployments |
| Merge approval | binds `changes_digest` + `head_sha` + `base_sha` + `plan_version`, becomes `outdated` when they move | `artifacts.py:1043-1045`, `:1622-1682`, `:1488-1513` | the binding W9 must reproduce for deploys |
| Revocation | artifacts (delete/retention), approvals (scope change/amend/expiry), change supersession, control revoke | `artifacts.py:621-709`, `collab.py:786-812,948-988,1198-1210`, `control.py:473-488` | exists for everything **except** shares and deployments |
| Tests | artifacts + VCS + collab suites pin digest binding, quarantine, retention, tenant 404 | `section4/tests/unit/test_artifacts.py`, `test_artifacts_vcs.py`, `test_collab_backend.py` | extend for shares/deployments; no share/deploy test exists today |

## Decisions

**1. A share link is a capability URL backed by a registry row, not a signed
string.** A signature can be verified offline but cannot be withdrawn; the gate
requires revocation. The server stores only `sha256(token)` — the URL is shown
once, a database leak does not yield working links, and revocation is a write to
one row. Tokens are 32 random bytes, url-safe; expiry is per link with a
deployment default (`SHARE_LINK_TTL_SECONDS`, default 7 days, capped at 90 to
match retention); `last_used_at`/`uses` are recorded so a link's history is
answerable.

**2. The anonymous route serves exactly one artifact's bytes, nothing else.**
No run listing, no metadata beyond name/type/size, no directory. It keeps the
same response discipline as `/content` (attachment, `nosniff`, sandbox CSP) and
refuses HTML. A capability that can serve a document to a browser is an XSS
vector against the deployment's own origin; a link that can *only* download is
not.

**3. Deployments are records, not actions.** Nothing in this repo can deploy
anything, and adding an executor would be a footgun the gate does not ask for.
A deployment row records: run, environment (an operator-declared string),
`changes_digest`, `run_head_sha`, `plan_version`, requester, and then an
approval decision bound to those values. "Deploy" remains a human/host action;
what the harness guarantees is that the approval names the exact reviewed
revision and stops being valid when that revision moves.

**4. No UI this week.** The gate is about enforcement. Frontend work for shares
or deployments would not change any claim above; it stays on the gap list with
the merge-approval UI it would sit beside.

## Slices, in order

1. **Share links** — `0063` migration (`artifact_shares`), `POST
   /runs/{runId}/artifacts/{artifactId}/share` (Writer, team-scoped), `GET
   /shares/{token}` (anonymous, single artifact), `GET /runs/{runId}/shares`
   and `POST /shares/{shareId}/revoke` (Writer), all audited. Tests pin: foreign
   team 404, unknown/revoked 404, expired 410, deleted artifact 410, HTML never
   served, revocation idempotent.
2. **Deployment approval bound to the reviewed revision** — `0064` migration
   (`deployments`, `deployment_approvals`), request → approve/revoke with CAS on
   `changes_digest`/`run_head_sha`/`plan_version`, `409 DEPLOYMENT_STALE` when
   the revision moved, revocation final. Tests pin each refusal.
3. **Live drill + evidence** — `scripts/e2e/w9_share_deploy_drill.py` against
   the compose stack: mint a link, fetch it with no auth header, revoke and
   fetch again, expire one against a short TTL, prove a foreign team cannot
   mint; then approve a deployment, move the head, watch the approval refuse,
   revoke, and watch it stay refused.

## Known constraints

- `contracts/` is FROZEN: nothing here changes it. The new tables are additive
  migrations, discovered by `deploy/runners/migrate.py` and subject to the
  drift gate (`section1/tests/test_migrate_drift_gate.py`).
- The share route must not reuse the Bearer dependency, so it is the first
  unauthenticated route on the orchestrator besides health/metrics: it gets its
  own rate-limit-free but strict shape (one artifact, no enumeration) and its
  own audit event. Any new field it exposes is a new attack surface.
- SQLite and PostgreSQL must agree: JSON text columns, no dollar quotes, no
  `now()` defaults (the migration convention in `0049`).
- The stack's model is now a hosted one (OpenRouter) for drills; nothing in W9
  needs a model, so the drills are deterministic and fast.

## Outcome (2026-10-27)

Evidence: `docs/briefs/evidence/pilot-prep/w9-share-deploy-1793124898.json` —
**PASS, 21/21 checks** against the compose stack (`harness-backend:vdev-local`,
migrations `0063`/`0064` applied by the `migrate` job).

What the drill actually observed, per claim:

1. **A link carries its own authority.** `GET /shares/{token}` with no
   `Authorization` header returned the exact bytes (`sha256` equal to both the
   artifact row and the `X-Artifact-Sha256` header). A second, independent user
   in another team got `404 NOT_FOUND` from the mint, list and revoke routes —
   and `200` from the token alone, which is the point of a capability: the link,
   not the session, is the grant.
2. **Served as data.** `application/octet-stream`, `attachment; filename=…`,
   `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox;
   default-src 'none'`, `Cache-Control: private, no-store`.
3. **Revocation.** The same token answered `404` immediately after revoke; a
   second revoke returned the identical `revokedAt`; the anonymous opens were
   counted (`uses`), and a 60s-TTL link answered `410 SHARE_EXPIRED` after the
   minute (checked by waiting, not by faking the clock).
4. **The binding.** The request captured `changesDigest`, `runHeadSha`=`c…`,
   `baseSha`, `planVersion`=1, all equal to the run's current revision. A client
   echoing a different digest was refused (`DEPLOYMENT_STALE`,
   `staleFields:["clientDigest"]`). After the head moved to `d…`, the approved
   request was listed `stale:true` and a request made **before** the move
   refused approval with `staleFields:["runHeadSha"]` while staying `requested`
   — a refusal is not a decision.
5. **Revocation is final.** Revoking the approved request left it `revoked`; a
   further approve answered `409 DEPLOYMENT_REVOKED`; the second revoke kept the
   first timestamp.
6. **Audit.** `audit_log` for this drill's ids holds `artifact.shared|2`,
   `share.opened|3`, `artifact.share_revoked|2`, `deployment.requested|2`,
   `deployment.approved|1`, `deployment.revoked|1`.

Deviations from the plan text, recorded rather than smoothed:

- The plan sketched two tables (`deployments`, `deployment_approvals`). One
  `deployments` row carries the request, the decision and the revocation: there
  is exactly one approval per request, so a second table would have been a join
  over a 1:1 relation with no independent history to add — the history is in
  `audit_log`. The state CHECK is `requested|approved|revoked`, exactly what the
  API can write; the sketched `rejected` state had no writer and was removed.
- The drill creates its run through `POST /plan`, which spends one planner call;
  everything after that is HTTP-only. W9's own code paths never call a model.

Not claimed, still open:

- No UI for minting links or requesting deployments (decision 4: API-first).
- Nothing deploys. A deployment row is an approval record bound to a revision;
  there is no runner, no target credentials and no artifact promotion here.
- `NOTHING_TO_DEPLOY` / `REVISION_UNKNOWN` are unit-tested, not re-drilled live:
  they need a second run without changes, and the gate is the binding, which the
  drill does exercise end to end.
