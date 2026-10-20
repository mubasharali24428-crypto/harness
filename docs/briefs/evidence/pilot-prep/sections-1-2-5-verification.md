# Fresh suite verification — 2026-10-18 (America/Los_Angeles)

These are separate suite runs, not a combined regression. Section 1's full run also supplies Task B's verification; it was not run twice under different labels. No existing tests or production code were fixed for this QA task.

| Section | Command (from repository root) | Passed / failed / skipped / xfailed | Skip reasons | Date |
|---|---|---|---|---|
| 1 | `HARNESS_TEST_PG=postgresql://pilot:pilot@127.0.0.1:5815/pilot HARNESS_REQUIRE_OWNERSHIP=true HARNESS_DATABASE_URL=postgresql+asyncpg://pilot:pilot@127.0.0.1:5815/pilot section1/.venv/bin/python -m pytest section1/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section1.junit.xml` | 654 / 0 / 0 / 1 | No ordinary skips; strict xfail documents raw SMTP error logging | 2026-10-18 |
| 2 | `section2/.venv/bin/python -m pytest section2/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section2.junit.xml` | 39 / 0 / 0 / 0 | None | 2026-10-18 |
| 5 | `section5/.venv/bin/python -m pytest section5/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section5.junit.xml` | 182 / 0 / 1 / 0 | `tests/unit/test_confinement.py:89`: landlock is linux-only (host is macOS) | 2026-10-18 |

All three commands exited 0. Durations/warnings respectively: 57.84s/72; 0.97s/2; 5.96s/1. Full stdout/stderr is retained in `section1.log`, `section2.log`, `section5.log` in this directory. JUnit represents pytest xfail as a skipped element; distinguish the expected defect from ordinary skips using its message and the terminal summary.

Section 1 uses the requested isolated Postgres 18 container, created with:

```
docker run -d --name harness-codex-pg -p 127.0.0.1:5815:5432 -e POSTGRES_USER=pilot -e POSTGRES_PASSWORD=pilot -e POSTGRES_DB=pilot postgres:18
```

Section 5 has no README/section-specific AGENTS.md or opt-in environment switches in its tests; pyproject.toml and confinement tests were inspected. Its real macOS Seatbelt tests ran with host execution permission. Linux Landlock remains unverified on this host. Section 2 tests have no opt-in environment switches. No paid models or external provider credentials were used.

No full-suite failure occurred, so no failure-reproduction rerun was needed. Task B's initial test-authoring failures are separately documented in `../../smtp-otp-tests.md`; they are not omitted from the record. The strict expected SMTP defect remains unfixed. Working-tree evidence is a snapshot of an actively shared checkout, not a committed revision or an assertion that later edits passed these tests.
