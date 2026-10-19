# Product boundary and maturity (Master prompt part 36)

Status as of 2026-10-18. Source of truth for capabilities: `docs/briefs/capability-matrix.md`. Nothing here is marketed until its
matrix row is `verified` (rule 009).

**Maturity.** Pilot release = the first delivery tranche (shared runs, leader, redirect, handoff, digest-bound approval, isolation, audit).
It is not the full product: presence, discussion, policy engine, orgs, search, integrations and compliance lifecycle are later phases
(`docs/roadmap.md`). The pilot is NOT cleared for real users (no measured baseline; see matrix part 03).

**Target users / sizes / tasks / environments.** Small engineering teams (2–10 people) working on one repository at a time, supervising
an AI coding agent on well-scoped tasks (bug fixes, refactors, tests, docs). Environments: self-hosted compose stack on a single host with
Docker; Postgres 18; Next.js UI in a modern browser. Larger orgs and multi-host deployments are out of scope until phase 4.

**Non-goals.** Autonomous merging or deploying without a human decision; hosting untrusted multi-tenant workloads on one host
without the isolation tier; replacing code review; real-time co-editing of files; a general chat product; compliance certification
claims (SOC 2 / ISO / HIPAA) without an audit.

**Excluded high-risk uses.** Production infrastructure changes or secrets handling by the agent; financial trading or fund movement;
medical, safety-critical or legal decisions; operating on repositories the team does not own; processing regulated personal data
(no DPA/counsel review yet — see `PRIVACY.md` placeholders).

**Required vs optional.** Required for pilot: matrix parts 04–12 and 20–32. Optional/advanced: 41–59 (see roadmap phases).

**Dependency order (by risk).** isolation → approval → audit → leader/handoff → realtime → presence/discussion → orgs/policy → integrations →
scale/DR → ecosystem.
