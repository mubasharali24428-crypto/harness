# Decision: what the run intent's `permissions` field means

**Date:** 2026-11-05
**Status:** PROPOSED — not decided. This is a design note from an engineer with no
decision authority. Nothing here is implemented, and nothing here is an approval
to change the approval boundary or the security-review scope.
**Decision owner:**

**Recorded by:** hy4 (pure-engineering task B1; code read at HEAD `f5b298a`).
**Related:** [`roadmap-3.0.md`](../archive/roadmap-3.0.md),
[`2026-11-05-w28-child-run-approval.md`](2026-11-05-w28-child-run-approval.md)

---

## The question this settles

The run intent has three editable fields (`control.py:40`,
`FIELDS = ("goal", "scopePaths", "permissions")`). Only `scopePaths` is enforced.
`permissions` is stored, versioned, shown to people, and used to void approvals —
but no execution path reads it. Either give it a meaning with an enforcement path,
or stop presenting it as one.

## What the field holds today (every claim cites a file and line I read)

* It is a sorted set of short lowercase tokens, at most 50 of them. `_PERM` at
  `control.py:41` is `^[a-z][a-z0-9_.:-]{0,63}$`; `MAX_PERMISSIONS = 50` is at
  `control.py:38`; `_norm_patch` at `control.py:661-666` validates each token,
  de-duplicates and sorts, and answers 422 `BAD_PERMISSIONS` on a bad one. The
  error text suggests `fs.delete` (`control.py:665`), but **no vocabulary is
  defined anywhere** — the regex is the only constraint.
* The empty intent is `{"version": 0, "goal": "", "scopePaths": [],
  "permissions": []}` (`control.py:42`). Delegated child runs are created with the
  literal string `'[]'` for permissions (`delegation.py:104-109`).
* It is persisted as JSON in `run_intent_versions.permissions`: the column list is
  at `control.py:682`, the INSERT binding `json.dumps(new["permissions"])` is at
  `control.py:772-778`, and `_snap` reads it back with `json.loads(r.permissions)`
  at `control.py:673-679`.
* It is exposed on the wire. `IntentBody.permissions` is a `list[str] | None` at
  `control.py:1037-1042`; the PUT route at `control.py:1117-1132` builds the patch
  from `("goal", "scopePaths", "permissions")` (`control.py:1126`); `_public` at
  `control.py:712-714` includes it in the GET response started at `control.py:717`.
* It is editable by a person in the front end: `section7/app/projects/[projectId]/runs/[runId]/intentSection.tsx:24`
  seeds a `perms` textarea, `:39-40` labels it "Permissions (one per line)", and
  `:34` sends `permissions: lines(perms)` on save.
* Changing it **voids approvals and asks again**: `control.py:784` is
  `voids = strict or "scopePaths" in changed or "permissions" in changed`, which
  calls `collab._invalidate` at `control.py:786-788` and reports
  `invalidatedApprovals` / `reviewRequired` at `control.py:807-809`. This is the
  only runtime *effect* the field has today.
* It is **not** in the execution policy. `service.py:1617-1624` builds
  `shell_policy` from exactly `forbiddenPaths`, `scopePaths` and `maxToolCalls`;
  `scopePaths` comes from `collab.intent_scope_paths` at `service.py:1620`, whose
  whole query is `SELECT scope_paths FROM run_intent_versions … ORDER BY version
  DESC LIMIT 1` (`collab.py:1178-1195`). `engine.py:209-215` builds the SDK
  overlay's `shell` dict from `gate`, `maxToolCalls`, `forbiddenPaths` and
  `scopePaths` only. The Section 5 gate reads `scopePaths` at
  `section5/src/harness_sandbox/shell_gate.py:144` and `:155`, and nothing named
  permissions. A grep for `permissions` across `section4/src/*.py` finds, outside
  `control.py`, only a different concept (the agent-tree scope, below), comments,
  and unrelated readiness/artifact routes.
* A **separate** permissions concept already exists for delegated agents:
  `AGENT_PERMISSIONS = ("read", "comment", "steer")` at `agent_tree.py:58`, with a
  closed-vocabulary check at `agent_tree.py:191-194` and a `ScopeBody` that also
  carries `tools`, `paths`, `network` and `credentialRefs`
  (`agent_tree.py:170-176`). That vocabulary is enforced for agent assignments
  (`agent_tree.py:441`, `:773`); it is **not** the run intent column, and the two
  must not be silently merged.
* Native file tools are confined by the container's mounts, not by the shell
  policy (`engine.py:218-220`). Any "deny a tool" meaning for this field would
  have to reach those mounts too, not just `harness-shell.js`.

Net: today `permissions` is a free-form, unenforced label that nevertheless
makes a person re-approve a plan. That mismatch is the risk, not a missing
feature.

## Option A — enforce a tool allow-list

Give the tokens a closed vocabulary (e.g. `fs.write`, `shell`, `web`,
`browser`, `image`), and have the engine refuse to mount any tool whose token is
absent. Reuse the agent-tree precedent (`agent_tree.py:58`, `:191-194`) rather
than inventing a third vocabulary.

* **What it changes:** a new closed enum + validation in `control.py:661-666`;
  a new field in `service.py:1617-1624`'s `shell_policy`; a new entry in
  `engine.py:209-215`'s overlay; `harness-shell.js` and the other `dsh_plugins`
  to honour it; and `confine.py`/the worker mounts for native file tools, because
  `engine.py:218-220` says those bypass the shell gate. The front end
  (`intentSection.tsx:39-40`) would become a real checkbox set, not a textarea.
* **What it costs to test:** unit tests for token validation, versioning and
  approval invalidation (reusing `control.py:784`'s path); unit tests for the
  policy patch and each plugin; a Section 5 gate test; and an OS-level
  model-free drill (the shape of `scripts/e2e/m01_native_edit_drill.py`) proving
  a denied native tool cannot reach the filesystem from inside the container.
  Highest cost: it creates a second enforcement boundary that must be proven
  for **every** tool, and a gap in any one plugin is a silent escape. It also
  needs a person to rule on the vocabulary and on security-review scope.

## Option B — make it one narrow switch: network off

Interpret the field as a single boolean-ish token (e.g. `network.off`) and
enforce only that. `engine.py:198-200` already mounts `harness-web.js` behind the
Section 5 web gate, so there is one existing choke point.

* **What it changes:** a closed one-token vocabulary in `control.py:661-666`;
  a policy key in `service.py:1617-1624` and `engine.py:209-215`; the web plugin
  to fail closed when the token is present; and a matching front-end control.
  Everything else about the field stays.
* **What it costs to test:** validation/versioning tests, one policy-patch test,
  one web-plugin test and one drill that `web_fetch` fails closed. Cheaper than A
  because there is a single enforcement point, but it still leaves the other
  tokens meaningless and does not address native tools (`engine.py:218-220`).
  It also quietly turns a general "permissions" field into a network switch,
  which the name will keep lying about.

## Option C — retire the column and say so in the API

Declare that `permissions` has no enforcement semantics, stop accepting edits to
it, and keep historical rows readable for audit. This is the honest reading of
the code as it stands.

* **What it changes:** `IntentBody` (`control.py:1037-1042`) and the PUT patch
  (`control.py:1126`) drop the field (or accept and ignore it with a documented
  deprecation); `_public` (`control.py:712-714`) marks it deprecated and
  read-only; `control.py:784` stops voiding approvals for a change that cannot
  happen; the front end removes the textarea at `intentSection.tsx:39-40`;
  `delegation.py:104-109` can keep writing `'[]'`. No migration is needed — the
  column stays for old versions. If a real second boundary is wanted later, it
  arrives as a new, separately named and separately approved field.
* **What it costs to test:** API contract tests that PUT rejects/ignores the
  field and GET still returns historical values; a test that editing goal/scope
  still voids approvals while permissions does not; a front-end test that the
  textarea is gone; and a docs test. No OS-level confinement test is needed,
  because nothing new is enforced. Lowest cost, and it removes the misleading
  affordance rather than adding an unproven one.

## Recommendation

**Option C (retire), proposed for the owner to rule on.** The field's only real
effect today is `control.py:784`, where it forces a fresh approval; a person
would reasonably read that as a security boundary, but `service.py:1617-1624`,
`engine.py:209-215` and `section5/src/harness_sandbox/shell_gate.py:144,155`
show there is none. Options A and B both ask engineering to build a second
enforcement boundary — including native tools that bypass the shell gate
(`engine.py:218-220`) — which is a person-owned security-review decision, not a
code cleanup. Retiring first makes the API honest and leaves the door open: if a
person later rules that a tool allow-list is wanted, it should be a new field
with a closed vocabulary, its own enforcement path, and its own drill — not a
reinterpretation of the free-form tokens already stored in old intent versions.

**Not decided by this note:** whether any second boundary is wanted at all, its
vocabulary, and its security-review scope. No owner has ruled; the line above is
blank on purpose.
