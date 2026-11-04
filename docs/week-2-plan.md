# Week 2 plan — context and recovery

Follows [Week 1](week-1-plan.md) and is governed by
[the marathon execution plan](marathon-execution-plan.md), which sets the rule
this week inherits: *"An unchecked gate stays open when a dependent week begins."*

## Inherited open, not silently deferred

- **M01/M03 — approved-scope enforcement.** W1's spike measured it and the answer
  was no: writes inside the workspace but outside the approved scope succeed,
  because `python3` is the only file-writing binary the policy leaves and
  `scope_violation` skips code strings. M03 needs mount-level enforcement, not a
  better parser. W2 does not depend on it, and nothing in this week widens write
  access while it stays open.
- **M07 — core live acceptance.** Blocked on topology: the orchestrator container
  has no Docker socket, so the confined worker that the stack is supposed to run
  cannot be created there. Needs a decision (worker daemon vs socket mount vs
  shipping `off`), not code.

## Packages

| ID | Deliverable | Completion evidence | State |
|---|---|---|---|
| N01 | A looping or burning run is caught from the event stream and stopped with a durable reason, not left to the wall clock | `tests/unit/test_loop_guard.py`, `tests/unit/test_loop_guard_wiring.py`; mutation-checked (wiring disabled → 3 of 4 wiring tests fail) | **done** |
| N02 | Baseline repo context reaches the prompt (tree, README, git status, dependency manifests) | `tests/unit/test_repo_context.py` (8), `test_repo_context_wiring.py` (6); bounded block, once per session | **done** |
| N03 | `project_rules` reaches the executing agent, not only the planner | `tests/unit/test_n03_context_to_executor.py` (5) | **done** |
| N04 | Retry-with-changes as a visible affordance | `tests/unit/resumeWithChanges.test.ts` (4), `controlsResumeWithChanges.test.tsx` (6); ordering pinned, empty change refused | **done** |

## N01 — what was actually wrong

`maxToolCalls` existed, and `remaining_tool_calls` computed it honestly. The
defect was *when*: the limit was a snapshot taken **before** the run, from
`COUNT(*) FROM agent_tool_calls`, and that table is populated by
`record_engine_tool_calls` only **after** the engine call returns. During the run
the count is always zero. Native `tool-fs` calls are not bash calls, so the shell
plugin's own counter never saw them either. Measured consequence: two acceptance
attempts made 14 and 37 tool calls against a one-sentence goal and were stopped by
nothing but `ENGINE_RUN_TIMEOUT_SECONDS`.

The guard therefore sits on the W1 transcript forwarder — the one place every
call passes on both execution paths — and it is pure and DB-free, so the answer
cannot depend on a table arriving late. Two independent stops: `loop_detected`
(same tool, same normalised arguments, four times) and `tool_budget_exhausted`
(more calls than allowed, counting distinct calls rather than distinct shapes).

Three things worth stating plainly:

1. **The worker is not killed.** There is no cancel channel into the SDK session;
   that is W3's spike. So a stopped run keeps burning until it ends by itself.
   What N01 guarantees is that the stop is durable and visible immediately, that
   the shell budget is written to zero so further bash calls refuse, and that a
   late `completed` cannot overwrite the failure.
2. **The post-run drain counts too.** `finally` used to emit records without
   consulting the guard; a loop starting in the last second would have been
   invisible. Both paths now go through one consumer.
3. **`tool_budget_exhausted` is untested at the service seam** — the fixture has
   no `run_limits` row, so the budget arrives as `None` and only the repeat guard
   can fire there. The arithmetic is covered in `test_loop_guard.py`.

## Gate

A deliberately looping fixture is caught and stopped with a message, not a budget
burn; and a representative task in a 500-file repo finds the relevant files
without being told. The first half is met. The second needs N02.

## N02 — the baseline the product can actually control

`includeRuntimeContext:false` is a key in the **SDK's** profile, not in our source,
so the lever this product holds is the goal string it hands the engine.
`repo_context.py` builds a bounded block — file tree with generated/vendored
directories pruned, README excerpt, `git status --short` when the workspace is a
repo, and the names of any dependency manifests — and `execute_goal` prepends it
once per session.

Four decisions worth their reasons:

- **Pruned before descent, not filtered after.** Walking `node_modules` and then
  discarding it costs seconds on a real checkout and buys nothing.
- **Bounded, and the bound is tested.** A context block that grows with the repo
  turns every run of a 500-file project into a 500-file prompt, paid for every
  time. `max_chars` truncates with an explicit marker so the model does not read
  the cut as the end of the repository.
- **Empty means empty.** A workspace with nothing recognisable returns `""`
  rather than a page of headings — an empty scaffold teaches the model that the
  repo is empty, which is worse than saying nothing.
- **Once per session.** The SDK session continues across a resume, so a second
  copy is not extra context; it is a duplicate the model pays to read again. A
  *different* session still gets its own copy.

`HARNESS_REPO_CONTEXT=0` disables it. Not yet demonstrated: the gate's other half —
a real task in a 500-file repo finding the right files without being told. That
needs a live run, and live runs are what M07 is blocked on.

## N03 — two blocks were assembled and thrown away

The finding here is smaller than N02's work and worse than it looks. Both context
blocks are built while planning and neither reached execution:

- `run_data["rules_prompt_block"]` — the project's rules, the
  `CLAUDE.md`/`AGENTS.md` equivalent. Its own comment says it is "stored on the run
  so … future engine calls can carry it into system context". The executor received
  `plan.goal + guidance` and nothing else.
- `run_data["context_bundle"]["rendered"]` — the bounded @ref bundle, whose comment
  says it "rides on the run for engine prompt assembly". It did not.

`_executor_context_prefix` now carries both into the engine call, rules before
curated context: instructions, then evidence, then the task. Reversing that order
puts the model's attention on a code snippet before it knows how the project wants
it to work. Both stay advisory — a blank block contributes nothing rather than an
empty heading, so a run with neither gets exactly the prompt it always got, which
is pinned by a test rather than assumed.

## Verification, including what is not clean

`section4` full suite, three consecutive runs after N02/N03: **5 failed / 1679
passed**, **2 failed / 1682 passed**, **1 failed / 1683 passed** (58 skipped
throughout). The first run's five were all `test_staging_e2e.py` and were **my
regression**: appending the repo baseline to the goal broke `standin_worker`'s
`startswith("delete:")` parsing, which resolved the path to
`"gone.txt\n\n## Repository context…"`. The double's assumption was wrong, not the
product change; it now reads the directive from the first line, and that file
passes 5/5.

The remaining failures are a different matter and are **not resolved here**:
`test_stateless_orchestrator_n1.py::test_mixed_decision_race_reject_wins_late_approve_conflicts`
and `test_workspace_lifecycle_s2.py::test_cleanup_refuses_while_the_worker_is_alive`.
Both pass alone, `test_workspace_lifecycle_s2.py` passes 6/6 three times as a
file, and which of the two fails changes between runs — so it is load- or
ordering-dependent rather than deterministic. They did not appear in the
pre-change run, and this week's change does add work before the worker spawns, so
"pre-existing" would be a guess. Recorded as an open flake with the numbers rather
than called green: chasing it properly is its own task, and guessing at a fix
under a feature commit is how the budgets in this repository became decorative in
the first place.

**Root-caused afterwards (2026-10-26): both were test races, not product bugs.**
Each was reproduced deterministically by widening its window rather than by
waiting for load:

- `test_cleanup_refuses_while_the_worker_is_alive` waited for `.run-tmp/<id>` and
  then asserted the worker was running, but `_run_isolated` creates that dir
  *before* it spawns and registers the worker. A 0.5 s delay in `wrap_command`
  fails it every time. It now waits on `is_worker_running` itself.
- `test_mixed_decision_race_…` asserted the reject wins a `gather` of two
  decisions, but the replicas' DB threads, not `gather`, pick the winner. Delaying
  the reject's first read fails it every time. It now asserts the real invariant
  whichever side wins: one `PlanAlreadyDecidedError`, version 1, and a durable
  status that matches the winner.

Ruled out: SQLite locks held across an await. With the busy timeout cut from 5 s
to 0.25 s, all three implicated files pass 5×. A third pair
(`test_s1_r05_resume_queue.py`) did fail with `database is locked`, but only at
load average ~80. That load was a stuck CPU-burn script of mine, not the suite.
It is starvation past the 5 s timeout, and it is not reproducible at normal load.

## N04 — the retry the roadmap asked for, and the order that makes it real

The backend already had both halves: `POST /runs/{runId}/redirect` with
`kind: "guidance"` records the correction, `POST …/resume` restarts, and the
executor already drains guidance through `collab.guidance_since` into `extra`.
What was missing was the affordance, so this is a client + component change
rather than plumbing.

`resumeWithChanges(runId, content, token)` is the whole feature in one function,
because **the order is the feature**: guidance is posted first, then the run is
resumed. Resume first would read nothing and throw the operator's correction away
— a real button with no effect, which is the fake-affordance class the roadmap's
first ground rule forbids. Three consequences fall out of that and are each
pinned:

- **Empty content is refused** before any request, rather than degrading into a
  plain resume. Resume already has a button; a second one that does the same
  thing is decoration.
- **A failed redirect never resumes.** If recording the change is denied, the run
  stays where it is; resuming would pretend the change had been accepted.
- **Content is trimmed**, so whitespace cannot pass the guard and become a
  meaningless guidance row.

In `Controls` the affordance appears only while a run can actually resume
(`paused` or `blocked`), is labelled `Resume with changes`, has an accessible name
on the input, starts disabled, and says what it does — "guidance the agent reads
before the agent continues — not a chat message, and not a restart".

Two things this does not claim: it is not a *model* retry ladder (no silent model
change), and it does not restart from a checkpoint — it continues the existing
session with new guidance, which is what the executor's `guidance_since` drain
supports today. A checkpoint rewind is W3's, not this week's.

Verification: `section7` — tsc clean, **1003 tests passed across 91 files**,
`next build` green. The sequence tests were written first and watched failing
(`resumeWithChanges` did not exist; the component had no affordance and failed
all six render assertions).
