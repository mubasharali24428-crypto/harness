# M17 PHASE A — O-DELTA PRODUCT AUDIT (TEAM-2)

> Seat: O-DELTA (ELITE-FORGE, Strategy/Product) · Baseline **3c794b2**, clean tree · 2026-08-24
> Lens (per mission17-elite-audit-graph.md): product-market — what stands between this and a
> commercial Cursor-class AI engineering harness.
> Method: read-only source audit + doc corpus (README, STATUS, ONBOARDING, EXPANSION_PLAN,
> CLIENTELA parts). No fixes, no git writes. Retry attempt: prior O-DELTA lane died to provider
> HTTP 500 with zero writes (footprint-audited); this file is the sole deliverable.
>
> **Delta discipline:** `docs/clientela/part3-roadmap.md` (2026-08-23, pre-M13) already ranked 10
> adoption killers K1–K10. This audit does NOT re-list what it got right; every item below either
> (a) verifies a K-item still open at 3c794b2, (b) closes one as fixed by M11–M16 waves, or
> (c) adds gaps that doc missed. Merge into Phase C synthesis accordingly.

---

## VERDICT

**Not yet a product; a well-engineered control plane wearing a product's clothes.** The
governance core (human plan gate, event-sourced audit, OS-confined sandbox, manual merges,
team tenancy) remains genuinely differentiated and now actually works end-to-end — the six
CLIENTELA CRITICALs landed (STATUS.md Mission 11 W1; plan gate reachable from UI; login mints
real identity; budgets atomic fail-closed). But judged against the commander's intent — a
commercial Cursor-class harness — the front office is still missing: no codebase indexing or
@refs, no editor surface beyond a read-only tree page, no monetization beyond an uninstantiated
protocol, no enterprise identity story, and no MCP/tool ecosystem. The moat is back-office;
the buyer experience is a governed terminal.

---

## L1 · Time-to-first-value: clone → first useful output

Counted steps from fresh clone to first completed agent run on a real repo:

1. `cp .env.example .env` + set `HARNESS_MASTER_KEY` (.env.example documents ~56 vars)
2. Register a GitHub OAuth app manually (deploy/ONBOARDING.md §GitHub OAuth — 6 sub-steps;
   ships deliberately unconfigured, connect returns 503 NOT_CONFIGURED until done)
3. `docker compose -f deploy/docker-compose.yml up -d` (db + migrate + 7 backends + frontend)
4. Open :3000 → log in (OTP if ownership enforced; dev code read out of container logs)
5. Connect GitHub → create project pointing at seeded `demo-repo`
6. Meeting Area → brief → `@ai` → approve brief → wait for plan proposal
7. Plan Gate card → human approve → run executes in sandbox
8. Review Area pass → draft PR → merge manually on GitHub

**≈ 8 steps, 2 of which (OAuth app, master key) are pure operator ceremony before any value.**
Mitigations that exist and are honest: seed_demo.py + compose migrate seed a demo project;
section10 guided_demo.py (179 LOC) walks the flow; /onboarding derives `done` from real state
only (no fabricated checkmarks — M10 revival, GAP ledger M10-ONBOARD).
Still true at 3c794b2: **no path to value without a GitHub OAuth app for private repos**, and
step 7→8 depends on a live model config (`DSH_MODEL`, default deepseek-chat; engine boot now
fails closed on placeholder creds — deploy/runners/engine_boot.py, C-004 fix).

**[HIGH] TTFV blocker: demo mode has no "canned run."** A stranger cannot see what the product
does without bringing model credentials and a GitHub app. Roadmap N8 ("two-click seeded demo,"
S-size) was never built; guided_demo drives live APIs only.
Evidence: section10/src/harness_onboarding/{guided_demo.py,api.py}; deploy/seed_demo.py;
docs/clientela/part3-roadmap.md §3a N8 (unshipped).

## L2 · Missing product pillars vs Cursor-class tools

| Pillar | Cursor/Windsurf baseline | Harness @ 3c794b2 | Status |
|---|---|---|---|
| Codebase indexing / @file @symbol / RAG | table stakes | none — @ai answers from pasted text + brief only | **OPEN** (=K2) |
| Editor surface | IDE-native | Next.js web app; read-only `/projects/[id]/tree` page is the closest thing; no inline hunk accept/reject | **OPEN** (=K3/K5) |
| Tab completion / daily-habit loop | core interaction | absent, correctly deferred (needs index infra) | OPEN (=K4) |
| Checkpoints / undo | native git | S4 checkpoint-aware resume exists (pause/resume reuses dsh session id), but **no git snapshots / restore-any-point UI**; checkpoints page renders events, not restorable states | PARTIAL (=K6) |
| Rules files (.cursorrules) | universal | none — grep across src finds no rules-file loader; conventions don't stick between runs | **OPEN** (=K8) |
| Per-step model selection | Cursor model picker | **deliberately absent**: engine pins THE configured model, downgrade ladder deleted by law (AGENTS.md "no forced downgrade"); plan contract carries no model field | BY DESIGN (=K9 half) |
| Review UX depth | PR-first, hunk-level | S8 has request-changes + inline comments API (api.py:7–9) + heuristic/risk scoring (232+169 LOC) — real, not stub — but review happens away from edits; no diff-side commenting anchored to hunks | PARTIAL |

**[CRITICAL] Context pillar (indexing/@refs) is still zero** and is the single biggest quality
ceiling: every plan is generated from pasted text, so output degrades on any real codebase.
Unchanged since raw-09 G#1/G#8; nothing in M12–M16 touched retrieval.
Evidence: contracts/src/harness_contracts/brief.py (text fields only); section6/src/harness_meeting/ai_participant.py (regex-routed, context = meeting text); no embedding/pgvector/tree-sitter anywhere in section*/src.

**[HIGH] Rules/conventions memory missing.** Cheap (S-size per X2) and high-leverage: users
already expect AGENTS.md-style convention loading; this very repo runs on one.
Evidence: absence verified by search; roadmap X2 unshipped.

**[MEDIUM] Checkpoints are lifecycle, not safety-net.** `checkpoints/page.tsx` + resume logic
exist, but there is no snapshot/restore of workspace state; a bad run is still unrecoverable
except via git outside the product. (=K6 remainder.)

## L3 · Agent story: multi-model routing, custom reviewers, MCP/tools — real vs stub

- **Multi-model routing: REAL but policy-frozen.** S3 catalog + router + spend metering +
  KeyVault BYO keys all exist and are tested (M10-C1 keys CRUD, wave9 P2-METER ledger writers,
  E-cat catalog w/ pricing coverage assert). But the engine refuses to route: configured model
  is THE model (engine.py:63,135–155; ladder deleted per C-004/H1 law). Fallback chain exists
  only as ctor/env/catalog last-resort. Net: multi-*provider* yes, multi-model *strategy* no.
- **Custom reviewers/planners: STUB-BY-ABSENCE.** One hard-coded `ReviewerSubAgent`
  (reviewer.py:18); reviewer role/model/prompt not configurable per team or per run; budget.py
  accepts a reviewer_role router param but there is no API/UI to define custom reviewer
  personas. Planner is the orchestrator itself; no planner swap point.
- **MCP / tool integration: ZERO.** Repo-wide grep for MCP/Model Context Protocol over all
  .py/.ts/.tsx source returns 0 hits (only fastapi internals inside .venvs match). No tool
  registry, no external-tool allow-list beyond the sandbox command list, no plugin surface.
  For a product whose pitch is "agents do real engineering work," having exactly one upstream
  integration (DeepSeek Harness SDK, pinned 0.1.1rc1) and no MCP story is a category-two gap.
- **What IS real:** sandbox confinement (Seatbelt/Landlock, F6r), durable queue w/ orphan
  reclaim (P1-QUEUE), heartbeat/resume, done-signal gating, fallback honesty guards (D2),
  WS streaming terminal (M2). The execution spine is production-shaped.

**[HIGH] No MCP/tool integration** — blocks the "harness for any agent stack" positioning;
every competitor is racing to be MCP-native.
Evidence: grep result above; task0/VERDICT.md (single SDK); contracts carry no tool-call event type beyond engine lifecycle.

**[MEDIUM] Reviewer/planner customization absent** — fine for v1 governance story, but blocks
"team tunes its own AI engineer" retention loops.

## L4 · Monetization seams: metered vs billable

- **Metering: REAL.** cost_ledger writers fire on every successful call_model incl. reviewer
  calls (wave9 P2-METER; service.py:835–847 budget-gates the reviewer's own spend);
  NUMERIC(12,6) Decimal math (H-023); /spend aggregation + S7 cost dashboard live.
- **Billing: HONEST STUB.** billing.py (292 LOC) implements flag-gated state + admin PUT plan
  + local quota (ledger SUM vs teams.budget_usd, guarded raw SQL for pre-0007 schemas). Stripe
  is a `StripeClient` Protocol — "concrete adapter wired by whoever deploys"; the `stripe`
  package is never imported; a failing adapter silently degrades to local mode. No checkout,
  webhooks, dunning, proration, invoices, tax. PLAN_TIERS = ("free","pro","team") remain bare
  strings with zero behavioral differences.
- **Enforcement: MISSING.** No free-tier caps at the API layer, no seat counting, no feature
  gating middleware; `PUT /billing/plan` guard status unchanged since raw-11 flagged it.
- **Lawful-sale blockers: ALL PRESENT.** No LICENSE file at repo root, no ToS, no pricing
  surface. You cannot charge money for this today — not as a business gap but as a literal
  missing artifact.

**[CRITICAL] Cannot lawfully sell: no LICENSE/ToS/pricing artifacts** (roadmap called this a
NOW blocker in wave-0 terms; still true).
Evidence: `ls` root shows no LICENSE/TERMS; .env.example:170–174 (HARNESS_BILLING/STRIPE_API_KEY stubs); billing.py docstring lines 17–26.

**[HIGH] Zero plan-based enforcement** — tiers are cosmetic; quota comes from
teams.budget_usd regardless of plan; no seat metering ties "team" to TeamMember rows.
Evidence: billing.py:57 (PLAN_TIERS constant, unused elsewhere); grep for run_cap/seat-limit/feature-gate across section*/src = no enforcement sites.

## L5 · Enterprise trust

- **SSO/SAML/OIDC/SCIM: ABSENT ENTIRELY.** Repo-wide grep over source: 0 hits. Auth reality:
  OTP email ownership (H2r, default-on capable), bearer deps fail-closed everywhere (D6),
  GitHub OAuth for repo connection only — GitHub-as-IdP is not wired for login. For any
  enterprise buyer this is a day-one disqualifier, not a roadmap item.
- **Audit completeness: STRONG (the genuine asset).** append-only agent_events + audit_emit
  typed helpers at sensitive sites (W1-A2 closed C-002's zero-call-site hole); replayable seq
  window; conformance goldens pin error envelopes; secret-scrubbed emission.
- **Compliance packaging: NONE.** No SOC2 controls mapping, DPA, subprocessor list, data-residency
  docs, retention-policy export. Telemetry defaults local with opt-in for full prompt/code
  capture (ONBOARDING.md tail; telemetry.py server-enforced) — a genuinely good compliance
  primitive nobody is told about.
- **Ops trust: solid.** WAL-G PITR scripts + runbook w/ validated/not-validated split, backup/
  verify_restore.sh, split containers, healthchecks, rate limiting, security headers, gitleaks+
  pip-audit in CI.

**[HIGH] No SSO/SAML/OIDC** — combined with [CRITICAL] L4 licensing, enterprise motion is
structurally blocked. (=F5 in roadmap, correctly sized L.)
Evidence: grep `\bsaml\b|\bsso\b|\boidc\b|\bscim\b` over *.py/*.ts/*.tsx → 0; section1/src/harness_identity/auth.py (OTP+bearer only).

**[MEDIUM] Compliance pack unbuilt** despite audit trail being the best sales asset — mapping
existing governance claims to SOC2 controls is documentation work (M), not engineering.

---

## TOP-3 KILLER GAPS blocking commercial adoption (ranked)

### 1. [CRITICAL] No codebase context layer — indexing/@refs/RAG (build: LARGE)
The agent is blind. Plans derive from pasted text; @ai sees meeting content only; no symbol
graph, no retrieval, no rules files. On any real repository output quality collapses, which
poisons trial conversion no matter how good governance is. Cursor-class buyers assume this as
table stakes. Postgres+pgvector keeps it infra-free; end-to-end (extraction→embeddings→@mention
composer→plan-context injection→eval harness) is the largest single build on this list.
Evidence: brief.py text-only; ai_participant.py context scope; no pgvector/embedding code in tree; =K2.

### 2. [CRITICAL] Commercial skeleton missing: license/terms/pricing + billing rails + plan enforcement (build: MEDIUM)
Three sub-gaps compound: (a) no LICENSE/ToS/pricing page — cannot lawfully transact at all;
(b) StripeClient protocol with no adapter, webhooks, dunning, or tax — metered-but-not-billable;
(c) tiers with zero enforcement, so even honor-system selling has no product levers. Unlike #1
this is bounded, mostly-known work (checkout session, webhook reconcile, cap middleware at
run-start/project-create, seat counts), and unlocks revenue for everything else.
Evidence: billing.py:17–26,57; root file listing; raw-11 cluster; part3 §4 (all still open).

### 3. [HIGH] Zero MCP/tool integration + no SSO — the two enterprise disqualifiers (build: MEDIUM each)
Buyers evaluating "AI engineering harness" now filter on MCP-nativeness (tool ecosystem =
future-proofing) and SSO (procurement checkbox). Both are currently 0-hit greps. MCP client +
tool-allowlist surfacing through the existing sandbox/forbidden_paths machinery is a natural
fit (the security story becomes the differentiator here: "tools run under the same kernel-
confined allow-list"). SSO via OIDC first (SAML later) slots into get_principal cleanly given
D6's fail-closed dependency architecture. Either alone kills deals; together they cap the
addressable market at solo/self-host enthusiasts.
Evidence: MCP grep 0 hits; sso/saml/oidc/scim grep 0 hits; section1 auth surface; reviewer.py:18 (also relevant: single hard-coded reviewer).

---

## What NOT to fix (explicit non-findings, so Phase B doesn't churn)

- Engine single-model pinning is *law* (AGENTS.md no-downgrade; C-004 lineage) — treat per-step
  selection as a new capability behind explicit opt-in, never a silent fallback.
- Manual-merge-only and plan-gate friction are the product, not friction to remove.
- Honest-empty-state discipline (fabricated IDs purged, flag-off upsells, 503s) is working;
  preserve it under commercial pressure.

— O-DELTA, ELITE-FORGE · M17-A retry · pointer-only delivery per U8/D3
