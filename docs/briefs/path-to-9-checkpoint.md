# Current checkpoint — Session 10 local work complete (2026-10-19)

**Claude verification pass (after Codex Sessions 6-10):** Claude re-ran the suites, found and fixed the stale-fixture and savepoint
regressions (`6b6adfb`), and closed part 44 (`00a1412`, `evidence/s6/part44-version-approval.md`: version approval that the engine
runs, scheduled limit sweep). Part 44 is `verified` with the tool-call-reporting gap stated. Part 57's cap-8 removal ("no Stripe") was checked against
the Codex session log: it follows the owner's direction. Codex's Session 9/10 text below is otherwise unchanged except where marked.

1. **Objective:** parts 56, 59, 60, 03 pilot tooling and 61 readiness. Prompt and directive map:
   `docs/briefs/path-to-9-session10-prompt.md` and `docs/briefs/evidence/s10/`.
2. **Work:** added client/language support and release-compatibility runbooks. Pilot baseline
   ingestion now checks team match, dates, method, unique task IDs, finite durations and boolean
   acceptance before DB access; the ≥5-per-side/no-causation gate remains. Wrote a cross-domain
  NO-GO readiness report. Reviewed locally actionable partials 49/51/57; confirmed current working
  tree already implements webhook controls, owner-configurable dual approval/shorter retention,
  and atomic daily reservations/durable tool-call limits. Remaining provider, residency, in-flight
  spend and alert-delivery gates need provider or deployment work. Updated matrix rows 03, 56, 59–61.
3. **Verification:** Section 7 accessibility/reduced-motion/responsive suite 94 passed; TypeScript
  typecheck passed. Section 4 pilot metrics 31 passed, 1 skipped without `HARNESS_TEST_PG`;
   Ruff passed. Section 1 migration drift-gate tests 6 passed. Exact commands are in
   `docs/briefs/evidence/s10/verification.md`.
4. **State:** part 03 remains blocked pending real comparable pilot tasks; part 56 remains partial
   pending browser/screen-reader/contrast/localization evidence; part 59 remains partial pending
   PostgreSQL rollout/rollback evidence. Part 60 is verified as a roadmap artifact only. Part 61
   remains blocked; broad rollout is NO-GO. No pilot was run or result invented.
5. **Environment:** no production deploy or customer communication. Docker/PostgreSQL, live pilot,
   browser assistive-technology review, Alertmanager delivery and external security/provider gates
   remain unavailable or unperformed. Earlier Session 7–9 working-tree edits were preserved; no
   commit or push was made.
6. **Next session:** obtain a real supervised pilot cohort and baseline; run the release/migration checklist
   on a scratch PostgreSQL/deployment stack; complete real-browser accessibility and operations
   drills. Reassess the NO-GO only from those recorded results.

---

# Historical checkpoint — Session 9 (2026-10-19)

1. **Objective:** parts 53–55 (reliability, scalability and observability). The Session 9 prompt is
   `docs/briefs/path-to-9-session9-prompt.md`; initial directive map is
   `docs/briefs/evidence/s9/directive-map-and-verification-plan.md`.
2. **Work:** added proposed measurable SLOs, explicitly bounded capacity/load scenarios,
   safe support-diagnostic guidance and operational alert caveats in
   `docs/runbooks/slo-and-capacity.md`, and an importable Grafana dashboard definition at
   `deploy/observability/dashboards/harness-overview.json`. Updated matrix rows 53–55. Existing historical load,
   Prometheus and runbook artifacts were mapped; they were not rerun or represented as fresh proof.
3. **Verification:** `evidence/s9/verification.md` records Section 1 (30 passed, 1 skipped),
   Section 4 observability/breaker/recovery (30 passed), Ruff for touched Python, dashboard JSON,
   and `git diff --check`. A restart regression uncovered that SQLite JSON text was treated as a
   list of characters; decoding was fixed and the recovery suite now passes. Rows 53–55 remain
   partial. A route-level p95 alert now matches the documented 250 ms target, but PromQL full-parser
   validation was skipped because `promtool` is unavailable. Alertmanager delivery is not configured;
   event freshness lacks a confirmed end-to-end histogram; deployed multi-replica capacity is unverified;
   dashboard import/visual review has not occurred.
4. **External gate:** part 53 remains capped at 8 until a real S3-compatible WAL-G backup/restore
   round trip demonstrates recovery point and recovery time. No such live-store evidence was added.
5. **Working tree:** earlier Session 7/8 and unrelated edits remain mixed in the checkout. This
   Session 9 start did not commit, stage, or rewrite those changes.
6. **Next:** resume the isolated load ladder when Docker is available; add deployed failure-injection
   evidence and event freshness instrumentation; provision alert routing and Grafana only for a
   configured operator environment. Do not claim Session 9 complete until evidence and matrix agree.

---

# Historical checkpoint — Session 8 (2026-10-19)

1. **Objective:** parts 52, 58 and 31 enforcement. The directive map and verification plan are in
   `docs/briefs/evidence/s8/directive-map-and-verification-plan.md`; reports are in `evidence/s8/`.
2. **Work:** refreshed `docs/data-map.md`; reviewed auth/audit/secret handling, exports/deletion,
   retention, sandbox policy and live SDK execution. Found SDK `sdk-minimal` defaults to
   `danger-full-access`; Section 4 now forces `sdk-minimal` and appends a per-run `workspace-write`
   overlay. Plans with non-empty `forbiddenPaths` fail closed before SDK invocation; the SDK shell
   still bypasses Section 5's command allow-list and cannot selectively enforce those paths.
3. **Verification:** Section 5 policy/isolation (73 passed, 2 deselected; full selected set has two
   Seatbelt failures on own-workspace/private-temp writes); Section 4 exports/deletion/retention 44
   passed; Section 1 security/audit 29 passed, one fixture-schema failure deselected. Section 5
   Section 4 engine/policy tests 27 passed, 1 skipped; new forbidden-path/overlay checks 14 passed,
   1 skipped; retention-hold checks 6 passed. Composed SDK profile dump confirms overlay.
   Ruff passed. Exact commands and failure details are in `evidence/s8/session8-summary.md`.
4. **State:** parts 31, 52 and 58 remain partial; 52/58 remain externally capped pending independent
   pentest/counsel evidence. Migration 0056 now has audited team-admin hold routes; run purge and
   event/audit retention honor held runs, including PostgreSQL event-partition retirement. User
   deletion and backup expiry do not honor holds. Account erasure, tenant residency and the live SDK
   command boundary remain open. PostgreSQL/Docker/production controls not verified.
5. **Working tree:** Session 7 prerequisite commit `f583d89` exists locally; its continuation edits
   and extensive unrelated changes remain uncommitted. None were committed or rewritten for Session 8.
6. **Next:** design the common Section 4/Section 5 enforcement seam and extend holds to user deletion
   and backup expiry. This continuation's new API/retention changes still need targeted test execution.
   Session 9 has not started.

---

# Historical checkpoint — Session 7: integrations, administration and billing (2026-10-19)

1. **Objective:** parts 49, 51 and 57. This completion pass adds a narrower GitHub OAuth default,
   configurable dual approval and retention windows, atomic daily run reservations, and durable
   per-run tool-call enforcement. All three parts remain partial for the gaps listed below.
2. **Evidence/docs:** `docs/briefs/evidence/s7/{part49,part51,part57,session7-summary}.md`;
   `docs/{integrations,billing-and-quotas,organizations,permissions}.md`.
3. **Changed:** migrations 0051–0055; versioned team-scoped outbound webhook API/outbox/worker;
   Section 7 integrations/governance pages; audited org execution stop; dual-approval policy queue;
   org retention windows and pruning; atomic daily run reservations and durable tool-call caps;
   identity OpenAPI and generated Python/TypeScript SDK updates.
4. **Verification:** Original Session 7 checks are recorded in `evidence/s7/session7-summary.md`.
   Completion-pass checks: Section 1 org/policy/retention tests 80 passed, 2 skipped; Section 2
   GitHub OAuth tests 11 passed; Section 4 reservation/tool-limit focused checks pass (the two fixture
   failures seen then were fixed in `6b6adfb`; full Section 4 is 1464 passed, 38 skipped); Section 7 typecheck passes.
   PostgreSQL migration execution was not available.
5. **Postgres/provider:** No PG migration or live provider delivery was run. Prior checkpoint records
   the unavailable Docker socket and local Postgres port; this session does not claim those checks
   passed. Stripe is intentionally out of scope.
6. **Open limits:** GitHub OAuth classic `public_repo` still permits public-repository writes; no
   issue-tracker/chat adapter or extension sandbox; physical data residency remains deployment-wide;
   in-flight engine spend is estimate-based and warning delivery is not wired; active-run execution
   stop is cooperative. Part 44 stays partial.
7. **Next:** Session 8 prompt is `docs/briefs/path-to-9-session8-prompt.md` (parts 52, 58, and 31
   enforcement). Do not treat this checkpoint as permission to start it.
