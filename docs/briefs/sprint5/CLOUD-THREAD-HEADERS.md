# Cloud thread headers (paste into Claude Code)

Replace `<BASELINE_SHA>` and `<BRANCH>` after PR 27 exists.

## Coordinator (first message)

```text
Harness delivery coordinator. Repo: github.com/mubasharali24428-crypto/harness.
Branch: <BRANCH>. Baseline SHA: <BASELINE_SHA>.
Read docs/briefs/sprint5/MASTER-PROMPT.md and docs/briefs/sprint5/OPUS-PREP-CHECKLIST.md.
Do not start Opus until PR 27 exists and SHA is confirmed.
Enforce: one thread = one done-when; push after each task; no fake live PASS without Docker.
```

## THREAD A — Opus Sprint 5 (31, 32, 37, 38)

```text
REPO: github.com/mubasharali24428-crypto/harness
BRANCH: <BRANCH>
BASELINE SHA: <BASELINE_SHA>
PROMPT: docs/briefs/sprint5/MASTER-PROMPT.md — execute Tasks 0, 31, 32, 37, 38 only in this thread.
Tasks 33–36 are OWNER MAC (Docker); mark BLOCKED in evidence if asked.
RULES: one commit per task; push after each; openapi → check-ts-dist; no owner signatures.
DONE WHEN: Tasks 31–32 tests pass; 37–38 evidence md; all commits pushed.
```

## THREAD B — Sonnet (parallel)

```text
Repo harness, branch <BRANCH>. Update PR body from docs/briefs/sprint5/PR-27-BODY.md if needed.
Task 39 only: ci-local summary md + docs/briefs/evidence/sprint5/c07-pilot-feedback-template.md (see MASTER-PROMPT Task 39).
Done when: docs committed and pushed.
```

## THREAD C — Opus checkpoint-1 (after 31–32 pushed)

```text
BRANCH: w28-s3-checkpoint1 from <SHA after Task 32>
Read docs/briefs/evidence/90day/checkpoint1-redesign-2026-11-21.md (on master after PR 26, or cherry-pick).
Un-skip integration/test_checkpoint1.py; faithful flow; max ~400 LOC production per commit.
DONE WHEN: test passes sqlite; evidence md; push each commit.
```

## Mac handback (you paste results)

```text
Mac gate: make gate rc=<n> at SHA <sha>
Playwright live: Tasks 33–36 PASS/BLOCKED + paths to docs/briefs/evidence/sprint5/task-*.md
```