# Parts 01, 02, 15 to 19, 34, 35: directive map

> **Status: draft.** These artifacts do not exist yet and are referenced below: `docs/briefs/final-deliverables.md`, `section4/tests/unit/test_program_docs.py`,
> the trimmed `PROJECT-STATE.md` (still the long version) and the 19.010 line in `stable-prompt-prefix.md`. Rows 01, 02, 15 to 19, 34, 35 stay
> below `verified` in `capability-matrix.md` until they land.

These parts describe the product intent and how the builder works. Each directive below points at the artifact that satisfies it, checked by
looking at the file (not recalled). `section4/tests/unit/test_program_docs.py` keeps the links and the matrix shape honest.
"Practice" means the evidence is the recorded sessions (commits, lane reports, prompts), not a runtime test.

## 01 Role and outcome

| Dir | Satisfied by |
|---|---|
| 001 technical lead | `stable-prompt-prefix.md` opens every session with the role |
| 002 first safe foundation in at most four sessions, without claiming the whole product fits | Sessions 1 to 4 produced the pilot foundation (`path-to-9-checkpoint.md`, history); `PROJECT-STATE.md` "Not cleared" and `final-deliverables.md` state what the four sessions did not deliver |
| 003 work in the repository | every commit is in this repo (`git log`) |
| 004 verify inherited claims | Session 1 truth ledger (`evidence/s1/verification.md`); the Claude verification pass over Codex Sessions 6 to 10 found and fixed regressions (`6b6adfb`) and a stale part 44 claim (`00a1412`) |
| 005 do not assume it is implemented | `capability-matrix.md` states per part, `verified` only with a test or evidence file |
| 006 to 009 shared live workroom centred on a shared run; observe, guide, redirect, review, hand off; human authority explicit | `collaboration-contract.md` (join, redirect, handoff, approvals with a leader decision), `realtime.md`, `control.md`; verified rows 05 to 08, 10, 11, 22, 23, 27, 42, 46 |
| 010 working, documented, demonstrable result at each session end | one close-out commit and evidence summary per session (`evidence/s2` to `s11`, `session*-summary.md`); the pilot smoke (`deploy/pilot/run_smoke.py`) was last run green in Session 5; **not run on this tree** (no Docker/PostgreSQL) |

## 02 Product direction

| Dir | Satisfied by |
|---|---|
| 001 to 003 small teams, one repeated task, small code issue plan to review | `docs/product-boundary.md`, `pilot-user-guide.md`, `runbooks/pilot.md` |
| 004 teammates join the same run and see current state | `POST /join`, `GET /participants`, `collab-history` (`collaboration-contract.md` 1, 6); UI `collab.test.tsx` |
| 005 concise plan, progress, changed files, decisions, outcomes | plan versions (`plans.md`), execution records (`execution.md`), approvals (`collaboration-contract.md` 4), session workbench `sessionSixWorkbench.tsx` |
| 006 action history without private model reasoning | search and export strip hidden-reasoning keys (`search.py`, `exports.py`); agent reports reject `reasoning`/`thoughts` fields (`agent_tree.py` `extra=forbid`). **Limit:** the project engine event stream (`agent_events`) was not audited for model text |
| 007 normal guidance during a run | `POST /redirect` kind `guidance` |
| 008 every meaningful redirection is a collaboration event | `redirect.<kind>` events plus audit rows (`audit-and-events.md` sections 1 and 2) |
| 009 deliberate handoff | `collaboration-contract.md` 3, `control.md` |
| 010 do not claim beyond demonstrated behaviour | matrix states and "not run" lists; part 61 NO-GO; `PROJECT-STATE.md` "Not cleared" |

## 15 Implementation method

| Dir | Satisfied by |
|---|---|
| 001 architecture map and gap list | `docs/architecture-map.md`; `capability-matrix.md`; `path-to-9-plan.md` |
| 002 thin vertical slice | `deploy/pilot/run_smoke.py` two-user flow with a stand-in worker (`evidence/s1/otp-e2e.md`) |
| 003 backend policy and persistence before UI | every session's lane order (backend lanes first, UI lane last: Session 4 lane U, Session 5 lane X); route policy classification `policy_enforce.py` |
| 004 UI bound to authoritative server state and streams | `realtimeCore.ts`, `collabStream.ts` (`realtime.md` 5) |
| 005 observability for approvals, redirects, handoffs, cancellation | domain counters and audit rows (`audit-and-events.md` 7) |
| 006 reviewable commits | practice: one commit per lane or task (`git log`), `commit-plan.md` |
| 007 feature flags only when they cut rollout risk | the one pilot flag `HARNESS_PILOT_TEAMS` (`runbooks/pilot.md`, `test_pilot_flag_rollback.py`) |
| 008 no unrelated platform features | scope is the 61-part prompt; `product-boundary.md` lists what is out |
| 009 preserve public contracts | `api-versioning.md`, `scripts/spec_drift.py`, generated SDKs regenerated with the specs |
| 010 docs alongside behaviour | doc-law tests: `test_collab_contract.py`, `test_threat_closeouts.py` (release guard), `test_program_docs.py` |

## 16 Agent operating model, 17 Suggested lanes

| Dir | Satisfied by |
|---|---|
| 16.001 to 16.008, 17.005 to 17.010 bounded lanes, one deliverable, explicit files, no shared-file edits, compact reports, a named lead | the "Lane contract" block in `path-to-9-session4-prompt.md` to `session10`: lanes never touch shared files, the lead wires; each lane writes `evidence/s<N>/lane-*.md` with Files, Verification and "Not verified / honest gaps" |
| 16.009 integrate centrally after reviewing diffs | integration commits ("Session 4 integration", "Session 5 integration") |
| 16.010 never treat agent output as verified until inspected | practice, and the written rule in `stable-prompt-prefix.md`; the Claude pass re-ran Codex's suites before accepting them |
| 17.001 to 17.004 lanes A to D | Session 1 and 2 lanes A to D (`path-to-9-session1-prompt.md`, `evidence/s2/lane-a.md` to `lane-d.md`); lane D reviewed ops and the threat model |

## 18 Context budget, 19 Prompt cache

| Dir | Satisfied by |
|---|---|
| 18.001 to 18.004 durable brief under two pages, decisions, owners, next actions, compact session notes | `PROJECT-STATE.md` (trimmed to two pages in this pass; history moved to `evidence/s11/state-history.md`); `path-to-9-checkpoint.md` |
| 18.005 to 18.009 targeted reads; detail in files, referenced by path | practice; evidence files per part; this directory |
| 18.010 checkpoint before compaction | `path-to-9-checkpoint.md` is rewritten at each session end; `checkpoint-template.md` |
| 19.001 to 19.004 stable prefix first, session tail after, reused unchanged | `stable-prompt-prefix.md`; each `path-to-9-session<N>-prompt.md` starts from it |
| 19.005 to 19.009 efficiency | practice |
| 19.010 record cache controls only where the tool exposes them | none used; the prefix file says so |

## 34 Final deliverables, 35 Final response format

`final-deliverables.md` is the index: executive summary, feature matrix, evidence references, session outcomes, acceptance results with pass/fail/not-verified labels,
risk register, pilot setup, feedback and metric plan, next steps, and the readiness decision (NO-GO for broad rollout; operator-present allow-listed rehearsal only after
the listed pre-flight checks). The same file holds the response template that satisfies part 35.

## Honest limits

* Parts 16, 17, 18.005 to 18.009 and 19.005 to 19.009 are working practice; no test can prove them. The artifacts above are the evidence that the practice was
  written down and followed, not a runtime guarantee.
* The pilot smoke has not run on this tree (Docker and PostgreSQL unavailable here). 01.010 rests on Session 5's green smoke and the local suites.
* 02.006: only the search, export and agent-report surfaces were checked for reasoning exposure.
