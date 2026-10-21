# Artifacts and version control (part 48)

As built by Session 6 lane AR. Code: `section4/src/harness_orchestrator/artifacts.py` (router, `configure_artifacts(service)`,
`purge_expired`), `artifact_store.py` (blob store, name check, content classifier). Schema:
`section1/migrations/0049_artifacts.sql`. Tests: `section4/tests/unit/test_artifacts.py`, `test_artifacts_vcs.py`
(fixture: `test_artifacts_support.py`). Evidence: `docs/briefs/evidence/s6/lane-ar.md`.

Every route is `/api/v1/orchestrator/runs/{runId}/...`, guarded by the shared run guard (`api._authz_dep`): identity from the
authenticated Principal, team ownership of the run, role. A foreign or unknown run answers the same `404 NOT_FOUND` envelope
(`run not found`). Artifact and change ids are looked up inside `(run, team)`, so a foreign, wrong-run or mistyped id is the same
`404 NOT_FOUND` (`artifact not found` / `change not found`). Raw SQL (`text()`), no ORM models. Errors are
`{"detail": {"error": {"code", "message"}}}` with the stable codes in section 12.

| Route | Needs | Notes |
|---|---|---|
| `GET runs/{r}/artifacts` | read | `?state=&kind=&limit=&cursor=`; deleted rows hidden unless `state=deleted` |
| `POST runs/{r}/artifacts` | write (not viewer/guest) | raw body is the file; `?name=&kind=&requestId=&retentionDays=&derivedFrom=&changeId=` |
| `GET\|DELETE runs/{r}/artifacts/{a}` | read \| write (uploader or administer) | metadata \| tombstone |
| `GET runs/{r}/artifacts/{a}/content` | read (quarantined: administer) | attachment download |
| `GET runs/{r}/artifacts/{a}/preview` | read | JSON text only |
| `GET runs/{r}/diff` | read | the done-signal diff as safe JSON |
| `GET runs/{r}/changes` | read | changed files; also records new ones (section 3) |
| `POST runs/{r}/changes/{c}/review` | approve | `{decision, diffSha, note?}` |
| `GET\|PUT runs/{r}/git-state` | read \| write | repo/branch/base facts (section 6) |
| `GET runs/{r}/git-permissions` | read | who may commit, push, PR, merge |
| `POST runs/{r}/merge-approval` | approve | the human merge decision (section 9) |
| `GET runs/{r}/lineage` | read | task to accepted result |

"read / write / approve" are the legacy roles of `api._authz_dep`. The lead's policy gate (`ORCHESTRATOR_RULES`) classifies the same
paths (GET `read`, review and merge-approval `approve`, the rest the `steer` fallback). The two checks that need more than the gate
(administer for quarantined bytes, uploader-or-administer for delete) are made inside the handler with the same policy engine
(section 10).

## 1. Repository, branch, worktree, run workspace (48.001)

What exists in the system today, read from the code, not defined here:

| Thing | What it is | Where it comes from |
|---|---|---|
| Repository | `projects.repo_full_name` (`owner/name`), set by section2 `POST projects/{id}/repo/connect` | existing system |
| Run branch | by convention `agent/<runId>`; `agent_runs.branch` holds it; section2 `pr_open.run_id_from_branch` derives the run from it | existing system |
| Branch and commit creation, draft PR | section2 `POST projects/{id}/branches`, `.../commits`, `.../pulls/draft`; the draft PR is refused while staged changes are unresolved; the opened PR is a `pull_requests` row and `agent_runs.pr_id` | existing system |
| Merge | **never done by Harness**: `section2 client` raises `MergeGuardViolation` ("PRs are never merged by this service"). Merging is a manual GitHub action | existing system |
| Run workspace | a plain per-run directory `<DSH_WORKSPACE>/<runId>` (`engine.run_workspace`, id and symlink checked), seeded by copying `HARNESS_WORKSPACE_SEED` **without `.git`**. Staging keeps `.baseline/<runId>` and `.staged/<runId>` beside it | existing system |
| Worktree | **not used**: the run workspace is not a git worktree and has no `.git`. There is one directory per run id | existing system |

So the relation is: one project is connected to one repository; each run has its own branch (by convention) and its own workspace
directory; a PR is opened from the run branch by section2; a human merges on GitHub.

What Harness did **not** know before this lane and still cannot find out by itself: which commit the run branched from, the current tip
of the base branch, the tip of the run branch. Those are **recorded by a caller** through `PUT git-state` (section 6) and are marked
`"source": "recorded"` in the answer. Nothing in Harness polls GitHub for them. `repo.fullName` is the project's value when the project has one
(`source: "project"`), and a different `repoFullName` in the PUT is refused (`REPO_MISMATCH`); the branch name falls back to `agent_runs.branch`
(`source: "run"`) until a caller records one.

## 2. Artifacts: store, limits, retention (48.004)

Upload is a **raw request body** (no multipart dependency in section4): `POST .../artifacts?name=report.txt` with the bytes as the body.
The `Content-Type` header is kept only as `scan.declaredType`; **the type served is chosen by the server** from the sniffed bytes
(`image/png|jpeg|gif|webp`, `application/pdf`, `text/plain`, `application/json` for `.json` text, otherwise `application/octet-stream`).
The size is checked from `Content-Length` first and again while streaming, so an oversized body is cut off at the cap.

| Limit | Default | Env |
|---|---|---|
| bytes per artifact | 10 MiB | `HARNESS_ARTIFACT_MAX_BYTES` |
| artifacts per run (not deleted) | 200 | `HARNESS_ARTIFACT_RUN_MAX_COUNT` |
| bytes per run (not deleted) | 100 MiB | `HARNESS_ARTIFACT_RUN_MAX_BYTES` |
| retention | 90 days (`retentionDays` 1 to 3650 per upload) | `HARNESS_ARTIFACT_RETENTION_DAYS` |
| name | 1 to 120 chars | |
| preview source / shown | 1 MiB / 64 KiB of text | |
| blob root | `$TMPDIR/harness_artifacts` | `HARNESS_ARTIFACT_ROOT` |

**Store.** Content-addressed: the key is `<team digest>/<sha[:2]>/<sha256>`; it is built only from the hash of the bytes and a digest of
the team id, never from a name or any client text. `artifact_store._path` accepts only that exact shape and checks the resolved path stays under the
root, so there is no traversal. Bytes are namespaced per team (identical bytes in two teams are two blobs and one team's delete never touches the other's).
Writes are atomic (temp file then rename); reads re-hash and refuse corrupted bytes (`410 ARTIFACT_CONTENT_MISSING`). Deleting an artifact removes the bytes only when no other
live row of the team shares them; the row stays as a tombstone (`state: deleted`) so lineage and audit keep their ids.

**Names** (`clean_name`): NFC-normalised, 1 to 120 chars, no `/` or `\`, no control or format characters (so no bidi override, no NUL, no newline), no leading
dot, no trailing dot. Anything else is `422 BAD_NAME`. The name is only ever echoed in JSON and in `Content-Disposition`
(`attachment; filename="<ascii-safe copy>"; filename*=UTF-8''<percent-encoded>`), never used in a path.

**Download** (`/content`): always `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`,
`Cache-Control: private, no-store`, `X-Artifact-Sha256`. The server never serves an artifact inline.

**Preview** (`/preview`): JSON only. Text artifacts (valid UTF-8, no NUL, not quarantined, at most 1 MiB) return `{"previewable": true, "kind": "text", "text": ..., "truncated", "render": "text-only"}`;
the first 64 KiB, control and bidi characters replaced. The string is data: clients must show it with `textContent`. Images, PDFs and any other type return `previewable: false` with
`reason` `NOT_TEXT` or `TOO_LARGE` and are download-only. **There is no inline image or PDF preview** (a deliberate ceiling: an inline renderer is an attack surface).

**Retention.** `retention_until` is set at upload. After it, reads answer `410 ARTIFACT_EXPIRED` and the list shows `state: expired` immediately (computed at read time). The bytes are
removed by `artifacts.purge_expired(factory)`, which the orchestrator runs every `HARNESS_ARTIFACT_PURGE_SECONDS` (default 3600, `0` = off). It tombstones the rows and audits `artifact.deleted` with `reason: retention`.
Delete by request: the uploader or an `administer` holder (`403 NOT_ARTIFACT_OWNER` otherwise); idempotent.

**Idempotency and races.** `requestId` makes a retried upload one artifact (`200` with `replayed: true`; the same id with other bytes or another uploader is `409 REQUEST_ID_REUSED`).
The unique index `(run_id, request_id)` settles concurrent retries (tested with `asyncio.gather`). The quota check is read-then-insert, so two simultaneous uploads can overshoot a
quota by one artifact; the per-artifact cap is exact.

## 3. Changed files bound to run, plan version and review state (48.002)

`run_changes` has one row per `(run, path, patch digest)`: `change_kind` (added, modified, deleted, renamed), `diff_sha` (sha256 of that file's patch text),
`plan_version`, `review_state` (pending, approved, rejected), `reviewed_by`, `reviewed_at`, and `flags` (binary, generated, minified, large, symlink).

**Where rows come from.** The only source of changed files in the system is the run's done-signal diff (`service.get_done_diff`, `diffstore`). `GET changes` parses it and records any
file not yet recorded (derived and deterministic; audited once as `change.recorded`, ids and counts only). Rows are bound to the plan version at the moment they are recorded: the
approved `plan_versions` row if there is one, otherwise the newest version, otherwise `null` (the plan table is lane PL's; a missing table is not an error). `before_sha` and `after_sha` stay
`null`: the diff does not carry blob hashes and nothing else in the system does. A diff path that is absolute, contains `..`, a backslash, NUL or control characters is **not recorded** and is counted in `unsafePaths`.

**Review.** `POST changes/{id}/review` needs `approve`. The caller echoes the `diffSha` they reviewed (`409 CHANGE_STALE` otherwise). The decision is a compare-and-swap on `pending`, so two reviewers racing give
exactly one winner (`CHANGE_ALREADY_REVIEWED`); repeating the same decision is a no-op. **A different patch for the same path supersedes the old row and starts `pending` again**: an approval never carries
over to new content (`409 CHANGE_SUPERSEDED` on the old id). Returning to a previously reviewed patch revives its reviewed row. `changesDigest` (sha256 over the sorted `(path, diffSha)` of current rows) is what merge approval binds.

## 4. Diffs (48.003)

`GET diff` returns JSON (`format: unified-structured`, `render: text-only`), never HTML: per file `path`, `changeKind`, `additions`, `deletions`, `flags`, `diffSha`, and `patch` or `patchOmitted`.
`patchOmitted` is `binary`, `generated`, `minified`, `large` or `response_budget`: such files show statistics only. Rules: binary = a `Binary file(s) ... differ` line or a git binary patch;
generated = vendor/build directories, lock files, `*.min.*`, `*.map`, `*_pb2.py`, `*.pb.go`, `*.generated.*`, or an `@generated` / "do not edit" marker in the first 4000 characters; minified = `.min.` path or an added line over 1000 chars;
large = over 2000 patch lines or 100,000 characters; symlink = mode 120000 (flag only). Patch text has control and bidi characters replaced and lines over 2000 chars cut; the whole response carries at most 200,000 characters of patch (`?limit` up to 500
files, `?offset` to page). Paths that are unsafe are shown as `[unsafe path]` with `pathRejected: true`. The parser follows hunk line counts, so a removed line that reads `-- x` is not taken for a file header. Both git-style and difflib-style
(the staging gate's) diffs parse; limits: a difflib deletion looks like a modification (no `/dev/null`), renames are only recognised from git headers.
Responses carry `X-Content-Type-Options: nosniff`. **No secret redaction** is applied to patch text (it is the agent's diff, visible to the team that already sees the run).

## 5. Stale base (48.005)

`run_git_state.stale` is computed on every `PUT git-state`: stale when both `baseSha` (what the run branched from) and `observedHeadSha` (the latest observed tip of the base branch) are recorded and differ.
`GET git-state` answers `base.state` = `unknown` (one is missing), `current` or `stale`, and when stale `mergeRequirement: "rebase_or_merge_required"`. The first flip to stale writes `base.stale_detected` (run id and both shas);
recording a new `baseSha` after a rebase clears it. Stale blocks merge approval (`BASE_STALE`) and makes an existing approval `outdated`. **Detection depends on a caller recording the observed head**: no component of Harness calls GitHub to look.

## 6. Concurrent branches without shared mutable state (48.006)

Each run has its own row (`run_id` primary key), its own branch by convention, its own workspace directory; writing one run's git state touches no other row (tested). The unique index
`(team_id, repo_full_name, head_branch)` makes two runs of one team on the same head branch of the same repo impossible: the second `PUT` is `409 BRANCH_IN_USE` (race-safe, tested with `asyncio.gather`). The same branch name in another team or repo is allowed.
`PUT git-state` is partial (omitted fields keep their value; there is no way to clear a field) and last-writer-wins within a run; `checked_at` and `updated_by` say who recorded last. Branch names and shas are validated (`422 BAD_GIT_STATE`): a branch matches `[A-Za-z0-9._][A-Za-z0-9._/-]*`, no `..`, `//`, `@{`, trailing `/`, `.` or `.lock`; a sha is lowercase hex, 40 or 64 chars.

## 7. Who may commit, push, open a PR, merge (48.007)

`GET git-permissions` answers for the caller and for the run's participants (`run_participants`, up to 50). Each person gets `commit`, `push`, `openPullRequest` (all need `steer`), `approveMerge` (needs `approve`, humans only) and
`merge` (needs `approve` **and** no blocker). It asks the org policy engine (`policy_enforce.decide_request`, the evaluation the gate middleware uses, with the run in the resource chain so run-level rules apply) when the team belongs to an org; teams without an org or policy tables use the role defaults
of `policy_engine.ROLE_DEFAULTS`; any other failure of the policy store denies. `blockers` (shared by everyone) are `GIT_STATE_MISSING`, `BASE_STALE`, `CHANGES_NOT_RECORDED`, `CHANGES_REJECTED`, `CHANGES_NOT_REVIEWED`,
`MERGE_APPROVAL_REQUIRED`, `MERGE_REJECTED`, `MERGE_APPROVAL_OUTDATED`. Agent members never hold `approveMerge` or `merge`.

**This is an answer, not an enforcement point.** Commit, push and PR opening happen in section2 (its own checks, plus the staged-approval gate on the draft PR) and Harness never merges (section 1). Wiring section2 to consult this answer, and a GitHub
branch-protection rule that requires the approval, are not done (section 14).

## 8. Roles at a glance

| Action | viewer / guest | engineer | admin (administer) |
|---|---|---|---|
| list, get, download clean, preview, diff, changes, git-state, git-permissions, lineage | yes | yes | yes |
| upload, record git state | no (`ROLE_DENIED`) | yes | yes |
| delete an artifact | no | own uploads | any |
| download a quarantined artifact | no | no (not even the uploader) | yes |
| review a change, decide a merge | no | yes (human) | yes |

With an org policy, the rows follow the policy engine for the permission named in section 7 and 10 instead of the role.

## 9. Human merge approval, separate from plan approval (48.008)

`POST merge-approval` (`approve`) writes a row in `merge_approvals`; plan approval is a different record (`plan_versions` / `agent_plans`) and nothing in this lane reads one to decide the other:

- a plan approver is not thereby a merge approver: `git-permissions` still lists `MERGE_APPROVAL_REQUIRED` until someone posts a merge approval (tested with an approved plan present);
- recording a merge approval changes no plan row (tested), and an agent member is refused (`403 AGENT_CANNOT_APPROVE_MERGE`, audited as `merge.refused`);
- the same person may hold both permissions (no four-eyes rule is enforced between plan and merge approvers; if the team wants that, it is a policy rule: grant `approve` to different people at run level).

To approve, every current change must be `approved`, git state must be recorded with a fresh base, `changesDigest` must equal the digest of the current changes (`CHANGES_CHANGED`) and `headSha` must equal the recorded `runHeadSha`
(`HEAD_MISMATCH`; if no run head was recorded, any valid `headSha` is bound as given). Refusals write `merge.refused` with the code only. The approval is bound to `headSha`, `baseSha`, `changesDigest` and the plan version in force. It becomes **`outdated`** automatically (no row is changed) when
the head, the base (stale) or the changes move, and then merge is blocked again. Decisions are append-only rows: a later `rejected` supersedes an `approved` (`MERGE_REJECTED` blocker). `requestId` makes a retry one row (`200`, `replayed: true`).

## 10. Scanning and restriction of untrusted content (48.009)

`artifact_store.classify` returns `verdict: clean | quarantined`, `reasons`, `sniffed`. **Quarantined** (state `quarantined`, audited `artifact.quarantined` with the reasons) when any of:

| Check | Examples |
|---|---|
| executable magic bytes | PE (MZ with a PE header), ELF, Mach-O / Java class (CAFEBABE), WASM, OLE compound |
| archive magic bytes | zip (so jar, docx, xlsx, apk), gzip, bzip2, xz, 7z, rar, tar (ustar) |
| script | `#!` shebang |
| executable / script / archive / active extension (any segment, so `x.pdf.exe` counts) | exe dll so dylib jar apk msi; sh bash zsh bat cmd ps1 vbs js mjs py pl rb php; zip tar gz 7z rar iso dmg; html htm svg xhtml |
| active markup in the first 4 KiB of a text file | `<!doctype html`, `<html`, `<script`, `<svg`, `<iframe`, `<?php`, `<body`, `<object`, `<embed` |
| claimed type that the bytes contradict | `.png/.jpg/.gif/.webp/.pdf` whose magic does not match |

Quarantined bytes are stored (so an administrator can inspect them) but: listed with metadata to everyone on the run; **downloadable only by an `administer` holder** (everyone else, including the uploader: `403 ARTIFACT_QUARANTINED`, audited as `authz.denied`;
another team's administer holder: `404`); served as `application/octet-stream` attachment; **never previewable**, even by an administrator (`409 PREVIEW_UNAVAILABLE`). Nothing is executed, unpacked or rendered anywhere. `administer` is decided by the policy engine as in section 7 (org policy when present, otherwise the `admin` role).

Honest limits: this is a static filter, not an antivirus. It does not unpack archives, detect macros in office documents or PDFs, exploit payloads inside allowed image formats, or malware in plain-text scripts that evade the extension and markup checks. Source files (`.py`, `.js`, `.sh`) are quarantined by design: carry code through the run's diff and changes, not as artifacts, or have an administrator
download them. Secret scanning of uploads is not done.

## 11. Lineage (48.010)

`GET lineage` returns `nodes`, `edges`, `stages` and `missing`. The chain is task request (`task_specs`) to plan versions (`plan_versions`, `planned_as`) to changes (`implemented_by` from the version each change is bound to, `changed_by` from the task if unbound) to review
(`reviewState` on the change) to the human-accepted completion (`run_completion.human_state = accepted`, `accepted_in`) to the merge approval (`merge_decided_in`, `covered_by`); artifacts hang off the task (`produced`) and carry explicit edges stored in `artifact_lineage` when uploaded with
`derivedFrom` (artifact of this run) or `changeId` (change of this run): `derived_from`, `evidenced_by`. The stages are `task, plan, approvedPlan, changes, changesReviewed, completionAccepted, mergeApproved`; `complete` is true only when all are; whatever is not present
is listed in `missing`, never invented. Tables owned by other lanes (`task_specs`, `plan_versions`, `run_completion`) are read if deployed and skipped if not. An agent-reported completion (`human_state` not `accepted`) leaves `completionAccepted` missing. Nodes carry ids, states, digests and file names, never task or plan text.

## 12. Stable error codes

`NOT_FOUND` (404, run/artifact/change/diff), `ROLE_DENIED` (403), `PILOT_NOT_ENABLED` (404), `BAD_NAME`, `BAD_REQUEST_ID`, `EMPTY_ARTIFACT`, `BAD_REFERENCE`, `BAD_REVIEW`, `BAD_GIT_STATE`, `EMPTY_UPDATE`, `REPO_MISMATCH`, `BAD_MERGE_APPROVAL` (422),
`ARTIFACT_TOO_LARGE` (413), `ARTIFACT_QUOTA_EXCEEDED`, `REQUEST_ID_REUSED`, `PREVIEW_UNAVAILABLE`, `CHANGE_STALE`, `CHANGE_SUPERSEDED`, `CHANGE_ALREADY_REVIEWED`, `BRANCH_IN_USE`, `CHANGES_CHANGED`, `HEAD_MISMATCH`,
`GIT_STATE_MISSING`, `BASE_STALE`, `CHANGES_NOT_RECORDED`, `CHANGES_REJECTED`, `CHANGES_NOT_REVIEWED` (409), `ARTIFACT_QUARANTINED`, `NOT_ARTIFACT_OWNER`, `AGENT_CANNOT_APPROVE_MERGE` (403),
`ARTIFACT_DELETED`, `ARTIFACT_EXPIRED`, `ARTIFACT_CONTENT_MISSING` (410), `ARTIFACTS_UNAVAILABLE` (503). Request-shape errors (an unknown `kind`, a bad `limit`) are FastAPI's standard 422.

## 13. Audit

Rows are appended in the same transaction as the change, `actor_kind human`, detail = `userId`, `teamId` and ids / states / digests / counts only, passed through the secret-key scrub: no file content, no file names, no notes. Actions used (all in the reserved list):
`artifact.uploaded`, `artifact.downloaded` (every content download, including administrator downloads of quarantined bytes), `artifact.deleted` (request or retention), `artifact.quarantined`, `change.recorded`, `change.reviewed`, `base.stale_detected`, `merge.approved`, `merge.refused`
(a rejection decision, or a refusal with its code), plus `authz.denied` for role and quarantine denials through the shared denial audit.

## 14. Honest gaps and ceilings

- **The artifact store is process-local disk** (same as `diffstore`): bytes live under `HARNESS_ARTIFACT_ROOT` of one container, survive a restart of that process only if the directory is kept, are not shared between replicas and are not backed up. The `artifacts` table is durable and a missing blob answers `410 ARTIFACT_CONTENT_MISSING`. A multi-replica or durable deployment needs a shared volume or an object store behind `artifact_store.put/get/remove` (the three functions are the seam).
- No GitHub is contacted: base/head shas are caller-recorded; stale detection is only as fresh as the last `PUT git-state`. Nothing calls the endpoint automatically yet.
- Merge approval is a Harness record, not a GitHub gate: section2 never merges and no branch-protection rule consumes the approval.
- `git-permissions` is not enforced on section2's commit / branch / PR routes.
- Changed files come only from the done-signal diff: a run that never signals done has no changes; `before_sha` / `after_sha` are not filled; renames outside git headers look like add + delete.
- Reading `GET changes` writes (derived rows and one audit row, atomically): a viewer's read can record changes. Acceptable because the rows are a pure function of the stored diff.
- Soft quota under concurrency (section 2). Orphan blobs can remain when an upload loses a race after writing bytes; there is no blob garbage collector beyond delete and `purge_expired`. A delete racing an upload of the same bytes in the same team can leave the new row without bytes (answers `ARTIFACT_CONTENT_MISSING`).
- `purge_expired` runs on an interval; on PostgreSQL each tick is leader-elected by an advisory lock (`schedule.leader_only`), so one
  replica runs it and the others skip that tick. It is also idempotent.
- No malware scanning, no archive inspection, no secret scanning, no inline image/PDF preview (sections 2 and 10).
- Postgres-only behaviour is mostly untested here (sqlite): since Session 11 `test_session6_live_pg.py` passes on PostgreSQL 18 for an upload and its request-id replay, but partial unique indexes, `begin_nested` savepoints around optional-table reads and the other unique-violation paths still have no PostgreSQL test of their own.
- The UI (lane X) and the OpenAPI / SDK files are not part of this lane.
