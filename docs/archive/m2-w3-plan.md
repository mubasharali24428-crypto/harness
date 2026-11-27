# Marathon 2.0 W3 — safe edits, conflict semantics, checkpoint/rewind (M2-W3)

Follows the 2026-10-28 Marathon 2.0 roadmap, W3 row, and
[the W2 outcome](m2-w2-plan.md). This is **Marathon 2.0 W3**, distinct from the
historical W3 (interrupt/steer/approve/rewind), whose outcome is recorded in
[week-3-plan.md](week-3-plan.md) and whose code is already in the tree.

Governing rule: *an unchecked gate stays open when a dependent week begins.*
W2's gate is checked, so W3 starts with an audit of the control surface and a
deterministic proof of the clauses that do not need a model — not with a rebuild
of what already exists.

## Purpose

Marathon 2.0 W3 asks to **audit the full checkpoint/hunk workflow, define
stale-file detection and conflict semantics, and model concurrent edit
fixtures**. Its gate has five clauses: worker and child stop; partial writes
reconcile; denied actions remain denied; steering cannot expand approved scope;
a stale patch is refused and a rewind is evidenced.

The historical W3 closed the first four with model-driven drills plus unit
tests. This week's job is to (a) name exactly which versioned object carries
"stale" in this product, (b) turn the stale/conflict and rewind clauses into a
deterministic corpus task that runs without a chat model, and (c) state plainly
what is still not proven at the current revision.

## Audit (2026-10-28)

| Area | What exists | Where | Finding |
|---|---|---|---|
| Run intent CAS | `put_intent` compares `expectedVersion` against the latest `run_intent_versions` row; the insert's primary key `(run_id, version)` is the compare-and-swap | `control.py:759-867` | A stale edit is refused, not merged. Whether a conflict **record** is opened depends on field overlap, not on the version alone. |
| Conflict records | `control_conflicts` with `kind`, `base_version`, `proposed`, `conflict_fields`, `state`; `stale` is derived (`base_version < current`) | `control.py:870-929` | Web-visible, resolved by leader/admin only; `keep_current`, `take_proposed`, `merge`. |
| Resolution | Applies the chosen patch through the same CAS; a resolution that changes anything is `strict` and voids pending/approved approvals | `control.py:932-991`, `_apply:784-788` | A conflict resolution cannot silently preserve an approval that was granted against different scope. |
| Plan versions | `plan_versions` with `content_sha256`; writes require `expectedVersion == latest`; edits may only be proposed on the latest open version; accepting a stale edit is `STALE_EDIT` | `plans.py:887-928, 1207-1244, 1317-1370` | A file-like patch object exists at the plan level, with a real content digest. |
| Checkpoints | Manifest + content-addressed blobs under `<workspace_root>/.checkpoints`, outside every run tree; `rewind` restores bytes, removes files created after the snapshot | `run_checkpoints.py:200-346` | Rewind is exact by construction; the store is not readable as ordinary workspace files. |
| Checkpoint routes | `GET /runs/{id}/checkpoints`, `POST /runs/{id}/rewind`; rewind refused while the run's task is live | `execution.py:2345-2370`, `service.py:372-398` | Refusal is a product behaviour with a test, not a comment. |
| Hunk review (sandbox) | `parse_unified_diff` → per-file/per-hunk units; `patch_for`/`build_reverse_patch` rebuild only the selected hunks; selections are a path plus an index into the diff of the same sha, never patch text | `section5/src/harness_sandbox/hunk_review.py` | The server decides what a hunk is, so a caller cannot inject paths or arbitrary patch text; malformed diffs raise instead of silently dropping a change. |
| Steering | `redirect` is an event: `guidance` queues to the holder; `scope_change`/`permission_change` require the leader and invalidate approvals, but **never write the intent** | `collab.py:582-660`, `control.py:494-546` | Scope lives only in `run_intent_versions`; steering cannot widen it. |
| Agent file write | Last-writer-wins inside one run's workspace; no content-hash precondition on the SDK fs tool | engine/SDK tool (not this repo) | There is no file-level stale patch to drill. The stale-patch clause is answered by the versioned documents (intent, plan), and cross-run isolation is W8's row. |
| Historical W3 evidence | stop kill 0.58–1.01 s (4 samples); torn write 28/120 lines, rewind exact, resume attempt 2; steering-scope and rejected-plan tests | `docs/briefs/evidence/pilot-prep/`, `docs/week-3-plan.md` | Real, but from 2026-10-26; the corpus keeps those tasks `blocked` because re-running them needs a chat model. |

### Conflict semantics, stated once

| Situation | Answer | Record |
|---|---|---|
| `expectedVersion` < current, changed fields also moved | `409 INTENT_CONFLICT` with `conflictId`, `currentVersion`, `current` | one open `conflict` |
| `expectedVersion` < current, no changed field moved | `409 STALE_INTENT_VERSION` | none |
| `expectedVersion` > current | `422 BAD_VERSION` | none |
| patch equals current values | `200` with `unchanged: true`, no new version | none |
| writer is not a participant/observer | `403 NOT_PARTICIPANT` | denial audit |
| path is absolute or contains `..` | `422 BAD_PATHS` + isolation metric | none |
| non-leader submits a valid change | `202` queued proposal (`conflictId`, `holder`) | open `proposal` |
| resolution by non-leader/non-admin | `403` | denial audit |
| plan write on a superseded version | `409 VERSION_CONFLICT` + `currentVersion` | none |
| plan edit proposed on a superseded version | `409 VERSION_NOT_OPEN` | none |
| stale plan edit accepted after the plan moved | `409 STALE_EDIT` + `currentVersion` | edit stays `pending`, closable |

## Decisions

1. **"Stale" is defined on the versioned documents, not on file bytes.** The
   product has two first-class versioned objects — run intent and plan content —
   and both already implement compare-and-swap with a content digest. The agent's
   file tools run inside one run's private workspace, where a concurrent writer
   does not exist by design; adding a hash precondition there would be a
   plugin-level change with no demonstrated adversary. Cross-run overwrite is
   W8's concurrency row. This is an explicit **non-claim**, recorded rather than
   implied.
2. **A conflict record means "someone's work would be lost", not "someone was
   slow".** A stale write that touches only untouched fields is refused with a
   re-read instruction and creates no record; a stale write that collides opens
   one, so the list stays a work queue instead of a log of every retry.
3. **Resolution voids approvals.** Any accepted resolution that changes the
   intent runs `strict`, because an approval granted for scope A is not an
   approval for scope B. The task asserts the version/state effects, not the
   prose.
4. **Steering is an event; scope is a CAS write.** No redirect kind mutates the
   intent, so "steering cannot expand approved scope" holds at the seam. The
   out-of-scope *write* remains M03's evidence, and M01 is still open; nothing in
   this week widens access.
5. **Rewind reverts, resume adopts, both only when not live.** Inherited from the
   2026-10-26 decision and re-asserted here: the drill checks exact restoration
   and the live refusal; it does not invent a merge.
6. **No model in the loop for this gate.** The stale/conflict/rewind path is
   deterministic API and storage behaviour; a chat model would add spend and
   variance without adding evidence.

## Slices

1. **Deterministic task** — `scripts/eval/tasks/stale_edit_task.py`: one live run
   (project → plan → join → leader) driven through intent CAS, conflict
   listing/resolution, steering, plan versions, and a checkpoint
   snapshot/mutate/rewind probe inside the orchestrator container. 18 checks,
   verdict from server state and restored bytes.
2. **Corpus** — `stale-edit` moves from `fixture-owed` to `implemented`
   (category `stale_edit`, cost `infra`, no model). Corpus is now 16 implemented,
   6 blocked, 4 fixture-owed.
3. **Current-revision tests** — the W3 and control/plan/checkpoint unit suites.

## Outcome (2026-10-28)

**Task passes 18/18.** Run `w3-stale-1793194464`, wall clock **0.89 s**, no model
spend. Evidence:
`docs/briefs/evidence/eval/w3-stale-edit-1793194464.json` and the runner's
normalized record `docs/briefs/evidence/eval/baseline-20261028T133421Z.json`
(1/1 executed, **revisionVerified true**).

Checks, all observed on the deployed stack:

| Check | Observed |
|---|---|
| stale conflicting intent edit | `409 INTENT_CONFLICT`, `conflictFields: ["goal"]`, one open record, `stale: true` |
| stale non-conflicting intent edit | `409 STALE_INTENT_VERSION`, no record |
| expected version ahead | `422 BAD_VERSION` |
| identical patch | `200 unchanged: true`, version unchanged |
| traversal path | `422 BAD_PATHS` |
| intent after stale attempts | version 1, `goal`, `scopePaths`, `permissions` byte-identical |
| scope_change redirect | `200`; intent still version 1 with the same scope and permissions |
| `keep_current` resolution | version 1, content unchanged |
| `take_proposed` resolution | version 2 with the proposed goal |
| stale plan version write | `409 VERSION_CONFLICT`, `currentVersion: 2` |
| plan content after stale write | version 2, goal `plan two` |
| plan edit on superseded version | `409 VERSION_NOT_OPEN` |
| snapshot → mutate → rewind | `a.txt` back to `v1`, `b.txt` restored, `c.txt` removed, 2 files restored, 1 removed |
| checkpoints on a run with none | `200 {"items": []}` |
| rewind with no snapshots | `409 CHECKPOINT_UNAVAILABLE` |

**Unit suites, current worktree: 91 passed.** `test_control.py` (intent order,
stale rejection, conflict record, simultaneous writers), `test_plans_edits.py`
(stale edit, latest-open-version, self-approval), `test_w3_rejected_stays_blocked.py`,
`test_w3_steering_scope.py`, `test_run_checkpoints.py`,
`test_checkpoint_blob_store.py`.

**Hunk workflow: 11 passed** (`section5/tests/unit/test_hunk_review.py`):
parse/split, selective reverse patch, unknown selection refused, reverting one
hunk leaves the other change, restore-to-snapshot, a second revert of the same
sha is a conflict rather than a silent no-op, empty selection refused, and the
API's writer/team, 422 and 409 paths. This is the sandbox's checkpoint diff
workflow; it is a different store from the run-workspace rewind above and neither
replaces the other.

### Gate position after this week

| Clause | Current revision | Note |
|---|---|---|
| Stale patch is refused | **closed (deterministic)** | intent + plan CAS, conflict records, resolution effects; file-level hash precondition is a declared non-claim |
| Rewind is evidenced | **closed (deterministic)** | component probe in the orchestrator + stored snapshots + live-refusal test |
| Steering cannot expand approved scope | **closed** | redirect leg + `test_guidance_does_not_widen_the_scope_the_engine_is_given` |
| Denied actions remain denied | **closed** | `BAD_PATHS`, `BAD_VERSION`, `NOT_PARTICIPANT`, rejected-plan and role tests |
| Worker and child stop | **historical only** | 2026-10-26, 4 samples; not re-run here |
| Partial writes reconcile | **historical only** | 2026-10-26 torn-write drill; not re-run here |

The two historical clauses are **not** claimed at this revision. Their corpus
tasks (`w3-rewind`, `w3-partial-write`) stay `blocked` because they need a chat
model; the local Ollama has only an embedding model, and the stack model
(`qwen/qwen3.7-flash`) would make them live spend. They re-open the moment that
prerequisite is available or a deterministic stand-in worker lands.

## Limitations

- n=1 for the task; a single pass is an existence proof, not a rate.
- The task uses one user. Multi-user races are covered by unit tests
  (`test_simultaneous_intent_edits_one_wins_the_other_is_stale`), not by a live
  two-principal run; E2E logins create solo teams, so a second participant needs
  an invite flow.
- The checkpoint probe runs in the orchestrator container against a temporary
  workspace; it proves the store and the restore semantics, not that a *model*
  run produces a checkpoint pair. That wiring is covered by
  `test_run_checkpoints_wiring.py`.
- `rewind` while a run is live is proven by unit test, not by this task (no live
  run).
- No file-level stale patch exists; see decision 1.

## Carry-over

- **W8:** concurrent isolated runs and overlapping edits between runs; prototype
  hash-anchored patching only if that row finds silent overwrite. The conflict
  API and records already exist for it.
- **M01:** confinement proof is still open; this week adds no reach.
- **Prerequisite:** a local chat model (or a deterministic stand-in worker) to
  re-run the stop and torn-write drills at the current revision.
