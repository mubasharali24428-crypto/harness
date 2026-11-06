# Decision: a delegated scope must name paths that exist in the workspace

**Date:** 2026-11-05
**Status:** PROPOSED — not decided. This is a design note from an engineer with no
decision authority. Nothing here is implemented, and nothing here is an approval
to change the confinement boundary or the security-review scope.
**Decision owner:**

**Recorded by:** hy4 (pure-engineering task B1; code read at HEAD `f5b298a`).
**Related:** [`roadmap-3.0.md`](../roadmap-3.0.md),
[`2026-11-05-intent-permissions.md`](2026-11-05-intent-permissions.md),
[`2026-11-05-w28-child-run-approval.md`](2026-11-05-w28-child-run-approval.md)

---

## The question this settles

The W28 scope-enforcement drill
(`docs/briefs/evidence/w28/scope-enforcement-drill-empty-workspace-FAIL.json`)
delegated a child with scope `src` into a workspace that contained `src` and
`other`, started the child, and then **both** the in-scope write and the
out-of-scope write came back with `result: null` — the child was `blocked` and
wrote nothing. The drill's verdict is `FAIL`. The live finding (recorded in the
batch 1 hand-back and the W28 follow-ups) is narrower than "enforcement is
broken": the engine binds only scope paths that **already exist** in the
workspace, so a delegated child whose scope paths do not exist at launch can
write nothing (fail-closed), and a child whose scope paths do exist is confined
to them. This note decides how the product should treat a scope path that does
not exist, without building it.

## What happens today (every claim cites a file and line I read)

* A delegation mints a child assignment and then opens a child run
  (`delegation.py:78-124`, `_open_child_run`). The scope the caller asked for is
  normalised by `_norm_scope` (`agent_tree.py:179-201`), which accepts any
  non-empty, relative, no-`..` path string (the `paths` clean branch at
  `agent_tree.py:198` uses `rx=None`, so the only checks are the shared ones at
  `agent_tree.py:185-186`: not blank, ≤256 chars, no NUL, no backslash, not
  absolute, no `..`). A leading `./` is accepted (`agent_tree.py:181` strips,
  `:185-186` does not reject `./src`).

* The delegation endpoint refuses a scope with **no** paths
  (`delegation.py:143-145`, `SCOPE_UNBINDABLE`, 422) on the stated ground that
  "an intent without paths is unconfined, so it cannot bind anything". It does
  **not** check that the named paths exist.

* The child run's intent is written as version 1 with `scope_paths` set to the
  assignment's paths (`delegation.py:104-112`, the `INSERT INTO run_intent_versions`
  with `:s` bound to `json.dumps(paths)`). This is the only place the scope is
  bound to the run; tools, permissions and network from the `ScopeBody` are
  recorded on the assignment (`agent_tree.py:527-537`) but **not** copied onto
  the intent (`delegation.py:104-111` writes only `scope_paths`).

* The engine reads the run intent's `scope_paths` via
  `collab.intent_scope_paths` (`collab.py:1178-1195`, a `SELECT scope_paths FROM
  run_intent_versions … ORDER BY version DESC LIMIT 1`) and puts them into
  `shell_policy` as `scopePaths` (`service.py:1620`, building the dict at
  `service.py:1617-1624`).

* `confine.scope_targets` (`confine.py:145-172`) resolves each scope path against
  the workspace root and **drops any path that does not exist** (`confine.py:167`,
  `if not inside or not os.path.exists(real): continue`). If none survive, it
  returns `None` (`confine.py:157-158`), and `None` means "the whole workspace is
  in scope" (`confine.py:150`). But — and this is the fail-closed hinge — a
  `None` scope is then handled by `_fs_write_capability`
  (`engine.py:137-179`): if the intent names a narrower scope and the workspace
  is not mounted read-only, writes are turned **off** (`engine.py:167-173`,
  `return False, "write/edit off: …"`). The read-only-ness is observed, not
  assumed, via `_read_only` (`engine.py:129-134`, `os.statvfs … ST_RDONLY`).

* So the chain for a child whose scope paths do not exist is: `_norm_scope`
  accepts them → the intent records them → `scope_targets` drops them all (none
  exist) → `scope_targets` returns `None` → but `_fs_write_capability` sees the
  intent named a narrower scope (`engine.py:167`, `narrower` is non-empty) and
  the workspace is not read-only → writes are refused. The child is confined to
  nothing: it can read, but it cannot write. That is exactly what the drill saw
  (`scope-enforcement-drill-empty-workspace-FAIL.json`, both writes `result:
  null`, child `blocked`).

* When the scope paths **do** exist, the container path
  (`confine.py:424-472`) mounts the workspace read-only and binds each surviving
  scope target back read-write (`confine.py:466-472`), so the kernel enforces the
  intent for every writer in the worker. This is the M03 design and it is what
  `m03-scope` drilled successfully (STATUS, "The last three pinned tasks now run
  on Haiku": in-scope `src/m03.txt` written, `docs/m03.txt` refused with EROFS).

Net: today a missing scope path is **fail-closed for writes** (the child cannot
write anything), which is safe but useless — the child is silently trapped, and
a human reading the run sees `blocked` with no reason that names the missing
path. The risk is not an escape; it is a silent dead child and a misleading
`SCOPE_UNBINDABLE` message that talks about *no* paths when the real condition
is *no paths that exist*.

## Option A — refuse delegation when a scope path is missing (clear 422)

At `_norm_scope` time (or at the delegation endpoint, `delegation.py:138-145`,
right after the `not scope["paths"]` check), resolve each path against the
workspace root and reject with 422 `SCOPE_PATH_MISSING` if any does not exist,
with the missing path in the message.

* **What it changes:** a new existence check in `agent_tree.py` (or
  `delegation.py`), one new error code, and a test. The front end's scope
  editor (`ScopeRequestEditor.tsx`) would surface the error; no new HTTP route.
  The check needs the workspace root, which the delegation endpoint can get from
  the run (`agent_runs` → `project_id` → workspace), the same lookup
  `_open_child_run` already does at `delegation.py:93-94`.
* **What it costs to test:** unit tests for the 422 on a missing path and for
  the happy path on an existing one (both model-free); one drill that a child
  with an existing scope writes in-scope and is refused out-of-scope (the m03
  drill already proves the happy path). No OS-level confinement change, because
  the confinement itself is unchanged — only the gate in front of it moves.
* **Safest for confinement:** yes. It fails closed *earlier* — before the child
  run is minted, before a plan is proposed, before budget is reserved — so no
  dead child, no reserved budget to release, no `blocked` run for a human to
  triage. It never widens scope; it only refuses. The fail-closed write
  behaviour (`engine.py:167-173`) stays as the backstop for any path that
  vanishes between delegation and start.

## Option B — warn but allow (delegated child runs with a missing path)

Accept the delegation, record the scope as-is, and surface a warning (on the
synthesis view, or on the run) that a scope path does not exist. The child's
writes stay fail-closed via `engine.py:167-173` as today.

* **What it changes:** a new warning path (a field on `/synthesis` or the run
  detail), and an existence check that is advisory, not a refusal. No change to
  `_norm_scope`, `scope_targets`, or the engine.
* **What it costs to test:** a unit test that the warning appears on a missing
  path and is absent on an existing one; a drill that the child is still
  fail-closed for writes (the current FAIL drill already shows this). Cheaper
  than A in code, but it leaves the silent-dead-child UX in place and asks a
  human to notice the warning.
* **Safest for confinement:** no worse than today (writes stay off), but it
  preserves the trap: a child that can never write is still minted, still
  reserves budget, and still needs a human to cancel it. The warning relies on
  someone reading it.

## Option C — create the directory in the workspace before binding

At delegation time, `mkdir -p` each scope path inside the workspace, then bind
as today. The child can then write inside the created directory.

* **What it changes:** a write to the workspace (creating a directory) at
  delegation time, before the child run starts. This is a side effect on the
  parent's workspace from the delegation endpoint, which today writes nothing
  outside the database. It also changes what "scope" means: today it names
  existing paths the child may touch; under C it names paths the system
  *creates* for the child. `confine.scope_targets` (`confine.py:167`) would need
  its `os.path.exists` check relaxed or the `mkdir` done before it.
* **What it costs to test:** unit tests for the mkdir, a test that a created
  directory is bound read-write, and a drill that the child writes inside the
  created dir and is refused outside it. An OS-level confinement drill is needed
  to prove the created directory cannot escape the workspace (the realpath check
  at `confine.py:163,166` still holds, but a symlinked parent could change
  semantics between mkdir and bind).
* **Safest for confinement:** the riskiest of the three. Creating directories
  widens what the worker can write to, and a race between `mkdir` and the
  container's mount setup (if a path is a symlink that resolves outside the
  workspace after creation) could bind an outside path read-write. The
  realpath check at `confine.py:163,166` guards this, but adding a write side
  effect to the delegation path is a change to the confinement boundary's
  invariants and should be a security-review decision, not an engineer's call.

## Recommendation

**Option A (refuse with a clear 422), proposed for the owner to rule on.** It
moves the fail-closed gate from "the child is minted and trapped" to "the
delegation is refused before any child exists", which is strictly earlier and
strictly safer: no reserved budget to release, no dead run, no misleading
`blocked`. It does not widen scope, does not create anything, and does not
change the container mounts or `_fs_write_capability` — the existing
`engine.py:167-173` backstop stays for any path that vanishes between
delegation and start. Options B and C both leave the child minted; B leaves the
trap, and C adds a write side effect to the delegation path that touches the
confinement boundary's invariants. The one thing A does **not** do is let a
child write to a not-yet-existing path; if that is a product need (a delegating
agent wants to scope a child to `src/new_module/` before creating it), it
should come as a separate, separately-approved change that creates the directory
*inside* the container under the engine's own confinement, not from the
orchestrator at delegation time.

**State which option is safest for confinement and why:** Option A is safest.
It fails closed earlier (before the child run is minted), never widens scope,
never creates a path, and leaves the kernel-level confinement
(`confine.py:424-472`) and the write backstop (`engine.py:167-173`) untouched.
Option C is the least safe because it adds a write side effect to the
delegation path, which is a change to the confinement boundary's invariants.

**Not decided by this note:** whether a child should ever be able to write to a
not-yet-existing path (a product question), the exact error code and message
text, and whether the check belongs in `_norm_scope` (shared with spawn) or
only in the delegation endpoint. No owner has ruled; the line above is blank on
purpose.
