# Cloud thread headers — Sprint 5 gap-close

Replace `<SHA>` with `git rev-parse HEAD` after clone.

## Coordinator (paste first)

```text
Harness gap-close coordinator. Repo: github.com/mubasharali24428-crypto/harness (public).
Branch: w28-s2-sprints1-4 (PR #27). Baseline SHA: <SHA>.
Read docs/briefs/sprint5-gap-close/MASTER-PROMPT.md and docs/briefs/evidence/sprint5/END-report-2026-11-24.md.
Sprint 5 Tasks 0–40 are DONE. Execute Tasks 41–49 per model routing in MASTER-PROMPT.
Task 43, 45b, 50 are OWNER MAC — document BLOCKED only.
D2 = soft-remove; D3 = userId on roster. Verdict stays NOT READY.
One commit per task; push after each. No payment / P-D7 / global P-D6.
```

## Thread 1 — Sonnet (41 → 44 → 45a → 46 → 47 → 49)

```text
REPO: github.com/mubasharali24428-crypto/harness
BRANCH: w28-s2-sprints1-4
SHA: <SHA>
MODEL: Claude Sonnet (not Opus)
PROMPT: docs/briefs/sprint5-gap-close/MASTER-PROMPT.md — Tasks 41, 44, 45a, 46, 47, 49 in order.
Stop before Task 42 (Opus). Push each task. openapi → check-ts-dist.
```

## Thread 2 — Opus (42 only)

```text
REPO: github.com/mubasharali24428-crypto/harness
BRANCH: w28-s2-sprints1-4 (rebase on SHA after Task 41 if needed)
SHA: <SHA after 41>
MODEL: Claude Opus
PROMPT: MASTER-PROMPT Task 42 only — D2 soft-remove memberships + migration + tests.
Do not break audit append-only law. Push one commit.
```

## Thread 3 — Opus optional (48 checkpoint-1)

```text
Only after 41–44 green on GitHub required checks.
BRANCH: w28-s3-checkpoint1 from <gap-close SHA>
MODEL: Claude Opus
PROMPT: MASTER-PROMPT Task 48.
```

## Mac handback (you paste after Docker work)

```text
Mac truth at SHA <sha>:
- invite-revoke-live.e2e.ts: PASS/FAIL
- make gate rc=
- readiness manifest updated Y/N
Paths: docs/briefs/evidence/sprint5-gap-close/*.md
```