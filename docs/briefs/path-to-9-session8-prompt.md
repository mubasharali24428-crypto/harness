# Session 8 — security, compliance and enforcement (parts 52, 58, 31)

Prefix: `docs/briefs/stable-prompt-prefix.md`. Read only the directive sections 52, 58 and 31 in
`docs/master-prompts/harness-multiplayer-master-build-prompt.md`, plus section 6 “Facts that bite”
in `docs/briefs/PROJECT-STATE.md`. Session 7 is committed locally; preserve any unrelated dirty
files. Do not begin until Session 7's checkpoint and commit are reviewed.

## Objective

Map every directive in 52, 58 and 31 to implementation and evidence. Close locally testable
enforcement gaps; identify external evidence separately. Keep externally dependent parts capped at
8. Do not claim legal, compliance, penetration-test, or certification evidence from source review.

## Work sequence

1. Start with `git status` and inspect only the three requested directive sections, current matrix
   rows, referenced code/tests/docs and the facts-bite section. Preserve unrelated dirty work.
2. Produce a numbered directive-to-code/evidence map and exact verification plan before editing.
3. For part 52, inspect threat model, secret boundaries, auth/session security, audit integrity,
   dependency/container posture, tenant isolation and failure behavior. Add only concrete local
   controls and invariant tests where needed. Record penetration testing and external review as
   gated evidence.
4. For part 58, map retention, deletion, residency, legal hold and data export behavior across the
   product. Implement only supported lifecycle controls and enforce them at storage boundaries.
   Do not imply legal approval or residency guarantees from a configuration label.
5. For part 31, trace every model/tool execution path against Section 5's allow-list and
   `forbidden_paths`, including run start, queue claim, retries, agents, reviewers and workers.
   Add deny-by-default enforcement and bypass tests where a real gap exists. Preserve frozen
   contracts; version a contract only if a change is unavoidable and update mocks in the same task.
6. Add one report per part under `docs/briefs/evidence/s8/`, update relevant product docs, and
   honestly update matrix rows. Explicitly keep part 44 partial and preserve Session 7's part 57
   cap at 8.
7. Run targeted tests and Ruff for touched sections/contracts; run relevant full suites only when
   the environment permits. Record exact commands/counts and platform blockers.
8. Update `PROJECT-STATE.md` and `path-to-9-checkpoint.md`; write `session8-summary.md`.
9. Stage explicit owned paths and create a local commit only. Never push. Do not start Session 9.

## Acceptance

Every directive is implemented or explicitly out of scope with a reason, invariant-tested where
applicable, documented, evidenced and reflected in the matrix. Report exact commits, test results,
external evidence caps, unrun checks and remaining gaps.
