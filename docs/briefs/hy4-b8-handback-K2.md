# hy4 batch 8 hand-back - K2 (known-limits draft)

## Assumptions

Snapshot for every task: `7c1acf4 (HEAD -> w28-s1-delegate) D1: a workspace-root scope covers every relative in-workspace path`, created with `mkdir -p /private/tmp/hy4-b8/snap && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"`. Import-path checks passed: `harness_orchestrator` resolved to `/private/tmp/hy4-b8/snap/section4/src/harness_orchestrator/__init__.py` and `harness_contracts` to `/private/tmp/hy4-b8/snap/contracts/src/harness_contracts/__init__.py`. All runs used the main checkout's venvs, PYTHONPATH pointed at the snapshot, `-p no:cacheprovider` and a private `--basetemp` from `mktemp -d /private/tmp/hy4-b8-tmp.XXXX`. No deploys, no docker commands, no git writes, no secrets, no .env reads, no production change.

Assumptions: a "limit" is one distinct open item or unsatisfied condition in PROJECT-STATE.md section 4 or 6; one source item with several limits yields several bullets; section 4's "Not a gap" line is recorded once as an exclusion, not as a limit; evidence is a file:line in the pinned snapshot that I opened and read, not an inference from the source sentence; claims that cannot be confirmed are listed under "Unverified" with what I found and are never repaired. I made no readiness, security or pen-test ruling, and did not reproduce any private hand-back content.

Plan:
1. Read PROJECT-STATE.md sections 4 and 6 in full -> verify: the delivered draft cites items 1-9 and the section 6 clauses.
2. For each bullet find and open a corroborating file:line -> verify: 15 distinct paths opened, each with a line count.
3. Write the draft to the evidence dir only, never `docs/known-limits.md` -> verify: `cmp` draft vs delivered.
4. Run the existence loop over every cited path -> verify: no MISSING lines.

## Times

Start 2026-11-07 00:40:40 PST (immediately after the K1 ledger mtime). Finish 2026-11-07 07:53:21 PST (mtime of the delivered draft). Duration 07:12:41 wall. The session was interrupted and resumed several times in this window; active work was much shorter and no claim is made about continuous effort. `date` at end of batch: 2026-11-07 08:06:27 PST.

## Commands run

No pytest runs in this task (doc only). Exact commands:
```
SNAP="/private/tmp/hy4-b8/snap"; REPO="/Users/user/untitled folder 2/harness"
sed -n '1,119p' "$SNAP/docs/briefs/PROJECT-STATE.md"
grep -nE "^#|pen test|argument|one program" "$SNAP/docs/briefs/evidence/s12/part52.md"
sed -n '130,143p' "$SNAP/docs/briefs/evidence/s12/part52.md"
sed -n '37,60p' "$SNAP/docs/briefs/evidence/s12/part56.md"
sed -n '40,50p' "$SNAP/docs/briefs/evidence/s12/part28-55.md"
sed -n '100,110p' "$SNAP/docs/briefs/evidence/s12/part54.md"
grep -nE "HARNESS_OPENROUTER_KEY_FILE|--real" "$SNAP/deploy/pilot/run_smoke.py"
grep -nE "email|webhook|pager|HARNESS_ALERT" "$SNAP/docs/runbooks/alerting.md"
grep -nE "write scope|oauth|token" "$SNAP/docs/briefs/evidence/s12/part49.md"
grep -nE "residency|in-flight|lifecycle|hold" "$SNAP/docs/briefs/evidence/s7/part51.md" "$SNAP/docs/briefs/evidence/s7/part57.md" "$SNAP/docs/briefs/evidence/s8/part31.md" "$SNAP/docs/briefs/evidence/s8/part58.md"
grep -nE "pen test|counsel|screen reader|SSO|baseline|NO-GO" "$SNAP/docs/briefs/final-deliverables.md"
sed -n '1,25p' "$SNAP/section4/src/harness_orchestrator/workspace_gate.py"
for f in $(cat /private/tmp/hy4-b8/k2-paths.txt); do test -e "$SNAP/$f" || test -e "$REPO/$f" || echo MISSING $f; done
```

## Results

Delivered: `docs/briefs/evidence/rc/known-limits-draft-2026-11-06.md` (draft only; `docs/known-limits.md` was not created). `cmp /private/tmp/hy4-b8/k2-draft.md <delivered>` -> `IDENTICAL`.

Verify output pasted:
```
bullets 25 unverified 7 paths 15
=== unverified claim count ===
7
=== copy to main checkout ===
IDENTICAL
=== required existence loop (paste output) ===
EXIT: no MISSING lines above means every cited path exists
 1 OPENED docs/briefs/PROJECT-STATE.md                                              118 lines
 2 OPENED docs/briefs/evidence/s12/part52.md                                        143 lines
 3 OPENED docs/briefs/evidence/s12/part28-55.md                                     149 lines
 4 OPENED docs/briefs/evidence/s12/part54.md                                        145 lines
 5 OPENED docs/briefs/evidence/s12/part56.md                                         49 lines
 6 OPENED docs/briefs/evidence/s12/part49.md                                         74 lines
 7 OPENED docs/briefs/evidence/s7/part51.md                                          30 lines
 8 OPENED docs/briefs/evidence/s7/part57.md                                          37 lines
 9 OPENED docs/briefs/evidence/s8/part31.md                                          38 lines
10 OPENED docs/briefs/evidence/s8/part58.md                                          24 lines
11 OPENED docs/briefs/final-deliverables.md                                         163 lines
12 OPENED docs/runbooks/alerting.md                                                 299 lines
13 OPENED deploy/pilot/run_smoke.py                                                 324 lines
14 OPENED section4/src/harness_orchestrator/workspace_gate.py                       211 lines
15 OPENED section4/src/harness_orchestrator/delegation.py                           214 lines
TOTAL PATHS OPENED = 15
```
Counts: 25 bullets, 15 evidence paths opened, 7 unverified claims, 32 source claims handled. The 15 paths are all the paths cited anywhere in the draft.

## Failures and NEEDS-REVIEWER-RUN

No failed runs (no tests were run). Seven section 4/6 claims are listed under "Unverified" in the draft rather than repaired: (1) backups expired outside the scripts / bucket lifecycle rules not covered by holds - no snapshot file contains those phrases; part31.md:12 and part58.md:9 say the opposite for the in-script case; (2) 30 s session-state sweep (Part 40) - no part40 evidence file and no 30-second statement found; (3) "only intent scopePaths are engine-enforced, at argument level" (Part 42) - delegation.py:101-103 and part52.md:138-139 are adjacent but no file states the "only"; (4) timed-out engine call's tool calls not recorded (Part 44) - no part44 file and no matching text; (5) audit anchor file only as safe as its storage (Part 11) - no part11 file and no "anchor file" text; (6) operator-present rules are manual - final-deliverables.md:113-114 and s2/lane-d.md:47 do not say they are manual; (7) the `--real` smoke has not run - run_smoke.py proves the key requirement but no execution log proves the state claim.

NEEDS-REVIEWER-RUN: confirming the "not configured" half of section 4 item 2 (OpenRouter key) would require reading a secret or .env, which this batch forbids. Confirming the seven Unverified claims needs evidence files that do not exist in this pinned commit, or a reviewer's reading of the code. No step was blocked by a permission denial.

## Security findings

Not applicable to this task (doc only); none raised. No claim about readiness, security or pen-test status was added beyond the source sentences; the section 6 "not cleared for real users" bullet points at the source line and deliberately does not restate the readiness decision.

## Least sure of

I am least sure of the Unverified bucket: the seven §4.8-style items may have evidence in a file outside `docs/briefs/evidence` that my greps missed, so 7 may over-count what is genuinely unverifiable.
