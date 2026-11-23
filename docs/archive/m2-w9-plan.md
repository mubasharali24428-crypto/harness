# Marathon 2.0 W9 — artifact authorization, preview/share, deployment binding, visual evidence (M2-W9)

Follows the 2026-10-28 Marathon 2.0 roadmap, the W9 row, and
[the W8 outcome](m2-w8-plan.md). The share/deploy half already had a live drill
(`scripts/e2e/w9_share_deploy_drill.py`, `pilot-prep/w9-share-deploy-*.json`);
this week adds the missing visual semantics and proves both halves at the
current revision.

Governing rules: *an unchecked gate stays open when a dependent week begins*,
*evidence over claims*, and *deny when a boundary cannot be enforced*.

## Purpose

W9 asks for:

1. **artifact authorization** — share links are capabilities, revocation and
   expiry are enforced, no registry leaks the token;
2. **preview/share** — a shared file downloads, never renders;
3. **deployment approval bound to the reviewed revision** — approvable only for
   the digest and head the reviewer saw;
4. **sanitized visual artifact provenance** — which revision a screenshot shows;
5. **retention and tenant isolation** for visual artifacts;
6. **before/after comparisons** on a fixture app with accessible and canvas
   controls; screenshot artifacts respect size/dimension limits and access.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Share as capability | live proof: mint → anonymous download → revoke → 404 → expiry 410; list carries no token | `w9_share_deploy_drill.py` (21 checks) |
| Download, never render | route always serves `application/octet-stream` + `CSP sandbox` + `nosniff` + `X-Artifact-Sha256` | `section4/.../shares.py` |
| Deployment binding | approval echoes the reviewed `changesDigest`; a moved head makes it `409 DEPLOYMENT_STALE` naming `runHeadSha`; revoke is terminal | `section4/.../deployments.py` |
| Visual artifacts | **missing.** `artifacts.kind` has no visual kind; nothing records dimensions, provenance, pairing or a revision snapshot | — |
| Visual limits | **missing** for uploads; the browser gate caps its own renders at 4 MiB but an HTTP upload had no raster check | `section5/.../browser_gate.py` |
| Fixture app | **missing** — W10's drill uses a page the agent edits; W9 needs a deterministic before/after fixture with accessible + canvas controls | — |

The temptation was to treat "a screenshot is an artifact" as the whole answer.
It is not: an artifact says *bytes*, not *which revision those bytes show*.

## What changed

### Migration 0065 — `visual_artifacts`

`artifact_id` (PK), `team_id`, `run_id`, `role` (`before|after`), `pair_id`
(NULL for a before, required for an after), `width`, `height`, `provenance`
(JSON text), `created_by`, `created_at`. A partial unique index allows **one
after per before**; a check requires the pairing shape. Visual rows hang off
the existing artifact store, so content, sharing, retention and audit are the
same code paths — there is no second blob store.

### Visual API (`section4/src/harness_orchestrator/visuals.py`)

* `POST /runs/{runId}/visuals?name=&role=&pairOf=&capturedFrom=&retentionDays=&requestId=`
  — validates the image from its **magic bytes** (PNG/JPEG/WebP), parses
  dimensions from the header, then stores it through `create_artifact`.
* `GET /runs/{runId}/visuals` — items + resolved `pairs`; `GET .../visuals/{id}`.
* Provenance is _not_ client-supplied: it is read at attach time from
  `run_git_state` (head, base, repo, stale), the latest **approved**
  `plan_versions` version and the run's current `changes_digest`. A run with no
  recorded head is refused (`409 VISUAL_REVISION_UNBOUND`).
* An after must name a before of the same run, available, with an identical
  provenance snapshot (`409 VISUAL_PAIR_REVISION_MISMATCH`), and only one after
  per before (`409 VISUAL_PAIR_TAKEN`; a lost insert race tombstones its own
  artifact rather than leaving a fake row).
* A retried `requestId` returns the visual it already recorded
  (`200 {replayed: true}`); it is never re-inserted and never tombstoned.

### Fixture app (`scripts/e2e/fixtures/w9_app.html`)

A settings panel with a `<button aria-pressed aria-controls>` and a labelled
`<canvas role="img">`; `?state=before|after` flips the pressed state and the
canvas colour. Rendered by the product's own browser gate
(`section5/.../browser_gate.py`) inside `docker run --network none --read-only
--cap-drop ALL --security-opt no-new-privileges --user 10001`.

### Drills and tests

* `scripts/e2e/w9_visual_evidence_drill.py` — 33 checks (see Outcome).
* `section4/tests/unit/test_visuals.py` — 12 header/validator tests
  (PNG/JPEG/WebP parsing, SVG/GIF/PDF/truncated, suffix mismatch, 9000-px
  refusal with width/height detail, byte cap, `_same_revision`).
* `section4/tests/unit/test_visuals_db.py` — 11 route tests on sqlite with the
  real 0049/0063/0064/0065 migrations: provenance, pairing, second-after,
  cross-revision pair, non-raster, dimension/byte refusals, foreign team 404,
  retention expiry, request-id replay.
* `w9_share_deploy_drill.py` re-run unchanged at HEAD: **21/21 PASS**, expiry
  leg included (410 `SHARE_EXPIRED` at the 60 s floor).

## Outcome (2026-10-28)

**Visual evidence, live: 33/33 checks PASS.**
`pilot-prep/w9-visual-evidence-1793204951.json`

* Renders: both 640×400, canvas pixel `#b3261e` before / `#1a7f37` after;
  `aria-pressed="false"` → `"true"`, canvas label present in the DOM dump.
* Attach: before 201, after 201 paired; provenance =
  `{runHeadSha: c…c, baseSha: a…a, planVersion: 1, changesDigest: 263abc…}` on
  both; the artifact sha matches the bytes.
* Refusals: SVG/GIF/PDF `415 VISUAL_TYPE_NOT_ALLOWED`; 9000×20
  `422 VISUAL_DIMENSIONS`; a declared 5 MiB body `413 ARTIFACT_TOO_LARGE`
  (answered on the declared length, before the body); after without before
  `422`; second after `409 VISUAL_PAIR_TAKEN`; after paired across a moved head
  `409 VISUAL_PAIR_REVISION_MISMATCH` (stale head `d…d` echoed).
* Isolation: foreign team 404 for list, item and attach.
* Share: anonymous 200 with the exact PNG bytes;
  `application/octet-stream`, `attachment; filename="before.png"`,
  `nosniff`, `sandbox; default-src 'none'`, `X-Artifact-Sha256` = the artifact
  sha; revoke → 404.
* Retention: backdated `retention_until` → list `expired`, content and share
  `410 ARTIFACT_EXPIRED`; `purge_expired` (run in the orchestrator container)
  purged 2, row `deleted`, content `410 ARTIFACT_DELETED`, blob gone.
* Audit: `artifact.uploaded×3`, `visual.attached×3`, `artifact.deleted×1`.

**Share/deploy, live: 21/21 PASS.** `pilot-prep/w9-share-deploy-1793204967.json`
— mint/anon/revoke/expiry as before; a request approved after the head moved is
refused `409 DEPLOYMENT_STALE` with `staleFields: ["runHeadSha"]` and stays
`requested`; a wrong client digest is refused with `["clientDigest"]`.

**Tests: 23/23 PASS** (`test_visuals.py` 12, `test_visuals_db.py` 11).

## Findings

1. **A visual needs its own shape.** `artifacts.kind` has no visual kind and
   nothing could answer "which revision is this screenshot of". The row in
   `visual_artifacts` is what makes provenance, pairing and one-after-per-before
   enforceable in SQL rather than in prose.
2. **The list query had a missing column.** The first live `GET visuals` 500ed:
   the join selected `v.*` fields by name and `_view` read `row.run_id`, which
   the projection did not include. Caught by running the route, fixed by
   selecting `v.run_id`, and now covered by the sqlite route tests.
3. **A retried upload would have destroyed evidence.** `create_artifact` returns
   the previous artifact for a replayed `requestId`; inserting the visual row
   then hit the primary key, and the race handler tombstoned the artifact —
   deleting a legitimate visual. Replays now return the existing row
   (`test_request_id_replay_returns_the_same_visual`).
4. **The byte cap answers before the body.** Sending 4 MiB+1 breaks the client's
   pipe because the server refuses on `Content-Length` and hangs up. The drill
   proves the refusal with headers only (`Expect: 100-continue`, no body)
   instead of mistaking a correct early refusal for a transport bug.
5. **The store's refusals must speak the same envelope.** `_read_body` raises the
   store's `CollabError` (`ARTIFACT_TOO_LARGE`); the visual router now maps it to
   the artifact error envelope instead of letting it become a 500.
6. **Purge needs the module's factory, not just a factory argument.** A one-off
   process must `configure_artifacts(...)` because the blob drop resolves the
   factory through the module; the drill does exactly that, and proves the blob
   is removed (distinct bytes, so deduplication cannot mask it).

## Gate position

| W9 exit claim | State |
|---|---|
| Unauthorized shares fail | **pass** — anonymous is the capability (200 with the token, 404 without/by another team); foreign team 404 on the registry and the item |
| Revocation works | **pass** — same token 404 after revoke; idempotent revoke keeps one timestamp |
| Approval names the reviewed revision | **pass** — `DEPLOYMENT_STALE` names `runHeadSha`/`clientDigest`; binding carries digest, head and plan version |
| Screenshot artifacts respect limits | **pass** — raster-only by magic, 16..8192 px, 4 MiB, refusals before storage |
| Screenshot artifacts respect access | **pass** — same team/run scoping and download-only shares as artifacts |
| Provenance, retention, pairing | **pass** — snapshot at attach, expiry on read, purge tombstone, one after per before |
| Before/after comparisons | **pass** — fixture with accessible button + labelled canvas, paired only within one revision |
| Tenant isolation | **pass** — 404 for foreign list/item/attach |

## Limitations

- **Provenance binds to the run's *recorded* head.** `PUT git-state` is
  caller-recorded (documented in §14 of `docs/artifacts.md`); a false head
  yields evidence of a false revision. `capturedFrom` is opaque text, not a
  verified action log.
- **A screenshot is a claim about pixels, not truth.** The drill samples one
  canvas pixel to prove the change; there is no OCR, pixel-diff baseline, or
  malware scan of image payloads.
- **The fixture render is one path through the gate.** It proves the gate and
  the visual API agree, not that every engine-produced screenshot went through
  either.
- **Retention proof backdates a row directly.** The scheduler that calls
  `purge_expired` on an interval is exercised as a function, not waited for.
- **n=1 per scenario.** Existence proofs, not rates.
- **No UI surface.** The API, share route and audit exist; the console does not
  yet show a visual pair.

## Carry-over

- **W10:** UI bug reproduced, fixed and re-verified with before/after
  screenshots attached to the run (its drill exists; W11 ran the vision task).
- **W11:** current-revision re-run still owed for the live vision task.
- **W12:** DB-backed skill catalog (skills remain advisory-only).
- **W17:** load and saturation for artifact uploads/purges.
- **M01:** native-tool confinement still open; nothing here widened access.
- **UI lane:** render `pairs` in the console before claiming a reviewer-facing
  before/after view.
