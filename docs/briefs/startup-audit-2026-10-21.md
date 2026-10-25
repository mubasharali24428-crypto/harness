# Harness: security, architecture and startup audit

Date: 2026-10-21. Auditor: Claude (single pass, read-mostly). Repo at `f0af9db`, `AGENTS.md` locally modified (not mine).
Status: **partial audit**. Section 12 lists exactly what was not covered. Nothing here is a clean bill of health.
Labels: **[C]** confirmed by a command I ran · **[S]** strongly supported inference · **[H]** hypothesis · **[U]** unknown / not checked.

## 1. Executive verdict

- **Condition:** a large, unusually well-tested integrated prototype heading toward a pilot candidate (1,637 tracked files; last full suite runs this session: 1595 passed / 984 passed, 58 and 54 skipped; matrix: 50 verified, 8 partial). It has **never been run with a real engine key (`--real` smoke) and has no customer baseline**. That makes it a pilot *candidate*, not a pilot.
- **Strongest opportunity:** the *auditable, policy-gated execution of coding agents for a small team* layer (approval gates, forbidden paths, audit chain, cost ledger). Not the whole platform.
- **Most serious risk:** the product's headline promise, "forbidden_paths are hard constraints", is enforced by **string matching on the command line** and is bypassable by an allow-listed interpreter (finding S-01, reproduced locally). Second risk: a solo founder maintaining 11 sections, 60 migrations and 22 services.
- **Recommendation: NARROW.** Pilot with one trusted team only, fix S-01/S-02/S-03 first, and cut scope to one workflow (section 6).

## 2. Scope and what I verified

Ran: `make guard-isolation`, `contracts/ts/generate.py --check`, `ruff` on section1, local policy PoCs against `section5/policy.py` (temp dir, canary file, no network). Read: `AGENTS.md`, `section5/policy.py`, `section3/api.py|service.py|providers.py`, `section1/config.py|crypto.py`, `mcp/tools.py`, CI workflow, LICENSE/PRICING/task0 verdict, `PROJECT-STATE.md`. `HARNESS_PLAN_V2.md` **does not exist** in the repo ([C] `find` returned nothing), so the "full rule list" it is cited for cannot be checked; I used `AGENTS.md`.

## 3. Capability matrix (condensed)

| Capability | Actual state | Evidence | Status | Blocking gap |
|---|---|---|---|---|
| Identity, orgs, audit chain, event delivery | Implemented, large test base | section1 (135 py, 106 test files), migrations 0001-0060 | Verified (tests) | Live SSO unproven |
| Orchestration around DeepSeek Harness SDK | Implemented, SDK pinned `==0.2.1rc2` | `section4/pyproject.toml:17` | Partial: **a release candidate pin** | Real-key smoke never run |
| Command allow-list / forbidden paths | Implemented as string policy | `section5/policy.py` | **Contradictory**: see S-01 | No OS-level path enforcement |
| OS confinement (Landlock/seatbelt) | Workspace-level only | `confinement.py` has no forbidden-path logic [C] | Partial | Fine-grained deny missing |
| Model router / BYOK | Works; global mutable defaults | `service.py:19`, `api.py:389` | Partial: S-02, S-03 | Per-team scoping |
| MCP `start_run` | Auto-approves plans (documented S1-R04c) | `mcp/tools.py:41-67` | Implemented, risky | Human gate bypassed |
| TS contract mirror | **Stale** | `generate.py --check` fails on `events.ts` [C] | Broken | Regenerate + commit |
| Editor/CLI integrations | `clients/` (VS Code, SDKs), `mcp/` | not exercised | [U] | Unverified |
| Observability/alerts | promtool-tested, email+webhook | batch 4 | Verified | Dashboard never viewed |
| Backups / PITR | Drilled on file + self-hosted S3 | evidence/s12 | Partial | No cloud-bucket restore |

## 4. Security findings

### Exploitable / confirmed

| ID | Title | Sev | Conf | Evidence | Impact | Fix | Solo effort |
|---|---|---|---|---|---|---|---|
| **S-01** | `forbidden_paths` bypass via allow-listed interpreter | **High** | [C] | `policy.py validate_command` checks literal tokens, globs and symlinks only. PoC (temp dir, canary): `cat payroll/data.txt` **blocked**; `python3 -c "print(open('pay'+'roll/data.txt').read())"` **allowed and read the file**. `python`, `python3`, `npx`, `pytest`, `cargo`, `go`, `npm` run arbitrary code | Agent (or prompt-injected agent) reads/writes any workspace path, defeating the hard-constraint rule. Mitigations that exist: workspace-level Landlock/Docker, secret-name patterns, env scrub | Enforce at OS level: bind-mount forbidden dirs out of the sandbox (or Landlock deny-by-default with explicit allow of non-forbidden subtrees); run with no network; treat the allow-list as UX, not security | 2-4 days | Test: forbidden dir unreadable via `python3 -c`, `pytest` conftest, `node -e` |
| **S-02** | Any "writer" can change a **process-wide** model default | Med | [C] | `api.py:389 configure_role_model` → `service.py set_role_model` mutates `self.role_models` on a module-level `_service`; no team scope | One team changes the model (and cost) for all teams, silent downgrade/upgrade, violates "no forced model downgrade" | Make role→model a per-team setting in Section 1; remove or admin-gate `/config` | 1 day | Two-team test: A's change must not affect B |
| **S-03** | `opts.extra` overrides the model actually sent; response and cost report the *requested* model | Med | [C] (code path; not run against a provider) | `providers.py:139-140 payload.update(opts.extra)` after `"model"` is set; `ModelResult.model`/cost computed from the catalog name | Integrity of audit/cost ledger; user can route to a pricier or unlisted model than billed/recorded | Whitelist `extra` keys, forbid `model`; record the provider-echoed model | 0.5 day | Unit test: `extra={"model":"x"}` rejected |
| **S-04** | MCP `start_run` auto-approves plans | Med | [C] | `mcp/tools.py:41-67` (documented decision) | Approval gate is skippable for anything holding the service principal; "human approves plan" claim is false on this path | Require a human approval token, or drop auto-approve outside a dev flag | 1 day | Test: MCP start leaves run in AWAITING_PLAN_APPROVAL |

### Hardening / delivery

| ID | Finding | Conf | Note |
|---|---|---|---|
| H-01 | Master key default `dev-master-key-change-me-in-prod!` is in source in sections 1, 6, 8, 11 and runners | [C] | Compose has a boot gate (`test_deploy_boot_gate.py`, `HARNESS_ALLOW_DEV_MASTER_KEY`); running a section directly would use the public default. Fernet master key from env is also single-key, no KMS |
| H-02 | CI declares no `permissions:`; actions pinned by tag (`@v4`, `gitleaks-action@v2`), not SHA; one scan step is `continue-on-error` | [C] | Default token scope; supply-chain exposure |
| H-03 | Allow-list includes `git checkout/reset/clean/stash/pull/fetch` and `git log --output=<path>` passes (PoC) | [C] | Writes outside workspace possible at policy level; again OS confinement is the real control |
| H-04 | SDK pinned to an `rc2` | [C] | Re-run `task0/hello.py` per bump is a stated rule; I did not re-run it |
| H-05 | `/models/metrics` is unauthenticated by design | [C] | Route-count only |
| H-06 | Provider base URL is env-only (`DEEPSEEK_BASE_URL`), not request-controlled | [C] | No request-driven SSRF seen in section 3; other sections [U] |

### Repo-rule verification

| Rule | Result |
|---|---|
| Sections import only contracts | **FAIL** `make guard-isolation`: 5 violations, all in tests/UI tests reading sibling files (e.g. `section4/tests/.../test_budget_delivery_live_pg.py:77`, `section7/.../notifications.test.tsx:105`). Source (211 files) is clean; the guard's own scope note says tests may pin parity, so this is a **gate/doc mismatch**, not a source violation [C] |
| Frozen contract + TS mirror in sync | **FAIL** `events.ts` stale since "Part 14 schemaVersion" (`13652f2`) [C] |
| Section 1 lint clean | **FAIL**, 3 × E501 in `tests/test_logout_revocation.py:28`, `test_sessions_lifecycle.py:144,146`. Last touched by my batch-3 commit `9680ddb`, so at least these are **my regression**; fix is trivial |
| No auto-merge / no forced downgrade | Auto-merge: not found in the paths I read [U overall]. Downgrade: S-02 shows the *mechanism* exists |
| Secrets not in logs/model context | Provider layer scrubs key from error text (`providers.py`, `_scrub`) [S]; not exhaustively traced [U] |
| Migrations numbered, never edit 0001 | Not re-checked this pass (selftest exists) [U] |

## 5. Architecture and engineering gaps

1. **Scope vs one maintainer** (blocks paying customers). 11 sections + packages + clients + 22 compose services. Defer: billing/Stripe adapter (Stripe does not support Pakistan; see section 8), multi-replica work, SSO, residency, Grafana. Keep: runs, approvals, audit, cost ledger.
2. **Policy-by-string** (blocks pilot): S-01. Smallest fix: OS-level deny + no network in the sandbox; mature option: gVisor/Firecracker later.
3. **Contract hygiene** (blocks nothing, erodes trust): regenerate TS mirror, fix lint, decide whether the isolation guard excludes tests. 2-3 hours.
4. **Evidence gap:** no real-provider run; a `--real` smoke (~$1 of tokens) is the cheapest way to find integration failures.
5. **Licence:** MIT is a placeholder with an in-file "decision point" comment; if the plan is open core or hosted, decide **before any distribution** (licence applies from first distribution). `PRICING.md` has no dollar amounts.

## 6. Pilot specification (smallest credible)

- **Customer:** one small trusted team (2-5 developers) already using an agent CLI and nervous about it touching secrets/prod files. Candidates in your network first.
- **Workflow:** "Run an agent on a repo task inside a policy sandbox → human approves plan → agent runs with forbidden paths enforced → reviewer sees diff + audit trail → cost per run shown."
- **Before/after:** before, the agent runs on the dev's laptop with their full access; after, it runs with an enforced deny-list, an approval record and a cost line.
- **Exclusions:** billing, SSO, multi-team, residency, MCP auto-start, marketplace.
- **Gates:** S-01..S-04 fixed with regression tests; `--real` smoke green; TS mirror and lint clean; master key set and boot gate on; one restore drill; `HARNESS_PILOT_TEAMS` set to the single team (allowlist semantics).
- **Telemetry:** run count, approval latency, failure reasons, cost per run; no prompts or code content.
- **Duration:** 4 weeks, ≤10 founder hours/week support. **Success:** the team runs ≥10 real tasks unaided and says they would pay. **Stop:** no unaided use by week 2, or any confirmed sandbox escape.

## 7. Market (research dated 2026-10-21; secondary sources, verify before relying)

- Open-source BYOK agents (Cline, Roo Code, Aider) are free and bill via your own provider key; subscription tools (Claude Code, Cursor) sell seats around $20-$200/month, with metered overage. So **BYOK alone is not a differentiator, and it is the free default**. [sources: pricepertoken, agentdeals, morphllm coverage; one search, blogs not vendor pages]
- Therefore the sellable value is **governance**: enforced limits, approvals, audit, spend control for teams. Whether anyone pays for it is **[H]**, to be tested by interviews, not asserted.
- Easily copied: BYOK, model routing, multi-agent UI. Possibly defensible: audit/evidence quality, policy that actually holds (hence S-01 matters commercially).
- Segments: (1) small agencies/dev shops using agents on client code, who need to prove control to clients **(recommended: reachable remotely, small buying chain, pain is liability)**; (2) security-conscious startups; (3) university/lab teams (cheap, low pay). Invalidated if 10 interviews show nobody has been blocked or burned by agent access.

## 8. Pakistan eligibility and money (evidence dated 2026-10-21)

| Program | What I confirmed | Pakistan eligibility | Status |
|---|---|---|---|
| AWS Activate Founders | $1,000 start, up to $5,000; pre-Series B, ≤10 yrs, paid-tier AWS account ([aws.amazon.com/startups/credits](https://aws.amazon.com/startups/credits)) | **Unconfirmed** (page names no country rule) | Apply when you have a company website; real award will be small |
| Microsoft for Startups | page: "up to $150,000" Azure credits ([microsoft.com/startups](https://www.microsoft.com/en-us/startups)) | **Unconfirmed**; eligibility page not read | Check Founders Hub terms; headline ≠ initial award |
| NVIDIA Inception | Free; **must be incorporated**, website, <10 yrs, pitch deck ([nvidia.com/startups](https://www.nvidia.com/en-us/startups/)) | Unconfirmed; incorporation blocks you today | Low value: GPU focus does not match a BYOK orchestrator |
| Google for Startups Cloud | page fetched had no programme terms | **Unknown** | Not verified |
| Pakistan Startup Fund (Ignite / MoITT) | Equity-free grant up to ~30% of a round, **after private investor commitment**; ~250 applications received, 39 funded this year; official channel unclear ([ProPakistani, June 2026](https://propakistani.pk/2026/06/24/pakistan-startup-fund-moves-to-hire-travel-agency-despite-slow-startup-disbursements/)) | Pakistan-registered required (secondary guides) | Category C: needs investor and registration |
| National Incubation Centers (Ignite) | New NIC Sialkot cohort applications open per [ProPakistani, June 2026](https://propakistani.pk/2026/06/09/ignite-and-mobilink-bank-partner-to-establish-national-incubation-center-sialkot/); Lahore: 50 incubation + 10 acceleration slots/yr, PKR 5M acceleration | Local programs | Worth applying now; confirm terms on the NIC site |

I did **not** research: YC/other accelerators, GitHub Sponsors/open-source funding, Pakistani university programs, angel networks, OpenRouter/AI-provider credit programs. Listing them without checking would be guessing. That is the next research task.

**Payments (facts found):** Stripe's supported-country list does **not** include Pakistan ([stripe.com/global](https://stripe.com/global)). Paddle says it works with software businesses anywhere except a listed set of unsupported countries and Pakistan is not on that list, but the page does not confirm payout to Pakistan ([Paddle help](https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle)). Payoneer is the main inbound channel for freelancers; PayPal availability is reported as unsettled, with a pilot announced in January 2026 ([Aaj](https://english.aaj.tv/news/30347457), [Arab News](https://www.arabnews.pk/node/2623262/pakistan)). Polar's page redirected and I did not read it. Tax, registration and banking: **not researched**; needs a Pakistani accountant. This is not tax advice, and I am not recommending a US company yet.

## 9. Budget scenarios (planning estimates, not quotes)

| | $0 | ≤$50/mo | ≤$150/mo |
|---|---|---|---|
| Runs | Local/one VPS credit, self-host for the pilot team, SQLite/PG on one box | One small VPS (~$10-25) + domain + backups to cheap object storage | Two VPSs (app + sandbox worker), monitoring, email |
| Not covered by BYOK | Postgres, storage, logs, sandbox compute, support time, failed runs | same | same |
| Constraint | Your hours are the cost; credits expire (AWS/Azure) and then you pay list price | Single host, no HA | Still no pager/on-call |
| Upgrade trigger | First paying team, or >1 concurrent pilot | Revenue ≥ 3× monthly cost | Second paying team |

## 10. Roadmap (hours are estimates)

- **Days 1-7 (must before pilot):** fix lint + regenerate TS mirror (3 h); S-03 + S-02 with tests (12 h); decide MCP auto-approve (S-04, 6 h); start S-01 design (8 h).
- **Days 8-30:** S-01 OS-level enforcement (20-30 h); `--real` smoke with a funded OpenRouter key (4 h, ~$1-5); CI `permissions:` + SHA pins (3 h); 10 customer interviews (15 h); decide licence (2 h).
- **Days 31-60:** pilot with one team (≤10 h/wk); landing page + 2 technical posts; apply to AWS Activate Founders and the NIC programme.
- **Days 61-90:** convert or stop; pricing test; accountant consult on receiving payments.
- **Months 4-6 (only if demand validated):** payment rail, second sandbox backend, SOC2-lite docs.
- **Defer/avoid:** Stripe billing, SSO, multi-replica, Grafana, residency, Kubernetes, GPU programmes.

## 11. Marketing (founder-led, near-zero cost)

Positioning: *"Run coding agents on your repo with limits that actually hold: approvals, enforced no-go paths, an audit trail and per-run cost, on your own keys."* Weekly cadence: 1 technical post (e.g. "why string allow-lists don't contain agents", "auditing agent runs", "per-team model policy", "cost per run", "BYOK is not governance"), 5 personalised outreach messages, 2 interviews. Experiments: (1) post the S-01 write-up and ask for sandbox-escape attempts; (2) 10 agency interviews; (3) one demo video to a small-team list. Metrics: interviews held, pilots started, runs per week unaided, willingness-to-pay statements. No paid ads. Do not claim traction you do not have.

## 12. Not covered / open questions

Not examined: authentication/token expiry, WebSocket auth and replay, tenant isolation tests beyond what exists, SQL injection review, rate limiting, prompt-injection testing, container privileges (compose ports not checked for 127.0.0.1 binding, the grep was inconclusive), dependency CVE scan, editor/VS Code client, migrations safety, the real engine. Market research was a single-pass web search with secondary sources; program eligibility for Pakistan is **unconfirmed** for every program except where noted. A clean result in any of these would still not prove security.

## 13. Five actions first / five to postpone

First: (1) fix S-01 at the OS layer; (2) S-02/S-03; (3) run the real-key smoke; (4) 10 interviews; (5) regenerate TS mirror + lint.
Postpone: Stripe billing, SSO, Grafana, multi-host scale, GPU/NVIDIA programmes.

Assumptions most likely to kill it: (a) teams will pay for governance when free BYOK agents exist; (b) one person can keep 11 sections secure; (c) payments from Pakistan can be sorted cheaply.
Evidence needed before spending real money: 3 teams using it unaided and ≥1 stating a price they would pay.
**Decision: narrow.**

## S-01 status update (2026-10-21)

`forbiddenPaths` means **do not modify**: the demo plan forbids files that pytest and ruff must read. Enforced now: read-only bind mounts in the container (paths existing at launch), a Seatbelt deny rule, the shell gate refusing to run when a forbidden path is still writable, and a hash check after each run that fails the run if a forbidden path changed or appeared. Not covered: confidentiality (reads). That would be a separate "hiddenPaths" field with a contract bump; add it only if a customer needs it.
