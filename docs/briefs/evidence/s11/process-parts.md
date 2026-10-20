# Parts 01, 02, 15 to 19, 34, 35: directive map

These parts describe the product intent and how the builder works. Each directive below points at the artifact that satisfies it. The
artifacts were checked by opening the files on 2026-10-20 (not recalled), and `section4/tests/unit/test_program_docs.py` re-checks that
every file path named in this document exists, that the matrix has 61 rows, and that the verified rows cite existing evidence.
"Practice" means the evidence is the recorded sessions (commits, lane reports, prompts), not a runtime test.

The master prompt itself (`docs/master-prompts/harness-multiplayer-master-build-prompt.md`) is **not in the repository** (it was never
committed), so the one-line directive wording below is the program owner's gloss, not text re-read in this pass. What was verified is that
each cited artifact exists and says what is claimed.

## 01 Role and outcome

| Dir | Satisfied by |
|---|---|
| 001 technical lead | `stable-prompt-prefix.md` opens every session with the role |
| 002 first safe foundation in at most four sessions, without claiming the whole product fits | Sessions 1 to 4 built the pilot foundation (`state-history.md` sections 3 and 3b; matrix rows 04 to 11, 22, 23, 27, 37 to 42); `docs/roadmap.md` says phases are "not compressed into the four-session pilot promise"; `final-deliverables.md` and `PROJECT-STATE.md` "Not cleared" state what was not delivered |
| 003 work in the repository | every commit is in this repo (`git log`) |
| 004 verify inherited claims | Session 1 truth ledger (`evidence/s1/verification.md`); the Claude verification pass over Codex Sessions 6 to 10 found and fixed regressions (commit `6b6adfb`) and a stale part 44 claim (`00a1412`); the Session 11 cloud pass found and fixed a PostgreSQL-only bug (`evidence/s11/live-pg-search-export.md`) and recorded files the pushed history cannot run without (`evidence/s11/clean-checkout-gaps.md`) |
| 005 do not assume it is implemented | `capability-matrix.md` states per part, `verified` only with a test or evidence file (`test_program_docs.py` fails a verified row without an existing evidence path) |
| 006 to 009 shared live workroom centred on a shared run; observe, guide, redirect, review, hand off; human authority explicit | `docs/collaboration-contract.md` (roles, redirects, handoff, approvals with a leader decision), `docs/realtime.md`, `docs/control.md`; verified rows 05 to 08, 10, 11, 22, 23, 27, 42, 46 |
| 010 working, documented, demonstrable result at each session end | one close-out commit and evidence summary per session (`evidence/s2` to `s11`, `session*-summary.md`); the pilot smoke (`deploy/pilot/run_smoke.py`) was last recorded green in Session 5 (`state-history.md` section 4); **not run on this tree** (no Docker daemon) |

## 02 Product direction

| Dir | Satisfied by |
|---|---|
| 001 to 003 small teams, one repeated task, small code issue plan to review | `docs/product-boundary.md` ("Small engineering teams (2-10 people)... well-scoped tasks (bug fixes, refactors, tests, docs)"), `docs/runbooks/pilot.md` |
| 004 teammates join the same run and see current state | `POST /join`, `GET /participants`, `collab-history` (`docs/collaboration-contract.md` sections 1 and 6); UI `collab.test.tsx` |
| 005 concise plan, progress, changed files, decisions, outcomes | plan versions (`docs/plans.md`), execution records (`docs/execution.md`), approvals (`docs/collaboration-contract.md` section 4), session workbench `sessionSixWorkbench.tsx` |
| 006 action history without private model reasoning | search and export strip hidden-reasoning keys (`search.py` pattern at line 88, `exports.py` header text); agent reports reject `reasoning` and `thoughts` fields (`agent_tree.py`, `extra="forbid"`, 422). **Limit:** the project engine event stream (`agent_events`) was not audited for model text |
| 007 normal guidance during a run | `POST /redirect` kind `guidance` (`docs/collaboration-contract.md` section 2) |
| 008 every meaningful redirection is a collaboration event | `redirect.<kind>` events plus audit rows (`docs/audit-and-events.md` sections 1 and 2) |
| 009 deliberate handoff | `docs/collaboration-contract.md` section 3, `docs/control.md` |
| 010 do not claim beyond demonstrated behaviour | matrix states and "not run" lists; part 61 NO-GO (`evidence/s10/readiness-report.md`); `PROJECT-STATE.md` "Not cleared" |

## 15 Implementation method

| Dir | Satisfied by |
|---|---|
| 001 architecture map and gap list | `docs/architecture-map.md`; `capability-matrix.md`; `path-to-9-plan.md` |
| 002 thin vertical slice | `deploy/pilot/run_smoke.py` two-user flow with a stand-in worker (`evidence/s1/otp-e2e.md`) |
| 003 backend policy and persistence before UI | lane order in the session prompts (backend lanes first, UI lane last: Session 4 lane U, Session 5 lane X); route policy classification `contracts/src/harness_contracts/policy_enforce.py` |
| 004 UI bound to authoritative server state and streams | `realtimeCore.ts`, `collabStream.ts` (`docs/realtime.md` section 5) |
| 005 observability for approvals, redirects, handoffs, cancellation | domain counters and audit rows (`docs/audit-and-events.md` section 7) |
| 006 reviewable commits | practice: one commit per lane or task (`git log`: `49448b5` lane A, `1c6fb93` lane U, `99c7911` lane X, `fa085cd` the Session 11 PostgreSQL fix) |
| 007 feature flags only when they cut rollout risk | the one pilot flag `HARNESS_PILOT_TEAMS` (`docs/runbooks/pilot.md`, `test_pilot_flag_rollback.py`) |
| 008 no unrelated platform features | scope is the 61-part prompt; `docs/product-boundary.md` lists what is out |
| 009 preserve public contracts | `docs/api-versioning.md`, `scripts/spec_drift.py`, generated SDKs regenerated with the specs |
| 010 docs alongside behaviour | doc-law tests: `test_collab_contract.py`, `test_threat_closeouts.py` (release guard), `test_program_docs.py` |

## 16 Agent operating model, 17 Suggested lanes

| Dir | Satisfied by |
|---|---|
| 16.001 to 16.008, 17.005 to 17.010 bounded lanes, one deliverable, explicit files, no shared-file edits, compact reports, a named lead | the "Lane contract" sections of `path-to-9-session3-prompt.md`, `path-to-9-session4-prompt.md`, `path-to-9-session5-prompt.md` and the "Lanes" section of `path-to-9-session6-prompt.md`: lanes own only listed files and the lead wires; each lane wrote `evidence/s<N>/lane-*.md` (Sessions 2 to 6) and most carry a "Not verified / honest gaps" section. Sessions 7 to 10 were single-agent (no lane files), which the rule allows ("lanes only for independent work") |
| 16.009 integrate centrally after reviewing diffs | integration commits `c0a3c72` (Session 3), `aecae32` (Session 4), `0693820` (Session 5) |
| 16.010 never treat agent output as verified until inspected | practice, and the written rule in `stable-prompt-prefix.md`; the Claude pass re-ran Codex's suites before accepting them (`path-to-9-checkpoint.md`) |
| 17.001 to 17.004 lanes A to D | Session 2 lanes A to D (`path-to-9-session2-prompt.md`, `evidence/s2/lane-a.md` to `lane-d.md`); lane D reviewed ops and the threat model |

## 18 Context budget, 19 Prompt cache

| Dir | Satisfied by |
|---|---|
| 18.001 to 18.004 durable brief under two pages, decisions, owners, next actions, compact session notes | `PROJECT-STATE.md` (at most 130 lines, enforced by `test_program_docs.py`; history moved verbatim to `evidence/s11/state-history.md`); `path-to-9-checkpoint.md` |
| 18.005 to 18.009 targeted reads; detail in files, referenced by path | practice; evidence files per part; this directory |
| 18.010 checkpoint before compaction | `path-to-9-checkpoint.md` is rewritten at each session end; `checkpoint-template.md` |
| 19.001 to 19.004 stable prefix first, session tail after, reused unchanged | `stable-prompt-prefix.md`; each `path-to-9-session<N>-prompt.md` is a session tail |
| 19.005 to 19.009 efficiency | practice |
| 19.010 record cache controls only where the tool exposes them | none used; `stable-prompt-prefix.md` section "Cache controls (part 19.010)" says so, with the grep that backs it |

## 34 Final deliverables, 35 Final response format

`docs/briefs/final-deliverables.md` is the index: executive summary, feature matrix, evidence references, session outcomes, acceptance
results with pass/fail/not-verified labels, risk register, pilot setup, feedback and metric plan, next steps, and the readiness decision
(NO-GO for broad rollout; operator-present allow-listed rehearsal only after the listed pre-flight checks). The same file holds the
response template that satisfies part 35.

## Honest limits

* Parts 16, 17, 18.005 to 18.009 and 19.005 to 19.009 are working practice; no test can prove them. The artifacts above are the evidence
  that the practice was written down and followed, not a runtime guarantee.
* The master prompt text is not in the repository, so directive wording was not re-read against the source (see the top of this file).
* The pilot smoke has not run on this tree (no Docker daemon here, and the pushed history cannot import section 4 without the owner's
  uncommitted files: `evidence/s11/clean-checkout-gaps.md`). 01.010 rests on Session 5's recorded green smoke and the local suites.
* 02.006: only the search, export and agent-report surfaces were checked for reasoning exposure.
* 18.001: "under two pages" is held to a line count (130), not a rendered page count.
