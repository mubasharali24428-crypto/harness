# known-limits.md draft - 2026-11-06 (hy4 batch 8, K2)

Draft only. Source of truth for this draft is `docs/briefs/PROJECT-STATE.md` sections 4 (Open work, in order) and 6 (Not cleared), read in full from the pinned snapshot `7c1acf4` at `/private/tmp/hy4-b8/snap`. The real `docs/known-limits.md` does not exist in the snapshot and was not created. Nothing here is a readiness, security or pen-test ruling; each limit is a plain restatement of the source sentence, and any wider characterisation is left to the reviewer.

Assumptions: a 'limit' is one distinct open item or unsatisfied condition stated in section 4 or section 6; a source item that contains several limits yields several bullets; the 'Not a gap' line is recorded once under 'Excluded' rather than as a limit; evidence means a file:line in the pinned snapshot that I opened and read, not an inference from the source sentence itself.

## Limits

1. **Limit:** An external penetration test has not been performed, so Part 52 keeps its external cap of 8.
   - **Source:** PROJECT-STATE.md section 4, item 1.
   - **Evidence:** docs/briefs/evidence/s12/part52.md:141 - the Limits list ends with the exact sentence 'No external penetration test. Part 52 keeps its external cap of 8.'
2. **Limit:** Shell-gate path checks are argument-level, so a path built inside a program is stopped only by OS confinement, which keeps writes inside the workspace but not inside the intent scope.
   - **Source:** PROJECT-STATE.md section 4, item 1.
   - **Evidence:** docs/briefs/evidence/s12/part52.md:138-139 - 'Path checks are argument-level ... a path built inside `python -c` is stopped only by confinement'.
3. **Limit:** The shell gate allows one program per call, with no pipes or redirection and no shell state between calls.
   - **Source:** PROJECT-STATE.md section 4, item 1.
   - **Evidence:** docs/briefs/evidence/s12/part52.md:137 - 'One program per call, no pipes or redirection, no shell state between calls.'
4. **Limit:** `run_smoke.py --real` needs an OpenRouter key and a free model to run the real engine.
   - **Source:** PROJECT-STATE.md section 4, item 2.
   - **Evidence:** deploy/pilot/run_smoke.py:8-9 and :46-48 - the docstring says '--real ... Needs HARNESS_OPENROUTER_KEY_FILE and HARNESS_TEST_OPENROUTER_MODEL' and the code raises SystemExit('--real needs {var}') for each.
5. **Limit:** The observability dashboard has not been imported into Grafana and looked at.
   - **Source:** PROJECT-STATE.md section 4, item 3.
   - **Evidence:** docs/briefs/evidence/s12/part28-55.md:48-49 - 'Not done: the dashboard was not imported into Grafana and looked at'.
6. **Limit:** No Grafana image is available on the host, and pulling one was not authorised in that session.
   - **Source:** PROJECT-STATE.md section 4, item 3.
   - **Evidence:** docs/briefs/evidence/s12/part28-55.md:49 - '(no Grafana image on this host, and pulling one was not authorised in this session)'.
7. **Limit:** No screen reader has been used with the UI.
   - **Source:** PROJECT-STATE.md section 4, item 4; section 6.
   - **Evidence:** docs/briefs/evidence/s12/part56.md:44 - 'Screen reader behaviour (no NVDA or VoiceOver run)'; also docs/briefs/final-deliverables.md:109 - 'UI never used with a screen reader'.
8. **Limit:** Only two pages were walked by the browser check.
   - **Source:** PROJECT-STATE.md section 4, item 4.
   - **Evidence:** docs/briefs/final-deliverables.md:109 - 'two pages walked in a browser'.
9. **Limit:** The UI is English only.
   - **Source:** PROJECT-STATE.md section 4, item 4.
   - **Evidence:** docs/briefs/final-deliverables.md:109 - 'English only'.
10. **Limit:** In-flight engine spend is an estimate until the engine completes.
   - **Source:** PROJECT-STATE.md section 4, item 5 (Part 57).
   - **Evidence:** docs/briefs/evidence/s7/part57.md:15 - 'In-flight model spend remains estimated until engine completion.'
11. **Limit:** Data residency is deployment-wide; tenant-specific physical residency is unsupported.
   - **Source:** PROJECT-STATE.md section 4, item 5 (Part 51).
   - **Evidence:** docs/briefs/evidence/s7/part51.md:14 - 'Tenant-specific physical residency is unsupported in this single-region deployment', and :26 - 'physical residency remains deployment-wide'.
12. **Limit:** Artifact, export and diff stores share one Docker volume across orchestrator replicas on a single host.
   - **Source:** PROJECT-STATE.md section 4, item 7.
   - **Evidence:** docs/briefs/evidence/s12/part54.md:84-85 - 'every orchestrator replica in deploy/docker-compose.yml mounts one named volume at /var/lib/harness-store'.
13. **Limit:** Several hosts would need NFS or an object store behind the artifact store interface.
   - **Source:** PROJECT-STATE.md section 4, item 7.
   - **Evidence:** docs/briefs/evidence/s12/part54.md:106 - 'The volume is a local Docker volume (one host); several hosts need NFS or an object store behind artifact_store.put/get/remove.'
14. **Limit:** The shared store volume is not in the backup scripts.
   - **Source:** PROJECT-STATE.md section 4, item 7.
   - **Evidence:** docs/briefs/evidence/s12/part54.md:107 - 'The volume is not in the backup scripts.'
15. **Limit:** Alerts go to one email address plus one optional webhook, with no pager integration.
   - **Source:** PROJECT-STATE.md section 4, item 7 (Part 28); section 6.
   - **Evidence:** docs/runbooks/alerting.md:216 - 'Delivery is the email address plus one optional webhook (section 7): no pager integration'; :280-282 describe the single email receiver and the one optional webhook; docs/briefs/evidence/s12/part28-55.md:83 - 'One email address is still the only alert channel.'
16. **Limit:** Point-in-time restore has been drilled from the live file archive and through self-hosted S3, but not against a cloud bucket.
   - **Source:** PROJECT-STATE.md section 4, item 7 (Part 53); section 6.
   - **Evidence:** docs/briefs/evidence/s12/part28-55.md:85 (section 5 'Point-in-time restore from the live archive'), :109 (section 6 'through S3'), and :139-140 - 'a self-hosted S3 gateway on the same host, not a cloud bucket (no AWS or R2 credentials...)'.
17. **Limit:** The GitHub OAuth token keeps write scope once a project opts in.
   - **Source:** PROJECT-STATE.md section 4, item 8 (Part 49).
   - **Evidence:** docs/briefs/evidence/s12/part49.md:12 - 'GitHub OAuth Apps cannot request read-only repository access, so the token keeps write scope'; :59 - 'The token still carries write scope.'
18. **Limit:** There is no measured pilot baseline: no real pilot cohort or baseline exists.
   - **Source:** PROJECT-STATE.md section 4, item 9 (Part 03); section 6.
   - **Evidence:** docs/briefs/final-deliverables.md:87 - 'Pilot baseline ... not verified: no real cohort'; :108 - 'No measured baseline: no claim about speed or quality is supportable'.
19. **Limit:** Live identity-provider (SSO) flows are unproven.
   - **Source:** PROJECT-STATE.md section 4, item 9 (Part 37); section 6.
   - **Evidence:** docs/briefs/final-deliverables.md:106 - 'Live identity-provider (SSO) flows unproven ... open; part 37 capped at 8'.
20. **Limit:** No counsel review and no penetration test have been done.
   - **Source:** PROJECT-STATE.md section 4, item 9 (Parts 52 and 58); section 6.
   - **Evidence:** docs/briefs/final-deliverables.md:107 - 'No counsel review, no pen test, no residency per tenant ... open; parts 52, 58 capped at 8'.
21. **Limit:** A real point-in-time restore from a live backup has not been done; the Part 53 real-restore cap stands.
   - **Source:** PROJECT-STATE.md section 4, item 9 (Part 53).
   - **Evidence:** docs/briefs/evidence/s12/part28-55.md:81-82 - 'No restore was taken from this backup ... row 53 keeps the real-restore cap'.
22. **Limit:** Full-product acceptance (Part 61) is blocked for lack of real pilot data.
   - **Source:** PROJECT-STATE.md section 4, item 9 (Part 61).
   - **Evidence:** docs/briefs/final-deliverables.md:40 - 'Pilot success and full acceptance | 03, 61 | blocked (no real pilot data; NO-GO report)'.
23. **Limit:** The pilot is not cleared for real users.
   - **Source:** PROJECT-STATE.md section 6, opening sentence.
   - **Evidence:** docs/briefs/PROJECT-STATE.md:103 is the source line itself; the readiness decision is recorded separately in docs/briefs/final-deliverables.md section 10 (line 147) and is deliberately not restated here, so this bullet adds no readiness characterisation beyond the source sentence.
24. **Limit:** The browser checks used synthetic data.
   - **Source:** PROJECT-STATE.md section 6.
   - **Evidence:** docs/briefs/final-deliverables.md:33 - '12: Chromium check on synthetic data, no screen reader'; also :109 lists the same open items.
25. **Limit:** A paused workspace only blocks new starts, by design; this is recorded as not a gap and therefore is not a limit.
   - **Source:** PROJECT-STATE.md section 4, closing 'Not a gap' line (excluded from the bullets above).
   - **Evidence:** section4/src/harness_orchestrator/workspace_gate.py:20-21 - the module docstring says `sweep_inactive_workspace_runs` stops runs when a workspace is archived or deleted, '(a paused workspace only blocks new starts)'.

## Excluded

Bullet 25 above is the section 4 'Not a gap' line, included once for completeness and explicitly marked as not a limit. It is counted in the bullet total below.

## Unverified

The following source claims could not be confirmed from an evidence path. Per the task rule they are listed here with what I found, and were not repaired or promoted into the bullet list.

1. **Claim:** Backups expired outside the scripts (bucket lifecycle rules) are not covered by holds. [section 4, item 6, Parts 31/58]
   - **What I found:** No file in the snapshot contains the phrase 'bucket lifecycle', 'outside the scripts' or 'expired outside'. The two relevant evidence files I opened say the opposite for the in-script case: docs/briefs/evidence/s8/part31.md:12 'Content deletion and backup expiry honor holds (Session 11)' and docs/briefs/evidence/s8/part58.md:9 'Content deletion, backup expiry and account erasure honor holds'. I could neither confirm nor refute the narrower claim about expiry outside the scripts, so I did not repair it.
2. **Claim:** A session state change reaches running runs within 30 s, by sweep (Part 40). [section 4, item 8]
   - **What I found:** No part40 evidence file exists in the snapshot and no file in docs/briefs or section1/section4 source states a 30-second session-state sweep. The nearest sweep I found, section1/src/harness_identity/runner_api.py:1348 `run_nonce_sweeper`, is a nonce sweeper and says nothing about session state.
3. **Claim:** Only intent scopePaths are engine-enforced, at argument level (Part 42). [section 4, item 8]
   - **What I found:** Two adjacent statements exist but neither states the 'only': section4/src/harness_orchestrator/delegation.py:101-103 says the engine's shell gate and container mounts enforce the run intent's scopePaths, and docs/briefs/evidence/s12/part52.md:138-139 says path checks are argument-level. No part42 evidence file exists. I did not upgrade the adjacent statements into the exclusive claim.
4. **Claim:** A timed-out engine call's tool calls are not recorded (Part 44). [section 4, item 8]
   - **What I found:** No part44 evidence file exists and no snapshot file states that timed-out tool calls go unrecorded; a grep for 'timed-out engine' and 'tool calls' across docs/briefs/evidence and section4/src returned no hits.
5. **Claim:** The audit anchor file is only as safe as its storage (Part 11). [section 4, item 8]
   - **What I found:** No part11 evidence file exists and no snapshot file contains the phrase 'anchor file'. A grep across docs/briefs returned no hits.
6. **Claim:** Operator-present rules are manual. [section 6]
   - **What I found:** docs/briefs/final-deliverables.md:113-114 says the operating rules live in docs/runbooks/pilot.md and that pre-flight checks have not been run; docs/briefs/evidence/s2/lane-d.md:47 mentions 'operator-present rules (who, when, checks, authority)'. Neither file states that the rules are manual, and I did not open pilot.md for this claim.
7. **Claim:** The real-engine smoke (`--real`) has not run. [section 6]
   - **What I found:** deploy/pilot/run_smoke.py:8-9,46-48 confirms the key requirement (bullet 4 above), but no evidence file records whether the run has ever happened. The 'has not run' half is a state claim I could not confirm without an execution log, so it is not asserted as a verified bullet.

## Verification

- Bullets in the Limits list: 25 (one bullet per limit; the last one is the 'Not a gap' exclusion).
- Evidence paths opened and read: 15 (listed below). Each cited file:line was checked against the file contents while drafting.
- Unverified claims: 7.
- Total source claims handled: 25 verified bullets + 7 unverified = 32.

Command run (paths relative to the repo root; `SNAP=/private/tmp/hy4-b8/snap`, `REPO=/Users/user/untitled folder 2/harness`):

```
for f in $(cat /private/tmp/hy4-b8/k2-paths.txt); do test -e "$SNAP/$f" || test -e "$REPO/$f" || echo MISSING $f; done
```

Output (no MISSING lines):

```
EXIT: no MISSING lines above means every cited path exists
```

The 15 paths, each opened and confirmed with a line count:

```
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
