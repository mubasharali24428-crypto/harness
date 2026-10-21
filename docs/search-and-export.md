# Search, history export and data deletion (part 50)

As built (Session 6, lane SE). Code: `section4/src/harness_orchestrator/search.py` (router, `configure_search(service)`,
`sync_index`, `reindex`) and `exports.py` (router, `configure_exports(service)`, `purge_expired_exports`). Schema:
`section1/migrations/0050_search_export.sql`. Tests: `section4/tests/unit/test_search.py`, `test_exports.py`,
`test_exports_deletion.py` (fixture: `test_search_support.py`). Style model: `docs/discussion.md`. Evidence:
`docs/briefs/evidence/s6/lane-se.md`.

Prefix of every route: `/api/v1/orchestrator`. Errors use `{"detail": {"error": {"code", "message"}}}` plus extra fields where noted.

## 1. What is searchable (directive 001, 003)

| kind | source | text indexed | never indexed |
|---|---|---|---|
| `comment` | `comments` (run and session scope) | the comment body, secret-scrubbed, <= 8000 chars | hidden, deleted, purged and past-retention comments; comments of deleted sessions |
| `event` | `run_collab_events` (id `c<seq>`) and `agent_events` (id `a<seq>`) | the event kind plus the text fields `content reason summary message note` of the payload | presence, typing, cursor events and `guidance.delivered` (same filter as the history feed); `terminal_output` and `diff_progress` agent events; every other payload key |
| `run` | `agent_runs` + `projects` | branch (or id), latest plan goal, state | |
| `session` | `collab_sessions` | title, description | deleted sessions (nobody can search them, admins included; admins use the discussion export) |
| `approval` | `high_impact_approvals` | action, paths, summary | the diff, the digest |
| `artifact` | `artifacts` (Session 6 lane AR table) | name, media type, kind | quarantined and deleted artifacts; **file contents are not indexed** (see gaps) |

If a source table does not exist (for example `artifacts` before lane AR lands) the source is **skipped, not an error**.

Free text goes through `search.scrub_text`: private key blocks, provider tokens (`sk-`, `ghp_`, `github_pat_`, `AKIA`, `xox*`),
JWTs, `Bearer ...` and `password|secret|token|api_key|authorization|credential = value` assignments become `[redacted]`.
It is a heuristic: a secret with no recognisable shape in free text is not caught, which is why JSON payloads are never copied
wholesale (only the five text keys above) and why `clean_json` (used by exports) also drops every key matching the collab
secret rule (`token|secret|password|passwd|key|otp|authorization|credential`) and every hidden-reasoning key
(`reasoning|thought|thinking|scratchpad|chain of thought|inner monologue`).

## 2. How a query is served (directive 004)

`GET search?q=&kind=&projectId=&participantId=&status=&approvalState=&from=&to=&limit=&cursor=`

1. The scan reads only `search_documents` rows with `team_id = principal.teamId`, in `created_at DESC, id DESC` order.
2. **Every candidate is rebuilt from its live source** with the same builder the indexer uses, and kept only if it is still
   eligible *now* (comment visible, not purged, inside retention; session not deleted; artifact available; event kind still
   durable; the person who caused it not erased) **and** the caller may see it now: run data needs the run to belong to the
   caller's team and the pilot allowlist to admit the team; session data needs an active membership (admins: every
   non-deleted session of the team). A stale index row (hidden since, member removed since, run moved) therefore serves nothing.
3. The hit's `title`/`snippet` are built from that **live** text (scrubbed, <= 200 characters around the first match,
   **HTML-escaped**: `snippetEncoding: "html"`), never from the index. A hit whose live text no longer contains the query terms
   is dropped (the document changed after it was indexed).
4. `status`, `approvalState`, `projectId` and `participantId` are checked again on the live values (the index only narrows).
5. **No oracle.** There is no total or count. `nextCursor` is returned only when a further *authorised* hit exists. If the scan
   budget (`HARNESS_SEARCH_SCAN_BUDGET`, default 600 index rows examined) runs out before a page is full, the response says
   `scanLimited: true` with a cursor to continue; that reveals only that many index rows matched, not what they are.

Query: `q` (2..100 characters; every whitespace-separated term, at most 8, must match; optional when another filter is given),
`kind` (`run session comment event artifact approval`), `limit` 1..50 (default 25), `cursor` (opaque, from `nextCursor`),
`from`/`to` (ISO 8601 on `createdAt`, bare dates mean 00:00 UTC, inclusive). Results are newest first (no relevance ranking, so
paging is stable). Result item: `kind, sourceId, runId, sessionId, projectId, title, snippet, status, approvalState,
actorUserId, createdAt`. Every role (admin, engineer, viewer, guest) may search; each sees only what its role may see.

Engine: PostgreSQL uses `to_tsvector('simple', title || ' ' || body) @@ plainto_tsquery('simple', ...)` (whole words, no stemming);
SQLite, and PostgreSQL after any error in the full text query, uses `lower(...) LIKE '%term%'` (substrings; `%`, `_` are
literals). `HARNESS_SEARCH_ENGINE=like` forces LIKE. The two engines differ on partial words; the live term check is substring
based so it never rejects a full text match.

Codes: `BAD_QUERY`, `BAD_CURSOR`, `BAD_FILTER` 422; `SEARCH_UNAVAILABLE` 503 (no database wired).

## 3. Indexing, delay and consistency (directive 009)

* **Incremental, watermark driven.** `await search.sync_index(factory, team_id=None, max_batches=20)` walks every source per team.
  `search_index_state(team_id, source, watermark)` holds progress: event streams by `seq` (re-reading the last 200 values for late
  commits with a lower seq, jumping to the table's max `seq` when the page ends so noise is not rescanned), mutable sources
  (comments, runs, sessions, approvals) by their change timestamps minus an **overlap window** (`HARNESS_SEARCH_OVERLAP_SECONDS`,
  default 300 s, covers clock skew and slow commits), artifacts by a full reconcile (they have no change stamp; capped at 5000
  rows per team). Writes are upserts on `(team_id, kind, source_id)`, so re-reading and concurrent syncs are harmless
  (`test_concurrent_syncs_do_not_duplicate_documents`). A source that fails is reported in `errors` and the others still run.
* `await search.reindex(factory, team_id)` drops the team's documents and watermarks and rebuilds from the sources; it also
  removes documents whose source row was hard-deleted (the incremental sync cannot see vanished rows) and writes one
  `search.reindexed` audit row (team id and counts only). Use it after raising comment retention, after restoring data, or if the
  index is suspected stale. There is no HTTP route for it (not in the lane's path list); wire it to a CLI or admin task.
* **Who calls the sync.** The orchestrator process runs `sync_index` every `HARNESS_SEARCH_SYNC_SECONDS` (default 60, `0` = off; `schedule.py`), and `GET search` also refreshes a team's index first when its last sync
  is older than `HARNESS_SEARCH_AUTO_SYNC_SECONDS` (default 30, `0` = off), covering at most 4 batches of 500 events. A refresh
  failure is logged and never fails the query. The scheduled job bounds the delay for quiet teams.
* **Expectations.** Discovery is **eventually consistent**: a new or edited item appears after the next sync, so within
  `HARNESS_SEARCH_AUTO_SYNC_SECONDS` of the next query by anyone in the team (default 30 s), or never if refresh is off and
  nobody syncs. Removal and access are **immediately consistent**: hiding, deleting or purging a comment, deleting a session,
  quarantining an artifact, removing a member or changing the pilot allowlist takes effect on the very next query because
  serving re-checks the live source. An edited comment stops matching its old text at once and matches its new text after the
  next sync. Hard-deleted sources vanish from results at once and from the table at the next `reindex`.
* `GET search/status` returns, for the caller's team: `engine`, per-source `watermark`, `lastSyncedAt`, `lagSeconds`, `lastError`,
  the `indexing` block (refresh interval, overlap, the plain statement above), the `consistency` sentence, `indexedKinds` and a
  `notIndexed` list. `documentCounts` is filled **for admins only** (counts of rows the caller cannot see would be an oracle).

## 4. Export (directive 005, 006, 008, 010)

`POST exports` (export permission = admin or engineer, as discussion export; org-linked teams are additionally gated by the policy
middleware) body:

| field | meaning |
|---|---|
| `kind` | `session_history`, `run_history`, `audit_evidence` (admin only) |
| `scopeId` | the session or run id (required for the two history kinds; for `audit_evidence` optional: narrows to audit rows whose `target_id` is that run or session, otherwise the whole team) |
| `format` | `json` (default) or `ndjson` |
| `includeActors` | default `true`; `false` replaces every person by a stable pseudonym (`actor-1`, ...) and drops the session roster |
| `from`, `to` | optional ISO 8601 window on record timestamps |
| `requestId` | idempotency key per requester: a retry returns the same job (200, `duplicate: true`) |

An export is a **job** (`export_jobs`) built synchronously: `201` with the job view (`id, kind, scope, format, state, errorCode,
recordCount, sizeBytes, sha256, requestedBy, createdAt, completedAt, expiresAt, downloadCount, options, download`). States:
`running` (visible only if the process dies mid-build), `completed`, `refused` (`EXPORT_TOO_LARGE`), `failed`
(`EXPORT_FAILED` 500), `expired`, `deleted`. A foreign, unknown, deleted or not-joined scope is `404 NOT_FOUND` with the same body
for all four (no oracle); a role without the permission is `403 ROLE_DENIED`; `422 BAD_SCOPE`, `BAD_RANGE`.

**Contents** (`harness.export/1`). `json` is one object `{...header, records: [...]}`; `ndjson` is a `header` line, one line per
record and a `footer` line `{"_type":"footer","recordCount":n,"complete":true}` (a file without the footer is truncated).
Header: `format, exportId, kind, scope, teamId, requestedBy, generatedAt, ordering, timestamps, actorsIncluded, window, redaction,
limits, recordCount` plus `run` (id, projectId, branch, state) or `session` (id, title, description, state, linkedRuns and, with
actors, `members`). Record: `order, stream, kind, seq, id, ts, runId, actor, data`.

| stream | from | `data` |
|---|---|---|
| `collab` | `run_collab_events` of the run(s) (durable feed only) | `requestId`, `payload` (cleaned) |
| `agent` | allow-listed `agent_events` | `summary` |
| `approval` | `high_impact_approvals` | action, paths, summary, state, decidedBy, decidedAt (no diff) |
| `comment` | comments of the run and, for a session export, of the session and of its linked runs | scope, parent, state, version, `body`, `bodyWithheld` (`hidden`, `deleted`, `purged`) |
| `audit` | `audit_log` (team-scoped like `GET /api/v1/audit/export`) | actor member and kind, target, `detail` (cleaned, `detailRedacted`), `chain {pos, prevHash, rowHash}` (PostgreSQL only; verify with `section1/scripts/verify_audit_chain.py` on the database, hashes cover the raw detail) |

**Ordering.** Records are sorted by `ts`, then `stream`, then `seq`, then `id`; `order` is the 1-based position, `seq` the source
sequence (collab/agent/audit). Same data gives the same records (`test_export_is_deterministic_for_the_same_data`). Timestamps
are ISO 8601 UTC.

**Actor identity.** `actor` is `{userId, displayName, kind}` for team members, `{userId, kind: "system"}` for `system:*` ids,
`{pseudonym}` when `includeActors` is false, and `{userId: null, displayName: "deleted user", erased: true}` for a person whose
deletion request was executed (for records at or before the execution; later activity is shown normally). An export only ever
reads the requester's own team, and only scopes the requester may open, so identities are limited to people the requester can
already see in that scope. `audit_evidence` is admin only and keeps identities (it is evidence).

**What is removed** (directive 008): secret-shaped keys and values, hidden-reasoning keys, presence/typing/cursor events and
engine markers, terminal output and diffs, file contents; hidden comment text for everyone but the author and admins,
deleted, purged and past-retention comment text for everyone (the discussion export remains the place for retained revisions);
everything outside the requester's team or scope.

**Limits** (directive 010). `HARNESS_EXPORT_MAX_RECORDS` (default 20000) and `HARNESS_EXPORT_MAX_BYTES` (default 10 MiB). Over
either, nothing is truncated: the job is `refused`, no file is written, the response is `413 EXPORT_TOO_LARGE` with
`{limit: "records"|"bytes", max, exportId}`, and an `export.refused` row is written. Narrow the window or the scope and retry.

**Store and expiry.** Files live in `HARNESS_EXPORT_DIR` (default `<tmp>/harness-exports`, mode 0600 in a 0700 directory), named
`<jobId>.<format>` (the reference is built server-side and validated against a strict pattern before any file access). They
expire after `HARNESS_EXPORT_TTL_SECONDS` (default 86400). `purge_expired_exports(factory)` deletes expired files and finishes
removals interrupted by a crash; the orchestrator runs it every `HARNESS_EXPORT_PURGE_SECONDS` (default 3600, `0` = off). The store is process-local disk like `diffstore.py`: with several
orchestrator replicas it must be a shared volume. The compose stack does this: every orchestrator replica mounts the
`harness_store` volume and `HARNESS_EXPORT_DIR=/var/lib/harness-store/exports` (`evidence/s12/part54.md` §shared store).

**Download.** `GET exports/{id}` and `GET exports/{id}/download` need the export permission and are for the requester and team
admins; anyone else, another team and unknown ids get the same `404 EXPORT_NOT_FOUND`. Download **re-authorises the scope**
(a person who lost the session or run gets 404 even for their own export), checks expiry (`410 EXPORT_EXPIRED`, also for deleted
jobs), verifies the SHA-256 (`500 EXPORT_CORRUPT`) and serves `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`,
`Cache-Control: no-store`, `X-Export-Sha256`, type `application/json` or `application/x-ndjson`. `GET exports` lists the
requester's jobs (admins: the team's), newest first, max 100.

**Audit.** `export.requested` (kind, scope, format), `export.completed` (counts, size, digest), `export.refused` (reason:
`ROLE_DENIED`, `NOT_FOUND`, `EXPORT_TOO_LARGE`, `EXPORT_FAILED`), `export.downloaded` (job, size, digest, whether the owner
downloaded). Ids and counts only, never content.

## 5. Deletion requests (directive 007)

* `POST deletion-requests {reason?, requestId?}`: any signed-in role asks for **their own** authored data to be deleted (the
  subject is always the principal). One open request per person (`409 DELETION_PENDING`). `GET deletion-requests`: your own
  requests (admins: the team's, optional `state`). Every response carries `policy`: the lists below.
* `POST deletion-requests/{id}/approve` and `/reject {note}`: **admin only** (`403 ROLE_DENIED` plus an `authz.denied` row);
  a person cannot decide their own request (`403 SELF_DECISION`, unless `HARNESS_ALLOW_SELF_APPROVAL=1` as for approvals);
  rejection needs a reason (`422 REASON_REQUIRED`); an unknown or foreign id is `404 DELETION_NOT_FOUND`;
  an already decided request is `409 NOT_PENDING`. **Approval executes immediately** and is idempotent (`duplicate: true`
  afterwards; a crash between approval and execution is resumed by approving again).
* States: `pending` -> `approved` -> `executed`, or `pending` -> `rejected`. Audit: `deletion.requested`, `.approved`, `.rejected`,
  `.executed` (ids, counts, before/after state).

**Deleted** (`policy.deletes`, executed in one transaction, files afterwards): the text of everything the person wrote as a
comment (tombstone consistent with `discussion.py`: `state='deleted'`, body emptied, display-name snapshot replaced by
`deleted user`); their earlier revisions, except in audit-sensitive scopes; their reactions and the mentions of them; search
documents of their comments, of events they caused, of approvals they requested, and their id in run/session participant lists;
their export files and every unexpired history export whose scope holds data they authored (file removed, job `deleted`).
Afterwards the indexer does not re-index events or approvals they caused up to the execution time, and exports show them as a
deleted user.

**Run retention holds.** Comments in a scope under a hold (a run with a `run_retention_holds` row, or a session that
contains such a run) are not tombstoned, renamed or blanked; reactions and mentions on them stay; history exports of that scope
stay. Everything else in the request still executes. `result.deleted` reports `commentsHeld` and `exportFilesHeld`. After the
hold is released the person files a new request. Hold create/release and the deletion serialize on PostgreSQL through the same
advisory lock.

**Retained, with the reason** (`policy.retained`, also stored in the executed result): `audit_log` (append-only, hash chained,
ids and counts only); `run_collab_events` (run history including guidance an agent received is evidence of why it acted; kept
until the run's retention job, search no longer finds them); `high_impact_approvals` (a digest-bound decision); the comment
tombstone row (thread structure, opaque author id, timestamps); `comment_revisions` in audit-sensitive scopes (as in
`docs/discussion.md` section 7); the `deletion_requests` and `export_jobs` rows (proof, no content); artifacts (follow the artifact
controls); the account and team membership (identity service). Audit-evidence exports are a documented retained copy.

## 6. Routes, codes, settings

Routes: `GET search`, `GET search/status`, `POST exports`, `GET exports`, `GET exports/{exportId}`, `GET exports/{exportId}/download`,
`GET|POST deletion-requests`, `POST deletion-requests/{requestId}/(approve|reject)`.

Codes: `ROLE_DENIED` 403, `SELF_DECISION` 403, `NOT_FOUND` `EXPORT_NOT_FOUND` `DELETION_NOT_FOUND` 404, `EXPORT_EXPIRED` 410,
`BAD_QUERY` `BAD_CURSOR` `BAD_FILTER` `BAD_SCOPE` `BAD_RANGE` `REASON_REQUIRED` 422, `DELETION_PENDING` `NOT_PENDING`
`EXPORT_UNAVAILABLE` 409, `EXPORT_TOO_LARGE` 413, `EXPORT_FAILED` `EXPORT_CORRUPT` 500, `SEARCH_UNAVAILABLE`
`EXPORT_UNAVAILABLE_DB` 503.

| setting | default | meaning |
|---|---|---|
| `HARNESS_SEARCH_ENGINE` | `auto` | `like` forces LIKE on PostgreSQL |
| `HARNESS_SEARCH_AUTO_SYNC_SECONDS` | 30 | refresh age before a query syncs the team (0 = never) |
| `HARNESS_SEARCH_OVERLAP_SECONDS` | 300 | re-read window for timestamp watermarks |
| `HARNESS_SEARCH_SCAN_BUDGET` | 600 | index rows examined per request |
| `HARNESS_EXPORT_DIR` | `<tmp>/harness-exports` | export store |
| `HARNESS_EXPORT_MAX_BYTES` | 10485760 | size cap |
| `HARNESS_EXPORT_MAX_RECORDS` | 20000 | record cap |
| `HARNESS_EXPORT_TTL_SECONDS` | 86400 | file lifetime |

Wiring (lead): `app.include_router(search.router)`, `app.include_router(exports.router)`, `search.configure_search(service)`,
`exports.configure_exports(service)`; policy classification of the paths in the Session 6 prompt; a worker or cron for
`search.sync_index`, `exports.purge_expired_exports` and `discussion.purge_expired`.

## 7. Limits (honest)

* Verified on SQLite. Since Session 11 one end-to-end run also passes on PostgreSQL 18 (`test_session6_live_pg.py`: index sync, search with
  live re-authorisation, a run-history export, retry replay); it found and fixed an empty-list `IN` typed `INTEGER` that failed the export of a
  run with no sessions. Anything off that path (full text ranking, `ON CONFLICT` variants, audit-export chain columns, other timestamptz
  comparisons) is written portably but not covered by its own PostgreSQL test. No GIN index is created
  (the migration must load on SQLite); without one the full text query scans the team's documents. Suggested PG-only follow-up:
  `CREATE INDEX ... USING gin (to_tsvector('simple', title || ' ' || body))`.
* No relevance ranking; newest first. LIKE mode is a sequential scan of the team's documents (bounded by the scan budget per
  request); fine for pilot volumes.
* Artifact **contents** are not indexed or exported (the artifact store belongs to lane AR); only names, media types and kinds.
  Artifact files are not touched by deletion requests.
* Exports are built in the request (no background queue) and held in memory up to the byte cap; the store is a directory (the shared `harness_store` volume under compose), not an object store.
* Session membership changes and approval changes do not bump the source's timestamp, so participant and approval-state
  *filters* can miss an item until the next reindex or source change (the live re-check can only remove false positives).
* Comment revisions are not part of history exports (use the discussion export); event payloads in exports are cleaned but
  guidance text typed by people is exported as written (minus secret-shaped values).
* `export` honours the legacy role gate here; the section1 step-up re-authentication (`require_team_stepup`) that guards
  `GET /api/v1/audit/export` is not applied by section4 (it cannot import section1). Org policy applies through the middleware.
* Raising comment retention after an index pass does not resurrect already de-indexed comments until `reindex`.
* The index sync, export purge, discussion purge and artifact purge run on intervals in every orchestrator replica (`schedule.py`); on PostgreSQL each tick is leader-elected by a session advisory lock (`schedule.leader_only`, `test_schedule_leader_live_pg.py`), so one replica runs it and the others skip that tick; the lock dies with the leader's connection. The jobs stay idempotent. A job that keeps failing is only logged.
