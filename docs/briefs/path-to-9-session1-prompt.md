# Session 1 prompt — Truth ledger and verification baseline

Paste everything below the line into a new Claude Code session opened in
`/Users/user/untitled folder 2/harness`.

---

You are the technical lead for the Harness Multiplayer AI product. Repository: /Users/user/untitled folder 2/harness. The repository is the source of truth; verify every inherited claim yourself.

Context: a 61-part master build prompt has been scored. Plan: docs/briefs/path-to-9-plan.md (read it first; "9/10" is defined there). Prior evidence: docs/briefs/multiplayer-readiness.md, STATUS.md, docs/pilot-remediation/. The master prompt text itself: docs/master-prompts/harness-multiplayer-master-build-prompt.md.

Rules: deny-by-default security; no auto-merge; no invented evidence, traction, or compliance claims; do not claim a check passed unless you ran it and report the command and result; do not touch unrelated uncommitted work (run `git status` first; ~126 files are already modified); commit by task with per-file pathspecs only when I ask; one focused deliverable per subagent with explicit files, no two agents editing the same file; subagents return findings and paths, never transcripts.

## SESSION 1 OBJECTIVE
Raise parts 13, 14, 15, 16, 17, 18, 19, 25, 26, 28, 29, 31 (mapping only), 33, 34, 35, 36, 60 and the ledger half of 61 to 9/10. This session builds no new product features; it builds the verified baseline every later session depends on.

## DELIVERABLES
1. **Capability matrix** `docs/briefs/capability-matrix.md`: one row per directive group of all 61 parts (id, requirement, user value, owner section, state = implemented|partial|missing|blocked|verified, acceptance test path, evidence path). Populate from code, not from old docs. This is the single source of truth for later sessions.
2. **Fresh verification run**: run every section's suite and the frontend (vitest + tsc) plus lint; record exact commands, counts, and any flake in `docs/briefs/evidence/s1/`. Re-confirm the container isolation and the P1 browser proof still run; if they cannot be run here, say so.
3. **Contract regeneration (part 14)**: extend `scripts/spec_drift.py` coverage from the orchestrator to every service that has an `openapi.json`; fix the 9 `KNOWN_UNSPECCED` UI-called URLs or document each; regenerate stale specs; document event schema versioning and limits (text, paths, payloads, event rates) in one contracts doc.
4. **Real sign-in end-to-end (parts 25, 26)**: replace the synthetic sign-in in the two-user browser proof with the real OTP/session flow (test mail sink acceptable); keep the stand-in worker only if the real runtime cannot run in CI, and state which. Re-run the pilot scenario: create run, teammates see same state, redirect recorded, handoff changes owner, high-impact action blocked then approved on exact digest, changed proposal re-requires approval, stop prevents mutation, cross-run isolation, reconnect/refresh survival.
5. **Ops acceptance (part 28)**: health endpoints that distinguish dependency failure from app failure; log correlation of request/run/collab action with a test; alert rules for stalled runs and broken event delivery; documented concurrency limits that are enforced; migration apply + rollback proof on a scratch Postgres; clean-environment start from documented instructions only (follow the docs literally and fix the docs where they fail); incident/recovery procedure.
6. **Security review refresh (part 29)**: threat model covering cross-team access, stale approvals, replayed decisions, concurrent leader decisions, forged actor fields, provider calls outliving cancellation, shared temp storage, malicious paths and crafted diffs. Each threat: mitigation, test path or "untested", residual risk, owner.
7. **Data map (part 31, mapping only)**: table of every stored data class (tables, files, logs, caches, event streams) with who can read it, retention, deletion rule, and telemetry fields explained. Enforcement is Session 8.
8. **Process parts (13, 15, 16, 17, 18, 19, 33, 34, 35, 36, 60)**: architecture map (authoritative state owners, auth trace UI to DB, event trace producer to subscriber, approval trace); checkpoint template file; a stable prompt prefix file for later sessions (stable opening first, session-specific tail last); versioned roadmap with phase exit criteria and committed vs exploratory; product boundary (target users, team sizes, non-goals, excluded high-risk uses). Keep each under two pages.
9. **Checkpoint** `docs/briefs/path-to-9-checkpoint.md` (<2 pages): done, evidence paths, commands run, open blockers with severity, exact next tasks for Session 2.

## METHOD
1. Plan first: write a numbered plan with a verify step per item and show it before starting.
2. Use at most four subagent lanes, only where independent: (A) full test + lint verification, (B) contract/spec drift, (C) ops acceptance + migrations, (D) threat model. You integrate, review diffs, and own the matrix.
3. Prefer targeted greps and narrow excerpts. Store detail in files; reference by path.
4. Do not defer any failing test; report it and root-cause it.

## DONE WHEN
- Capability matrix covers all 61 parts, every row has a state and an evidence path or an explicit "not verified".
- All suites run in this session with results recorded; failures explained.
- Real sign-in two-user proof passes, or the exact blocker is documented.
- Every deliverable above exists, docs match shipped behavior, checkpoint written.

## FINAL RESPONSE FORMAT
1. One-sentence readiness statement for the baseline.
2. What existed vs what changed.
3. Commands run and results (no unrun claims).
4. Parts now at 9 (with evidence path) and parts still below 9 (with reason).
5. Open risks and whether each blocks Session 2.
6. Files changed (links) and the checkpoint link.
Ask one focused question only if a genuine decision remains.
