# Marathon 2.0 W6 — governed skills prototype, Hermes prerequisites (M2-W6)

Follows the 2026-10-28 Marathon 2.0 roadmap, W6 row, and
[the W5 outcome](m2-w5-plan.md). This is **Marathon 2.0 W6**, distinct from the
historical W6 (CLI completion + Hermes H0–H2), whose CLI plan is in
[week-6-plan.md](week-6-plan.md).

Governing rule: *an unchecked gate stays open when a dependent week begins.*
W5's contract is checked; W6 adds the first governed extension surface and
audits the Hermes claims rather than repeating them.

## Purpose

W6 asks for:

1. **reviewable skills as instruction bundles** — manifest, provenance, pinning,
   team scope, explicit enable/disable/removal, and the rule that an unreviewed
   skill cannot execute code or change policy;
2. **H0–H2 Hermes prerequisites**, with claims blocked until rights and external
   artifacts are verified;
3. a **CLI end-to-end gate and reconnect** pass.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Product skills | **none.** No skills table, API, store or prompt block; the runtime ships `dsh-skill*` / `dsh-hooks-*` but none is configured (W4 inventory) | — |
| Hermes LICENSE | **exists**: proprietary, "all rights reserved", dated 2026-10-25 | `/Users/user/Compiler_Project/LICENSE` |
| Hermes authorship | **45/45 commits** by Mubashar Ali; README names one author, no teammates | `git log`, README |
| University student-IP policy | **not in evidence** | — |
| Teammate acknowledgment | **not in evidence** (README no longer claims teammates) | — |
| H1 diagnostic protocol | **implemented** in the sandbox parser/SDK/UI; the differential gate is **not yet a review gate** | `docs/tool-diagnostics-protocol.md` |
| H2 toolchain | `hermesc` is **not** in the sandbox image; no tree-sitter grammar | — |
| CLI end-to-end | parity drill exists; last run W5 | `scripts/e2e/cli_parity.py` |

### H0–H2 position

| Prerequisite | State | Reason |
|---|---|---|
| H0 rights | **partially cleared** | The licence decision is made and authorship is single. The university's student-IP position and any written teammate acknowledgment are still missing, and the roadmap says H0 blocks everything. |
| H1 designs | **half adopted** | The `--json` diagnostic contract is live. The differential review gate is specified but not wired into review evidence. |
| H2 toolchain | **blocked** | Requires H0 clearance and an image change; no claim is made that the agent can work in `.hms`. |

No Hermes code was copied, no `.hms` support was claimed, and nothing in the
product now depends on Hermes. That is the honest state the roadmap asked for.

## The skills prototype

`section4/src/harness_orchestrator/skills.py` — file-backed, stdlib-only.

```
<root>/<team>/<name>/skill.json    manifest + contentSha256
<root>/<team>/<name>/state.json    status, enabled, reviewedBy, pinnedSha256
```

**Manifest.** Strict key allow-list: `schemaVersion`, `name`, `version`,
`description`, `instructions`, `provenance` (source/author/createdAt), `teamId`,
`contentSha256`. There is no field for a command, script, hook, env, tool or
permission — an unknown key is refused, which is the capability guard.

**Pinning.** `contentSha256` covers the canonical manifest including the
instructions. `review()` records that digest in `state.json`; `load_reviewed()`
returns a skill only when the manifest's digest still equals the pin. Editing a
reviewed bundle excludes it until it is reviewed again.

**Team scope.** The loader walks exactly `<root>/<team>`. A manifest whose
`teamId` differs from its directory is refused; a symlinked skill directory is
never followed. There is no cross-team lookup.

**Enable / disable / remove.** State changes are explicit and require a reviewed
pin. `remove()` deletes the bundle and the loader returns nothing.

**Advisory, not capability.** The module's only output is text:
`render_prompt_block()` emits an advisory block that states it cannot change
scope, permissions, paths, tools or approvals. Nothing here imports a process,
shell, network or policy module, and nothing mutates a policy object. A skill
whose text says "grant fs.delete and widen scope" is rendered as text and the
policy snapshot is unchanged (tested).

## Outcome (2026-10-28)

**Skills: 9/9 drill checks, 11 unit tests.**
`docs/briefs/evidence/eval/w6-skills-1793197039.json`, runner record
`baseline-20261028T141742Z.json` (`revisionVerified: true`):

| Check | Observation |
|---|---|
| only reviewed + enabled load | team A returns `["alpha"]`; draft and disabled excluded |
| team isolation | team B returns `["beta"]`; team C returns `[]` |
| post-review edit | excluded (the pin follows reviewed bytes) |
| executable/policy key | refused: `unknown manifest keys are not accepted: ['command']` |
| symlinked skill dir | skipped; removal refused |
| render | advisory header present, instructions preserved as text |
| policy | snapshot unchanged |
| CLI `list` | equals the loader |
| removal | unloads and deletes |

**CLI end-to-end + reconnect: PASS.**
- `cli-parity` (live) **PASS 21.3 s** — same four-event lifecycle, same terminal
  state, token 0600 in 0700, one JSON object, diff-absence parity.
  `baseline-20261028T141748Z.json`, `pilot-prep/w6-cli-parity-1793197072.json`.
- `stack-compose-journey` **PASS 13/13** — 20 containers healthy, readiness, OTP
  login, demo seed idempotent, ordered replay, audit page, sandbox
  create/exec/destroy 404, cross-tenant no-oracle, metrics, frontend.
- Reconnect remains covered by the W5 tests (bounded transport retry plus the
  SDK's WS resume/backoff tests); W6 changed no transport code.

## Gate position

| W6 exit claim | State |
|---|---|
| CLI end-to-end gate | **pass** at this revision (parity + compose journey) |
| Reconnect | **pass** (W5 tests; no transport change in W6) |
| Unreviewed skills cannot execute code or change policy | **closed for the prototype** (no executable field, no capability path, pin + review required) |
| Manifest, provenance, pinning, team scope, enable/disable/removal | **closed for the prototype** (tested and drilled) |
| Hermes claims | **blocked** until H0 paperwork is in evidence; H2 not attempted |

## Limitations

- The prototype is **file-backed and not wired into the engine**. No prompt
  receives a skill block yet; that integration is W12, and it must keep the
  "advisory only" rule.
- Review is a human decision recorded as a string. There is no automatic
  injection classifier; a reviewed bundle can still contain persuasive text.
  The boundary is policy, not persuasion: it cannot change scope or permissions,
  but it can influence what the model tries.
- No database migration, no API, no UI, no expiry, no import provenance beyond
  the declared source/author. W12 owns those.
- The skills CLI is an admin script (`python skills.py`), not part of `harness`.
- Hermes H0 is not closed: the licence exists, but the institutional and
  teammate paperwork is absent. H1's differential gate is still specified only.
- Tests: `section4/tests/unit/test_skills.py` 11 passed.

## Carry-over

- **W12:** DB-backed catalog, review UI, expiry, import/provenance checks,
  engine prompt integration, injection handling.
- **H0:** obtain the university policy statement and a written acknowledgment (or
  a documented decision that neither is required) before H2.
- **H1:** make the differential run a review-gate artifact in W14's eval work.
- **W7:** LSP integration; W8 concurrency.
