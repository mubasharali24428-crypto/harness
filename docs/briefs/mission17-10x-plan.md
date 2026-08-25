# MISSION 17 · PHASE C — THE 10X PLAN

> **Co-authored:** O-DELTA (ELITE-FORGE, product/strategy) × PBC1 (Elite, scale/systems) — one document, two lenses, disagreements marked where they exist.
> Baseline **c5c70ca** (M17-B landed). Inputs: all seven `m17-findings-*.md` @ 3c794b2 + Phase B salvage records (malpha §B1, mac2 re-dispatch) + `STATUS.md` + `docs/clientela/part3-roadmap.md` K1–K10. No code changes; disk-delivery law; final message = pointer only.
>
> **Honesty frame:** every "10x" below is a measured-or-derived number against a named current-state metric, not aspiration. Where a claim rests on an estimate it is tagged [E]; measured facts carry file:line or a findings-file citation. Phase B already closed some Phase A items — the baseline below is **post-c5c70ca**, and each item says whether its finding ID is still OPEN or CLOSED-by-B.

---

## 1. BASELINE — where the product actually is today

**Product axis (O-DELTA).** A well-engineered governed control plane wearing a product's clothes: human plan gate reachable from UI, event-sourced audit with typed emission, OS-confined sandbox (Seatbelt/Landlock), manual merges, team tenancy, budgets atomic on the reserve path — all real and tested. The front office is still missing: zero codebase indexing/@refs (plans derive from pasted text only — `brief.py` text fields, no pgvector anywhere), read-only tree page as the closest thing to an editor, billing that meters honestly but cannot lawfully charge anyone (no LICENSE/ToS/pricing artifact at repo root; StripeClient Protocol never instantiated; PLAN_TIERS are behavior-free strings), zero MCP/tool integration (repo-wide grep = 0 hits), zero SSO/OIDC/SAML/SCIM (0 hits). *(O-DELTA findings L1–L5; nothing in B1–B6 touched these.)*

**Scale axis (PBC1).** The architecture survives demo load and breaks at specific, countable walls between ~50 and ~1,000 concurrent users. Post-B the earliest wall moved: engine concurrency is now bounded by design (`engine_runtime.py`, 327 LOC + 420 test LOC) instead of an invisible 16–40-thread default — but the orchestrator remains a **stateful singleton** (`service.py:186-190` `self.runs` dict, never evicted; `plans` dict holds approvals in RAM; one uvicorn process, `entrypoint.sh:81`), so horizontal scale is still ~0 replicas. NOTIFY fan-out still ships full payloads (F3), `agent_events` is still unpartitioned (F4), budget gates still full-ledger SUM with **zero indexes on cost_ledger beyond PK** (F6 — verified: no index migration exists through 0026). Breaking points from the findings file stand except where noted in §3.

**DX/ecosystem axis (MAC1).** Massively improved by B6: `make bootstrap` creates the eight venvs (F-1 closed), README/Quick-start accuracy pass landed, `docs/TUTORIAL.md` now exists (F-5 partially closed), envelope middleware installs at deploy assembly for all backends (F-2 closed via contracts promotion, commit 798fdbc). Still true: no client SDK in any language (both working clients trapped in section7 `lib/api.ts` 567 LOC + `useEventStream.ts` 347 LOC), extension seams undocumented (project-rules loader is the best extension point in the repo and its best-kept secret — F-6), stale `harness_fresh` psql command still in AGENTS.md (F-7).

**Ops axis (S-GAMMA).** B2 wired live-PITR onto the real cluster and put migrate.py into CI against PG (OPS-01/15 closed). Still open and ugly: **no rollback or zero-downtime path whatsoever** (OPS-24 — grep `rollback|blue-green` over docs+deploy = 0 hits; forward-only migrations; images retagged in place so old builds dangle), metrics on ONE service of nine with nothing scraping them (OPS-21), no crontab on the host (OPS-02), zero resource limits on containers sharing a kernel with Postgres (OPS-03), healthchecks liveness-only (OPS-06).

**Architecture axis (P-BETA).** Contracts promotion (B3) moved cost_ledger/limits/ratelimit/pricing/timeutil/reviewer_thresholds into `harness_contracts` and killed the byte-identical forks; isolation guard upgraded to catch package-name imports (C-1 closed). Still open: **authz.py triple fail-open** (verified present at HEAD: `factory is None → return`, `not project_id → return`, dangling row → allowed — H-3/MAC2-F1), TS mirror marks always-serialized fields optional (H-1), four tautological asserts (H-2), golden fixtures per-section instead of shared (M-2).

**Correctness axis (M-ALPHA).** Money math is now genuinely sound post-B1: reserve() executes on asyncpg (named→positional translation), team-level `pg_advisory_xact_lock` serializes concurrent reserves (probe: 5 winners vs $5 budget, six rounds, zero violations — was 8/8 overspend), NUMERIC overflow rejects honestly. Open: three disagreeing budget caps with opposite failure postures (F-3 — live gate fails OPEN on DB error, `budget.py:144`; guard fails CLOSED; UI takes max), meeting messageSeq positional over volatile memory (F-6), done-signal double-fire → 500-not-409 (F-8).

**Security axis (MAC2).** B4 closed the two worst: plan_gate is now team-scoped (374-line regression suite), rate limiting keys IPv6-/64-aware with proxy policy. Open: invite redemption binds to token not invited email (F3 — admin role joinable via forwarded mail), WS fingerprint bypassable by omitting header (F5), long-lived session token accepted via `?token=` URL param (F7), rate_buckets never GC'd (F9).

---

## 2. THE 10X AXES — current metric → 10x target

| Axis | Current state (measured) | 10x target | Why honest |
|---|---|---|---|
| **Scale headroom** | ~16–40 concurrent runs pre-B (thread-pool default); post-B bounded but **1 orchestrator replica max** (split-brain approvals at 2); first hard walls: ~120 PG connections (compose `max_connections=120`, pool-math 88 worst-case), >8KB notify payloads break inserts today (F3), ~10–30M event rows before vacuum/debt curve wins (F4) | **~400–500 concurrent users / ~10M events/day before first wall** — i.e. one replica → N stateless replicas, notify slimmed to signals, partitioned retention, PgBouncer | Every sub-wall has a named fix with a findings-file estimate; none requires new infra categories beyond Redis/broker (optional) |
| **Agent throughput** | Runs/day unmeasured but capped by: 4/team running (HARNESS_RUN_CONCURRENCY), single process, reviewer spend unbounded per run (repeated REVIEW_PASSED→EXECUTING cycles, service.py:95-109), resume-after-reap loses mid-flight work (F10 multiplier #1) | **≥500 completed runs/day sustained at p95 run-start latency <500ms** (budget gate must stop paying a growing SUM tax — F6) at ≤$0.25/run blended model spend | Throughput is gated by queue+pool math (countable), latency by the F6 index/rollup (one migration), cost by existing metering — all measurable in CI probes |
| **Time-to-first-value** | MAC1 timeline: time-to-login ≈35 min (good), **time-to-first-value ≈60–80 min AND provider-key-gated** (dry-mode dead end, F-4 closed by B6's quick-start fix but demo still needs a real key), time-to-first-extension ≈ days | **≤8 minutes clone→first successful agent run** via canned seeded demo replayed from recorded events (roadmap N8, never built), zero provider key required; ≤30 min to first scripted API call via published SDK | N8 was sized S two roadmap generations ago and never built — this is the cheapest 10x on the board |
| **Ecosystem surface** | Integration points: exactly **1 upstream SDK** (deepseek-harness-sdk pinned), 0 MCP tools, 0 plugins, 0 client SDKs, 3 undocumented extension seams, 39 typed routes of 76 | **≥10 integration points**: MCP client + tool registry, published Python+TS client SDKs generated from OpenAPI, documented plugin seams (engine/reviewer/rules), webhook egress (outbox exists), 76/76 typed routes | MCP-native is table stakes buyers now filter on (O-DELTA L3); everything else compounds off the frozen-contract discipline the repo already has |

---

## 3. THE PROGRAM — four horizons

Effort tags [S]≤3d · [M]≤2wk · [L]>2wk · [XL] multi-week program. "Lanes" = parallel delegate_task lanes at the wave cadence this repo already runs (5–7 lanes/wave verified across M10–M17-B). Trace format: finding file seat + ID.

### HORIZON NOW — "stop the bleeding, prove the walls" (1 wave, 6 lanes)

Ordered by dependency; every item is either a correctness/security hole still open at HEAD or the measurement layer without which "10x" stays unverifiable.

| # | Mission | What | Size | Lanes | Trace |
|---|---|---|---|---|---|
| C1 | **Authz fail-closed sweep** | Kill the triple fail-open in `section4/src/harness_orchestrator/authz.py:50-66` (unwired factory ⇒ deny in prod wiring, allow only behind explicit test flag; missing projectId ⇒ deny + audit_emit; dangling project row ⇒ deny). Same posture pass over plan_gate's new scoping. | [S] | 1 | pbeta H-3 · mac2 F1 (OPEN — verified at c5c70ca) |
| C2 | **One budget truth** | Unify cap resolution teams.budget_usd > billing_plans.monthly_budget_usd > env > unlimited; route S4 `_budget_gate` through the now-working `reserve()` advisory-lock path; single fail-CLOSED posture with explicit degrade flag; one month-window definition (DB clock, `date_trunc`). | [M] | 1 | malpha F-3 (+F-9 None-run_id foot-gun) |
| C3 | **Cost-gate latency kill** | `CREATE INDEX ON cost_ledger (created_at)` + `(team_id, created_at)` via new migration 0027 (none exists — verified through 0026); optional daily rollup table fed by outbox pattern. Gate reads rollup row. | [S] | 1 | pbc1 F6 |
| C4 | **Throughput truth probe** | Load harness in CI: k6/locust profile driving propose→approve→run against compose PG; publish measured concurrent-runs ceiling, run-start p50/p95, events/day capacity to `docs/benchmarks/`. This is the ruler for every later claim. | [M] | 1 | derives from pbc1 F2/F10 (post-B re-measure needed) |
| C5 | **Invite + ticket hygiene** | Invite redemption binds email (or one-time claim-link pattern); drop `?token=` long-lived WS fallback; require X-WS-Fingerprint when mint carried one; opportunistic `rate_buckets` GC (ws_tickets sweep pattern). | [M] | 1 | mac2 F3/F5/F7/F9 |
| C6 | **Rollback story v0** | Image version tags + `docker compose down && up` rollback runbook + deploy-time schema-drift gate (refuse start if applied ≠ declared migrations — OPS-16 residual). Not blue-green yet; just make bad deploys *recoverable*. | [M] | 1 | sgamma OPS-24/16 residuals |

**NOW exit criteria:** authz cannot silently allow; budget caps agree under race probes; cost gate is O(1) at any ledger size; the throughput ceiling is a published number, not folklore.

### HORIZON NEXT — "stateless spine + first value" (2 waves, ~12 lanes)

The scale architecture change and the demo-value unlock, which have no shared files and can run concurrently.

| # | Mission | What | Size | Lanes | Trace |
|---|---|---|---|---|---|
| C7 | **Stateless orchestrator** | Run lifecycle state → durable storage (rehydration SQL exists, `service.py:88-92`; migration 0013's plan version column enables approval-as-CAS). Workers become stateless claimers; N replicas safe. Evict terminal runs from RAM. | [XL] | 2 | pbc1 F1 |
| C8 | **Slim the bus** | NOTIFY carries minimal signal (seq, project_id, type); listeners fetch bodies REST/table-side; enforce payload <8KB at publish (DONE_SIGNAL diffs ship references, `DONE_DIFF_TEXT_MAX=200_000` stops riding the wire); dedupe path simplifies. Pre-req for identity scale-out. | [M] | 1 | pbc1 F3+F9 |
| C9 | **Partitioned retention** | Monthly range partitions on agent_events ts; attach/detach replaces DELETE churn; extend retention runner to notification_outbox + audit_log (no pruning path exists for either). Must include fresh-install migrate() probe (suites don't exercise migrate() — the OPS-15 lesson). | [M/L] | 1 | pbc1 F4+F8 |
| C10 | **Canned demo** | `POST /demo/seed` materializes recorded-event replay: project + repo + one COMPLETED run with real diff, real review, real PR — zero provider key, two clicks. guided_demo.py stays for live mode. | [S] | 1 | odelta L1 HIGH · roadmap N8 · mac1 F-4 residue |
| C11 | **Client SDK v0** | Generate Python client from the 7 backends' OpenAPI (all verified reachable, 19/14/8/13/5/10/7 paths); extract ws-ticket/replay/dedup logic from section7 lib into the generator's runtime; publish internally, document in TUTORIAL.md. TS client second. | [M] | 1 | mac1 F-3 |
| C12 | **Observability minimum** | configure_logging + RequestContextMiddleware on ALL services (currently orchestrator-only); one scrape target (Prometheus) + 5 alert rules (queue depth, event-bus lag, DB pool saturation, backup freshness, archiver failed_count); container mem/cpu/pids limits + log rotation caps. | [M] | 2 | sgamma OPS-21/22/03/04 |
| C13 | **Docs truth pass** | Fix AGENTS.md/README `harness_fresh` command; document the three real extension seams (engine backend_runner injection, reviewer thresholds settings→reviewer.py mapping gap, project rules loader — the best secret in the repo); glossary day-one nouns (project, sandbox, dry/live, KeyVault). | [S] | 1 | mac1 F-6/F-7/F-9 |

**NEXT exit criteria:** 2+ orchestrator replicas pass the C4 probe with zero duplicate execution; reconnect storm of 200 sockets doesn't saturate the loop; demo converts a stranger in <8 min without credentials; a scripter authenticates + streams events from the published SDK in <10 min.

### HORIZON LATER — "the context pillar + commercial skeleton" (2–3 waves, ~15 lanes)

The two CRITICAL killer gaps from O-DELTA, which are also the two biggest multipliers (§4).

| # | Mission | What | Size | Lanes | Trace |
|---|---|---|---|---|---|
| C14 | **Codebase indexing / @refs / RAG** | Per-project index job: tree-sitter symbol extraction → embeddings → pgvector in existing Postgres; `@file`/`@symbol` composer in meeting/chat; top-k chunks injected at plan-context; eval harness pinning answer quality on a fixture repo. Largest single build in the program. | [XL] | 3 | odelta killer#1 (=K2) · roadmap L1 |
| C15 | **Commercial skeleton** | LICENSE + ToS + pricing page (literal missing artifacts); Stripe adapter + checkout + webhook reconcile + dunning; enforcement middleware: free-tier run caps, seat counts tied to TeamMember rows, feature gates mapped to tier boundaries (part3 §4 sketch); guarded `PUT /billing/plan`. | [L] | 2 | odelta killer#2 (=K9 remainder, L4) · part3 §4 |
| C16 | **Editor surface v1** | Read-only tree → inline hunk accept/reject (X5 spec), git-snapshot checkpoints tagged with agent_events seq (restore-any-point UI), PR flow completion (approved run → branch → PR with trail summary). | [L] | 2 | odelta L2 (=K3/K5/K6/K7) · roadmap N3/X1/X5 |
| C17 | **MCP client + tool registry** | MCP client surfacing tools through the existing sandbox allow-list/forbidden_paths machinery ("tools run kernel-confined" becomes the differentiator pitch); tool-call event types via lawful 4-way contract bump; per-team tool allow-lists in settings. | [L] | 2 | odelta killer#3a (L3) |
| C18 | **SSO via OIDC** | OIDC first (SAML later), slots into get_principal given D6's fail-closed dependency architecture; SCIM deprovisioning w/ session revocation second. Enterprise procurement checkbox #2 after MCP. | [L] | 1 | odelta killer#3b (=F5 roadmap) |
| C19 | **Rules files shipped loud** | Rules loader exists (rules_loader.py, M12-G10) — promote to first-class UX: precedence chain team>repo>run, loaded at plan-generation, editor UI, documented. Cheap; high retention leverage. | [S/M] | 1 | odelta L2 (=K8) · mac1 F-6 gem |

**LATER exit criteria:** plans cite real symbols from the actual repo; a buyer can pay money lawfully and hit an enforced free cap; edits are curatable hunk-by-hunk with undo; external tools execute inside the same confinement story; enterprise SSO pilot passes.

### HORIZON BET — "category moves" (quarter+, decision-gated)

| # | Mission | What | Size | Trigger/gate | Trace |
|---|---|---|---|---|---|
| C20 | **Tab completion** | Daily-habit hook; needs own serving path + latency budget. Only meaningful AFTER C14 grounding + C16 editor. | [XL] | C14+C16 shipped; early-customer pull | roadmap F2 (=K4) |
| C21 | **Marketplace/plugins** | Third-party surface (CI notifiers, custom sandbox policies). Prereq: stable public contracts + feature-flag service + C17 tool registry. | [XL] | C17 shipped; 3+ design-partner asks | roadmap F3 |
| C22 | **Managed cloud** | Cloud trial funnel beside self-host; self-host packaging (Helm one-liner) ships first as bridge. Requires C15 billing real + C12 observability + PITR proven on the LIVE cluster (drill against real schema per OPS-12). | [XL] | C15 closed; DR drill green on real cluster | roadmap F4 · sgamma OPS-12 |
| C23 | **Meeting-media bet** | Voice/video presence stack OR async-first verdict with transcript integration. Written decision, not code. Already overdue since roadmap X7. | [S decision then L×several] | early-customer signal | roadmap X7/F1 (=K1) |

---

## 4. THE MULTIPLIER MAP — what compounds

```
                    ┌──────────────────────────────────────────────┐
                    │ C14 CODEBASE INDEXING  [the master key]      │
                    └──────┬───────────────┬──────────────┬────────┘
                           │               │              │
              agent quality│        editor │surface   tab-completion
              (plans cite real code)   (C16 nav)      (C20 viable)
                           │               │              │
              review depth (risk scoring reads real diffs)
                           │
              @ai answers w/ citations → trial conversion → pricing power (C15)

  C7 STATELESS ORCHESTRATOR ──► HA for every tenant ──► C22 cloud credible
        │                                  │
        └──► scale headroom 10x ───────────┴──► C4 probe numbers become sales data

  C11 CLIENT SDK ──► C17 MCP/tool registry ──► C21 marketplace ──► ecosystem flywheel
        │                    │
        └─ CI integrations   └─ "tools run confined" = security moat exported

  C3 COST INDEX/ROLLUP ──► C2 one-budget-truth ──► C15 tier enforcement (caps need
        fast gates)                                        cheap checks to enforce)

  C10 CANNED DEMO ──► C4 funnel metrics ──► every later horizon gets a conversion ruler
  C12 OBSERVABILITY ──► proves every other claim here at 3am without a user reporting it
```

Ranked by leverage-per-effort: **C14** (unlocks 3 axes at once — the single biggest quality ceiling; every plan currently derives from pasted text), **C7** (turns "~0 horizontal scale" into "N replicas"; everything cloud-shaped sits downstream), **C11→C17** (SDK is 2 weeks; MCP rides the sandbox moat; together they convert "governed terminal" into "platform"), **C10** ([S] effort, removes the #1 trial killer), **C3+C2** ([S]+[M], remove the tax that makes tier enforcement impossible later).

---

## 5. WHAT NOT TO BUILD — explicit non-goals

1. **Per-step silent model downgrade.** Engine single-model pinning is law (AGENTS.md no-forced-downgrade; C-004 lineage). Model *selection* may arrive only as explicit opt-in per plan step (C15/C19 territory) — never as a fallback ladder. *(odelta "what NOT to fix")*
2. **Auto-merge, ever.** Manual merge + plan-gate friction IS the product. No horizon item may route approved work to GitHub merge without a human. *(charter house rule; odelta L-verdict)*
3. **Voice/video media stack by default (C23 build phase).** Six incumbents own it; our wedge is governance. Build only on written X7-style verdict with customer signal; buy transcripts otherwise. *(part3 K1 framing)*
4. **Per-keystroke analysis / IDE fork.** No Cursor-clone editor investment beyond C16's review-surface scope; the differentiated surface is the governed run loop, not competing with a decade of IDE work. *(AGENTS.md law; part3 two-sided verdict)*
5. **A second database technology.** Postgres carries events, claims, vectors (pgvector), buckets, tickets. No Redis/Kafka *requirement* — C8's broker option is opt-in only if minimal-notify profiling shows the table-fetch path insufficient. *(pbc1 F3 fix note; F11 says pooling/partitioning first)*
6. **Fabricated telemetry or demo states.** Honest-empty-state discipline (fabricated IDs purged, 503s, flag-off upsells) survives commercial pressure intact. C10 replays RECORDED events — clearly labeled — rather than faking live runs. *(odelta; STATUS M10/W1 lineage)*
7. **Micro-frontends / section splits.** Seven services is the right grain; the s1→s8 reverse edge taught us coupling hides in package names — further splitting adds ceremony without isolation. *(pbeta C-1 lesson)*
8. **SAML-first SSO.** OIDC covers the procurement checkbox at a fraction of the surface; SAML waits for a named enterprise deal. *(odelta L5)*

---

## 6. RISKS & OPEN QUESTIONS

**Risks.**
- **C7 is the program's critical path and its biggest blast radius** (touches service.py state machine, approvals, queue, rehydration). Mitigation: C4 probe FIRST gives the pre/post ruler; feature-flag dual-write (dict + durable) one wave before cutover; the M10 lesson applies — sole-owner law per file.
- **Contract freeze friction.** C14 (@mention composer), C16 (checkpoint events), C17 (tool-call types) each need lawful EventType bumps (4-way version + TS mirror + consumer mocks, same commit). Three bumps in one horizon is the most churn since contracts 0.2→0.3 — schedule sequentially, batch unrelated additive types.
- **pgvector at scale is unproven here** — index build time and embedding cost per large repo need a spike BEFORE committing C14's full lane count (add one spike lane to the LATER wave's first week).
- **Estimate decay:** PBC1's breaking points were derived [E], not load-tested (audit charter was read-only). If C4 measures materially different ceilings, §2 targets re-baseline — that's the system working, not a miss.
- **Team bandwidth vs. wave cadence:** NOW+NEXT ≈ 18 lanes over 3 waves at historical pace; slipping C10/C11 past NEXT forfeits their multiplier effect on everything in LATER.
- **Security tail risk:** MAC2's F5/F7 (WS fingerprint bypass, URL-token fallback) are open while WS is the product's live surface; C5 should not slip past NOW.
- **Provider dependence:** every throughput/cost number assumes DeepSeek-class pricing (~$0.03–0.25/run); frontier-model catalogs price identical runs $0.5–5 (pbc1 F10) — C15's tier economics must be priced against the catalog, not one vendor.

**Open questions (need commander/user input, not guesses).**
1. License choice (blocks C15's literal first artifact): MIT like task0? AGPL for moat? Dual-track with commercial terms per part3 §4 sketch?
2. Target buyer for LATER sequencing: self-host sovereignty shops (argues C17/C18 before C16 polish) vs. team productivity (argues C16/C19 first)?
3. Is there appetite for a managed-cloud P&L at all (C22), or does self-host-only positioning hold? Determines whether C7's HA work is existential or excellence.
4. Meeting side: does anyone actually want the media bet answered (C23), or do we formally retire K1 and reposition as async-first governance?
5. Should `deepseek-harness-sdk` naming collision (external upstream vs. our future PyPI client) get a formal rename decision before C11 publishes?

---
*— O-DELTA × PBC1, ELITE-FORGE · Mission 17 Phase C synthesis @ c5c70ca · pointer-only delivery per U8/D3*
