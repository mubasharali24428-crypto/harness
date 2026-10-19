# Collaborative discussion (part 43)

As built. Code: `section4/src/harness_orchestrator/discussion.py` (router, `configure_discussion(service)`, search,
export, retention). Schema: `section1/migrations/0045_discussion.sql`. Tests:
`section4/tests/unit/test_discussion.py`, `test_discussion_flows.py` (support: `test_discussion_support.py`).

## 1. What discussion is (directive 001, 004)

A **comment** is a human message attached to a scope: a **run** (`scope_kind='run'`) or a collaboration **session**
(`'session'`). Replies, mentions, reactions and references hang off comments. Comments are **never agent input**.
Nothing reads `comments` on the engine side, a new comment writes no `redirect.*` event and no `control_instructions`
row (`test_comments_are_never_agent_input`). The only road to the agent is **promote** (section 8).

Comments are text only. There are **no attachments** in this lane, so there is nothing to retain or export for them
(the export says so in its `attachments` field).

## 2. Who can do what (deny by default)

| Scope | Visible to | Guard |
|---|---|---|
| run | every member of the run's team | the shared run guard `api._authz_dep("read", audit=True)` (project to team join) |
| session | **active session members** and team **admins** (admins also see `deleted` sessions) | `collab_sessions.team_id` + `session_members.status='active'` |

A foreign or unknown scope answers `404 NOT_FOUND` with the same envelope (`run not found` / `session not found`);
a session you are not a member of looks exactly like an unknown id. Comment ids are looked up inside
`(scope, team)`, so a foreign comment id is `404 COMMENT_NOT_FOUND` on a scope you can see. The role gate runs
**before** the ownership check (no new existence oracle for low roles) and a refused role leaves one `authz.denied` audit row.

| Action | Legacy role | Policy permission (gate) |
|---|---|---|
| list, history | any | `read` |
| create, edit, delete own, react | admin, engineer, viewer (**not guest**) | `comment` |
| promote | admin, engineer | `steer` |
| moderate (hide, restore, delete) | admin | `administer` |
| export | admin, engineer | `export` |
| change settings | admin | `administer` |

For teams linked to an org the policy gate (`policy_enforce.py`) additionally applies; for other teams the legacy
column is the whole check. Run-scope routes also obey the pilot allowlist (`HARNESS_PILOT_TEAMS`, 404
`PILOT_NOT_ENABLED`); session routes do not. A session accepts new discussion while `active`, `paused` or
`completed`; `archived` and `deleted` sessions are read-only (`409 SCOPE_READ_ONLY`, reads and delete/moderation still work).

## 3. Comments, replies, authorship (directive 002, 006)

`POST {scope}/{id}/comments` body: `body` (1..4000 chars, trimmed), `parentId`, `mentions` (<= 10 user ids), `refKind`/`refId`/`refPos`,
`requestId`. Returns 201 (200 with `duplicate: true` for a retried `requestId`; ids are per author and scope, and a
simultaneous retry is resolved by a unique index).

* **Replies** nest at most **3 levels** (depth 0, 1, 2); deeper is `422 REPLY_TOO_DEEP`. The parent must be in the same
  scope and team (`422 BAD_PARENT` for unknown, foreign and other-scope parents alike) and must be `visible`
  (`409 PARENT_UNAVAILABLE`). The list is flat and chronological with `parentId` and `depth`; the client builds the tree.
* Each comment shows its **author** (`userId` plus the display name **snapshot** taken at writing, so history survives a rename or
  removal), `createdAt`, `editedAt`, `version`, `edited`, `state`, `moderation`, `deleted`, and a `can` block (edit, delete,
  react, reply, promote, moderate) computed for the caller from the live state, plus `editableUntil`.
* `GET` is keyset paged: `limit` (1..100, default 50) and `after=<comment id>`; `nextCursor` is the last id or null.
  `policy` in the response lists the edit window, retention, reply depth, reactions and reference kinds.

## 4. Mentions without leaks (directive 002, 008)

`mentions` is an explicit list of user ids (the client resolves `@name`; text is never parsed). A mention is kept only if the
person **can already see the scope**: run scope = a member of the author's team; session scope = an active session member
or a team admin. Everyone else (unknown id, another team, a teammate outside a private session) is **silently dropped and
counted** in `ignoredMentions`, identically for all three cases, so a mention reveals nothing about who exists or who
is in a private session. Self mentions are dropped uncounted. Stored mentions are returned with the display names of
team members only.

Each valid mention calls lane N's `notify.emit(kind='mention', source_kind='comment', source_id=<comment id>,
run_id|session_id, dedupe_key='mention:<comment>:<user>')` **in the same transaction** (savepoint, never fails the comment).
It passes ids only: no comment text, no session title. The module is imported lazily; if `harness_orchestrator.notify`
does not exist nothing is sent and the mention rows are still stored. The recipient's inbox link re-checks access
(lane N), so a mention never grants access. An edit that adds people notifies only the newly added.

## 5. References (directive 003)

`refKind` is one of `plan`, `file`, `diff`, `approval`, `event` with `refId` and `refPos`:

| kind | `refId` | `refPos` | Available when |
|---|---|---|---|
| `approval` | high-impact approval id | unused | the approval belongs to the team and to a run of the scope |
| `diff` | approval id | line (optional) | as `approval`, and it has a stored diff |
| `plan` | plan id | unused | the plan belongs to a run of the scope |
| `event` | run id | collab event `seq` (required) | the run is in the scope and the event exists for the team |
| `file` | repo-relative path (no `..`, not absolute) | line (optional) | always: a label, authorised by the scope. There is no file store to look it up in |

For a session, "a run of the scope" means a run linked to it with `session_runs`. A bad reference is `422 BAD_REF` on write
(`BAD_PATHS` for a malformed path). **Every read re-evaluates** the reference: an unavailable one is returned as
`{kind, available: false}` with **no id and no position**, so a link whose target moved to another run, another team or
disappeared leaks nothing. References are fixed at creation.

## 6. Reactions (directive 002)

Closed set: `thumbs_up thumbs_down heart eyes celebrate question check` (enforced by the API enum **and** a database CHECK).
`POST .../reactions {reaction, present: true|false}` is a set operation (idempotent), returns the counts with `mine`.
Not allowed on hidden, deleted or expired comments (`409 COMMENT_UNAVAILABLE`). Reactions are not audited.

## 7. Edit window, history, deletion (directive 006, 007)

* **Edit**: author only (`403 NOT_AUTHOR`), within **`editWindowSeconds` of creation** (team setting, default 900, 0 = no
  edits, max 86400; exactly at the boundary is still inside, `409 EDIT_WINDOW_CLOSED` after). `expectedVersion` is required
  (`409 STALE_COMMENT_VERSION` with `currentVersion`; the update is a compare-and-set on `version`, so two simultaneous edits
  cannot both win). Not on hidden, deleted or expired comments. Same text = `changed: false`, no new version. `mentions`
  may be sent to replace the mention set.
* **History**: `GET .../comments/{id}/history` lists every revision (version, editor, time, text). Version 1 is the original.
  Text follows the comment's visibility: hidden = author and admins; deleted = admins only (and only where kept, below);
  expired = never.
* **Delete** (author, any time; `DELETE` is idempotent) is always a **tombstone**: the row stays with its place in the
  thread, author, timestamps, moderation fields, reactions and mentions; `state='deleted'`, `deletedAt/By`; the text leaves
  `comments.body`. What happens to the revisions depends on the scope:

  | Scope | Revisions after delete |
  |---|---|
  | **audit-sensitive** | **kept** with their text (readable by admins through `history`/`export`), the audit row says `revisionsKept: true` |
  | other | text blanked in every revision (`revisionsKept: false`) |

  A scope is audit-sensitive when the team setting `auditSensitive` is `always`, or (`auto`, the default) when the run, or
  any run of the session, has ever had a high-impact approval. An admin removes someone else's comment with moderation,
  not `DELETE`. Replies to a deleted comment stay. A deleted comment cannot be restored (`409 COMMENT_DELETED`).

## 8. Promote: the explicit step to agent guidance (directive 004, 005)

`POST .../comments/{id}/promote` body `{"action": "send_as_guidance", "expectedVersion": N, "runId"?, "reason"?}`.

* Needs `steer` (admin or engineer; viewer and guest 403 `ROLE_DENIED`) and the **explicit `action`** (missing or other value =
  422). `expectedVersion` must equal the comment's current version, so the promoter sends what they read, not a text that was
  edited in between (`409 STALE_COMMENT_VERSION`).
* It calls `collab.redirect(kind='guidance', content=<comment body>, reason='comment:<id>', requestId='promote:<id>')`: the
  **same path as `POST runs/{id}/redirect`**. The promoter must be a joined, non-observer participant of the run (`403
  NOT_PARTICIPANT` otherwise) and the control-lease rule applies: if someone else holds a timed lease the guidance is
  **queued** for the holder (`queued: true`, `instructionId`, visible in `GET control`) and reaches the agent only when
  the holder accepts it (`control/decide`).
* Run scope promotes into that run. Session scope needs `runId`, a run **linked to the session** (else `422 BAD_RUN`, the
  same for unknown and foreign runs).
* **Idempotent**: one promotion per comment (`comment_promotions` primary key) and a deterministic request id, so a retry (or
  two simultaneous calls, or a retry after a crash between the two writes) returns the first one with `duplicate: true`
  and never sends a second instruction. Hidden, deleted and expired comments cannot be promoted.
* **The link is recorded**: `comment_promotions(comment, run, promoted_by, promoted_at, comment_version, event_seq,
  instruction_id)`, shown on the comment as `promotion`, an audit row `comment.promoted` (ids only), and the guidance event
  carries `reason: "comment:<id>"`. Editing the comment afterwards does not change what was sent.

## 9. Moderation (directive 006)

`POST .../comments/{id}/moderate {action: hide|restore|delete, reason}` (admin only, reason 3..500 characters, 422
`REASON_REQUIRED`). `hide` makes the text invisible to everyone except the author and admins (the list still shows the
comment with `state: "hidden"`, who hid it, when and why); `restore` undoes it; `delete` is the tombstone of section 7 with
`via: moderator`. Repeating the same action is a no-op (`changed: false`, no audit). Hidden comments take no replies,
reactions, edits or promotion.

## 10. Search (directive 009)

`GET /api/v1/orchestrator/discussions/search?q=&scopeKind=&limit=&before=`: case-insensitive substring over comment text
(`q` 2..100 chars; `%`, `_` are literals), newest first, keyset cursor `before=<comment id>`. Only **authorized scopes**:
runs of the caller's team (when the pilot allowlist admits the team) and sessions where the caller is an active member
(admins: every session of the team, as in the guard). Hidden, deleted, purged and past-retention comments never match. Results carry
ids, author, a 200 character snippet around the first match and dates; **no session titles**. Limit: a sequential `LIKE`
scan (no index); fine for pilot volumes, move to a trigram/full text index before large tenants. "Session history" in
the sense of event/plan/audit search is part 50, not here; this endpoint searches discussion.

## 11. Export and retention (directive 010)

**Export**: `GET {scope}/{id}/comments/export` (admin or engineer; policy `export`). Own scope and own team only: every
query filters `team_id`, mentions are limited to team members. JSON `harness.discussion.export/1` with every comment
(reactions, mentions, reference, promotion link, moderation) and all revisions, up to 10 000 comments (`truncated`).
Redaction by role: admins also get hidden text and the retained revisions of deleted comments; engineers get what a
reader of the scope sees. Text past retention is never exported. Each export writes `comment.exported` (scope and count).

**Retention**: team setting `retentionDays` (default 365, 0 = keep forever, max 3650) counted from `created_at`.
It is enforced **at read**: a comment past retention is shown as `purged: true` with no text in lists, history, export and
search, even if no job ran. `discussion.purge_expired(factory, team_id=None)` is the physical job: it blanks `comments.body`
and every revision body, stamps `purged_at`, keeps the row, author, timestamps and audit, and writes one `comment.purged`
audit row per scope with a count. It is a function: **nothing schedules it** (wire it into a worker loop or cron).
Tombstones of audit-sensitive scopes are purged by the same rule: retention is the ceiling, audit rows (ids only) remain.

## 12. Settings (directive 007, 010)

`GET /api/v1/orchestrator/discussion-settings` (any role) and `PUT` (admin): `editWindowSeconds` 0..86400,
`retentionDays` 0..3650, `auditSensitive` `auto|always`. Team scoped; omitted fields keep their value; a no-op write is not
audited; a change writes `discussion.settings_changed` with before/after. Defaults apply until a row exists.

## 13. Events and audit

* Run scope: `comment.created|edited|deleted|hidden|restored|promoted` collaboration events (ids only, never the text) so
  peers refresh over the push socket and `collab-history`. Session scope has no event stream; clients poll the list.
* Audit (section4 raw SQL, `target_type` `agent_run`/`session`/`team`, detail = ids and before/after states, never text,
  scrubbed of secret-shaped keys): `comment.created|edited|deleted|hidden|restored|promoted|exported|purged`,
  `discussion.settings_changed` (all reserved names) and `authz.denied` for refused roles/authors/participants.
  Validation refusals (409/422) are not audited. `redirect.recorded` is written by `collab.redirect` for a promote.

## 14. Routes (prefix `/api/v1/orchestrator`, `{scope}` is `runs` or `sessions`)

`GET|POST {scope}/{id}/comments`; `PATCH|DELETE {scope}/{id}/comments/{commentId}`; `GET .../{commentId}/history`;
`POST .../{commentId}/(reactions|promote|moderate)`; `GET {scope}/{id}/comments/export`; `GET discussions/search`;
`GET|PUT discussion-settings`. Errors use `{"detail": {"error": {"code", "message"}}}` plus `currentVersion` /
`editWindowSeconds` where noted. Wiring: `app.include_router(discussion.router)` and
`discussion.configure_discussion(service)`.

Codes: `ROLE_DENIED` 403, `NOT_AUTHOR` 403, `NOT_PARTICIPANT` 403, `NOT_FOUND` 404, `COMMENT_NOT_FOUND` 404,
`PILOT_NOT_ENABLED` 404, `BAD_BODY` `BAD_PARENT` `REPLY_TOO_DEEP` `BAD_REF` `BAD_PATHS` `BAD_RUN` `BAD_CURSOR` `BAD_QUERY`
`REASON_REQUIRED` 422, `PARENT_UNAVAILABLE` `SCOPE_READ_ONLY` `STALE_COMMENT_VERSION` `EDIT_WINDOW_CLOSED`
`COMMENT_UNAVAILABLE` `COMMENT_DELETED` 409, `DISCUSSION_UNAVAILABLE` 503 (plus the codes of `collab.redirect` on promote).

## 15. Limits (honest)

* SQLite-tested only; the SQL uses `ON CONFLICT DO NOTHING/UPDATE`, partial unique indexes and expanding `IN`, all
  portable, but nothing here has run on Postgres.
* No per-user rate limit or per-scope comment cap on writes (lane R/global limits only). A noisy author can add many comments.
* No attachments; no rich text or markdown rendering rules (the body is plain text, the client must escape); no unread
  counters per thread; no thread-level permissions; session scope has no live event.
* Mentions are by user id; resolving `@name` is the client's job. A person removed from a private session keeps the
  comments they were mentioned in but loses access to the session.
* The purge job is not scheduled; search is an unindexed scan; a comment promoted to guidance cannot be un-sent.
* Audit-sensitivity `auto` looks only at high-impact approvals; handoffs and control changes do not mark a scope.
* Org policy applies through the gate only for org-linked teams.
