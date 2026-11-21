# hy4 batch 3 hand-back — C3

## Task
C3 — Say the denial-knob footgun out loud (comment only).

## Status
DONE

## Assumptions
Batches 1–2 left `f5b298a` with everything uncommitted; the three `./` defects are reachable until the writer table proves otherwise; no paid call, deploy, `docker run`, secret or git write is needed. C3 adds only that the reviewer has not ruled on the `0`/empty/`off` semantics, so the change must be words, not behaviour.

## Files changed
- `.env.example` (edited) — **only the comment block** for `HARNESS_DENIAL_RUN_LIMIT` and `HARNESS_DENIAL_WINDOW_LIMIT`. The three-line shared arrow rule was replaced by one plain sentence under each knob:
  `Setting 0, empty or off turns this stop off, a positive number is the limit, and a non-number falls back to the default.`
  The intro sentence, the "3 denials IN A ROW" / "10 denials within the last 50 tool RESULTS" lines, the "50-result window itself is not tunable" line and both example value lines (`# HARNESS_DENIAL_RUN_LIMIT=3`, `# HARNESS_DENIAL_WINDOW_LIMIT=10`) are unchanged in meaning; no value was changed and no code was touched.
- `docs/briefs/hy4-b3-handback-C3.md` (new) — this hand-back.

The pre-existing `git diff -- .env.example` hunk is batch 1 A4's uncommitted addition. C3 changed only comment lines inside it.

## Commands run

```
cd "/Users/user/untitled folder 2/harness" && git diff -- .env.example
→ diff --git a/.env.example b/.env.example
→ @@ -271,6 +271,19 @@ DSH_SESSION_ROOT=/tmp/harness-dsh/sessions
→ +# W28: the orchestrator's two repeated-denial stops (loop_guard.py). A denial is
→ +# a shell-policy refusal, NOT a timeout or a merely failed command.
→ +# 3 denials IN A ROW stop the run as denials_repeated. Default: 3.
→ +# Setting 0, empty or off turns this stop off, a positive number is the limit, and
→ +# a non-number falls back to the default.
→ +# HARNESS_DENIAL_RUN_LIMIT=3
→ +#
→ +# 10 denials within the last 50 tool RESULTS stop the run. Default: 10.
→ +# Setting 0, empty or off turns this stop off, a positive number is the limit, and
→ +# a non-number falls back to the default.
→ +# The 50-result window itself is not tunable here.
→ +# HARNESS_DENIAL_WINDOW_LIMIT=10
```
```
cd "/Users/user/untitled folder 2/harness" && git diff -- .env.example | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -vE '^[+-]#'
→ (no output; NON_COMMENT_LINES_RC=1 from grep = no non-comment added/removed lines)
```
```
cd "/Users/user/untitled folder 2/harness" && make static-gates
→ STATIC_GATES_RC=0
→ orchestrator specs and UI calls match the router.
→ {"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}
→ env knob catalogue complete: 62 runtime reads, all in .env.example
```
The env-knob gate (`scripts/check_env_knobs.py`) still finds both knobs documented: 62 runtime reads, all in `.env.example`.

## Proof the new tests fail without the change
N/A — C3 changes no behaviour and adds no test. Nothing was implemented that could be falsified; the acceptance is the two checks above (`make static-gates` rc=0 and a comment-only diff). No negative control was run because there is no new behaviour to control. The batch 3 rule "every new behaviour gets a test with a real negative control" does not apply to a comment-only edit.

## What is NOT verified
- The semantics themselves were **not** changed or tested; `0`, empty and `off` still disable a stop, which is the footgun this comment now names out loud. The reviewer has not ruled on whether that is correct.
- No deploy, no service restart, no env value set, no live run. Nothing from batches 1–3 is deployed.
- No full test suite was run for C3; the task's only check is `make static-gates`.

## Unrelated findings (one line each)
- `scripts/check_env_knobs.py` reads literal `os.environ.get("KEY")` strings, so a comment mentioning a knob does not register it; only the example lines matter. The gate still reports 62 reads.
- The `0 disables` footgun remains a reviewer ruling (batch 1 A4 hand-back, "Risks the reviewer should check first"); this comment does not decide it.

## Risks the reviewer should check first
- Confirm the sentence is plain enough for an operator who reads `0` as "stop immediately"; it now says explicitly that `0`, empty and `off` turn the stop **off**.
- Rule on the semantics themselves: the comment only states current behaviour, it does not endorse it.
- If you paste a different wording, keep `make static-gates` rc=0 (62 runtime reads, both knobs documented).

## Scratch files (all under /private/tmp/hy4-*)
- `/private/tmp/hy4-b3/static-gates-c3.txt` (log of the C3 `make static-gates` run).
