# Checkpoint — Session 6: plans, execution, artifacts, search and export (2026-10-19)

1. **Objective:** parts 44, 48, 50. Part 44 remains partial because native plan versions cannot be
   approved or executed; parts 48 and 50 are verified with the limits listed in the matrix.
2. **Evidence/docs:** `docs/briefs/evidence/s6/{lane-pl,lane-ex,lane-ar,lane-se,session6-summary}.md`;
   `docs/{plans,execution,artifacts,search-and-export}.md`.
3. **Changed:** migrations 0047–0050; five Section 4 routers mounted/configured in the deployment;
   start/claim execution gates; spec-drift and policy-route coverage; OpenAPI + SDK generation;
   Section 7 run-detail workbench for plans, tasks, execution, artifacts, changes, search, export and
   deletion requests.
4. **Verification:** PL 53, EX 66, AR 93, SE 74 tests passed; integrated policy/spec tests 8 passed;
   Section 7 typecheck passed and full Vitest passed 817 tests. Full Section 1: 923 passed, 29 skipped, 21 local
   socket setup errors. Full Section 4: interrupted after 1353 passed, 37 skipped and three
   failures (one performance threshold, two macOS Seatbelt restrictions), stalled at 93%. Lane-file
   Section 4 Ruff clean; Section 1 Ruff fails on three findings in an unrelated untracked audit script. Specs and UI calls match.
5. **Postgres/smoke:** Session 6 PG probe and both PG make probes were unavailable (Docker socket
   missing, port 5499 refused). OTP smoke was attempted once and declined because port 8101 was busy.
6. **Open limits:** tool-call count/in-flight spend are not enforced; execution sweep, search index,
   artifact retention and export cleanup have no deploy schedule. Artifact and export bytes are
   local disk. Git tips are caller-recorded; merge approval does not gate GitHub. No antivirus,
   browser keyboard/axe tests, or live-PG proof.
7. **Next:** Session 7 (49, 51, 57); 57 remains capped at 8 for live provider evidence. Preserve the
   part 44 gap until native version approval and engine execution are connected.
