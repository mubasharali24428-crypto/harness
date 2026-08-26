# Release Notes — v1.0.0-candidate

> **Status:** RELEASE CANDIDATE. First versioned tag of the AI Engineering
> Harness. Contracts ship as **1.0.0rc1** (PEP 440 pre-release — it predates a
> future final 1.0.0 and sorts accordingly). Prepared by Mission 23 lane Z4
> (2026-08-26) against master @ `3c87665` + the release-prep commit; suites
> verified green at preparation time (exceptions attributed inline, §Known
> limitations). The commander applies the tag; exact command at the bottom.

---

## What ships in v1.0

The platform is a self-hosted, governed execution plane for AI coding agents:
human plan approval, sandboxed execution, event-sourced audit (`agent_events`
is the source of truth), manual merges only, team tenancy with budgets and
tier enforcement. v1.0 closes the 10x program (mission17-10x-plan.md): every
Horizon NOW/NEXT/LATER item is landed, and the first Bet decision is recorded
by its own lane.

### Capability summary — per the 10x-plan axes, with measured numbers

**Scale & throughput (the headline).**
The B1 ruler (docs/benchmarks/BASELINE.md, commit under test `1ec49e7`,
2026-08-25, isolated loadtest stack, dry echo engine) measured the platform
before the fix: control-plane latency flat from 8→64 VU (p50 ≈ 18–21 ms, p95
34→98 ms, zero failures), journey throughput saturated at ~1.9/s and decaying
only mildly (1.72/s at 64 VU) — with the wall named precisely: the worker
claimed **one run per 0.5 s poll cycle**, hard-bounding admission at ~2 runs/s
regardless of executor width. Mission 20-T1 tore that wall down: **claim
batching moved 32-VU throughput from 1.69 to 37.58 journeys/s (~22×), zero
failures across 4,521 journeys** (CI 32922623925). Theoretical ceiling is now
~3.2M runs/day; executor capacity and provider latency become the real
limits. Supporting capacity facts from B1: ~880k `agent_events` rows/day and
~149k runs/day equivalent at the old ceiling, metering honest at
$0.0000510/run average with zero row loss.

**Stateless spine.** Orchestrator approvals are CAS-on-durable-state; a
two-replica exactly-once proof landed (M19-N1). The NOTIFY bus carries ~70-byte
signals with an 8 KB publish cap (0029 slim bus); `agent_events` retention is
partition-based (0028) with outbox/audit pruning; request-id middleware,
Prometheus scrape target and 5 promtool-validated alerts cover all services;
every compose service has resource limits, log rotation, and `/ready` distinct
from `/health`.

**Time-to-first-value.** Canned demo (M19-N4): clone → first completed agent
run replayed from recorded events in minutes, zero provider key. The guided
dry/live modes remain for real engines.

**Ecosystem surface.** Python + TypeScript client SDKs generated from one
pipeline over all sections' OpenAPI specs (Python: 91 paths / 104 ops);
`harness-mcp` exposes start_run / get_run_status / search_context /
list_projects over stdio and streamable HTTP; three documented extension
seams (engine backend_runner injection, reviewer thresholds, project rules
loader) in docs/extensions.md. Both SDK user-agents and packages carry the
release version (1.0.0rc1).

**Context intelligence (M21).** Per-project codebase indexing into pgvector
(pluggable embeddings, incremental re-index by chunk hash), `@file`/`@symbol`
reference resolution with hybrid ranking and token-budgeted bundles, and a
retrieval-augmented planner whose plans cite path:line chunks — the exit
criterion "plans demonstrably cite indexed chunks" is met. UI surface:
@autocomplete, chunk preview panel, searchable tree.

**Governance core (carried, tested).** Fail-closed authz, one budget truth
(`contracts/budget.py`, advisory-lock reserve, fail-CLOSED with explicit
degrade flag), O(1) cost-gate indexes + daily rollup, invite/ticket hygiene
(email-bound invites, fingerprint-enforced WS tickets, bucket GC), passwordless
login behind OTP ownership proof, quote-aware shell-injection rejection and
egress pinning in the sandbox, WAL-G PITR backup stack with a drilled restore,
and a deploy-time schema-drift gate that refuses boot on schema/image mismatch.

**Commercial skeleton (M22).** Behavior-bearing plan tiers enforced at
run-start and index API (free tier defaults: 20 runs/day/team, 2,000 context
chunks/project, 1 human seat — code truth in `contracts/tiers.py`, tunable via
`HARNESS_TIER_*` env and per-team override columns); Stripe checkout +
signature-verified, idempotent webhooks behind `HARNESS_BILLING=stripe`;
OIDC SSO (discovery + PKCE + JWKS, per-team IdP config, encrypted secrets);
LICENSE (MIT) plus TERMS/PRIVACY skeletons; MCP server as the LLM-facing
surface. Dollar prices deliberately absent pending counsel review.

---

## Known limitations (honest list)

1. **Single-host topology.** Compose targets one host; the two-replica
   orchestrator result is an exactly-once correctness proof, not a shipped HA
   story. PgBouncer/broker-scale work stays on the roadmap.
2. **Throughput numbers are dry-engine numbers.** They measure the platform
   (API + queue + claims + DB + bus + metering), not provider latency or
   breaker behavior. Closed-loop generator; single-replica; ±0.5 s poll
   quantization on engine-wait percentiles — see BASELINE "Honest limits".
3. **pgvector is optional by design.** Without a pgvector-bearing Postgres
   image, migration 0030 still lands but creates nothing, and
   indexing/search degrade honestly (empty results, "unavailable" posture).
   Point compose's db service at `pgvector/pgvector:pg18` to enable.
4. **Stripe integration is CI-test-mode only** (mocked provider, no live keys
   exercised). Pricing amounts are placeholders pending counsel; do not charge
   money on this candidate without legal review of TERMS/PRIVACY.
5. **No schema down-migrations exist** (forward-only ledger law). Schema
   recovery always means PITR restore (docs/runbooks/backup-and-pitr.md). The
   combined image+schema rollback has been rehearsed only as parts (drift-gate
   trip tests + the 2026-08-24 PITR drill), not as one drilled sequence.
6. **SSO scope.** OIDC only (no SAML/SCIM); first-login email-match
   provisioning rule is documented and audited — read it before enabling SSO.
   Editor surface (inline hunk accept/reject) and MCP *client* + tool registry
   are post-1.0 items.
7. **Snapshot-time suite exception (attributed, not mine).** During this
   lane's verification, section11 showed 2 red tests in
   `tests/test_chunking.py` — both pin the legacy fixed-window chunker and
   fail against the AST-aware chunking bet that lane Z2 landed in
   `chunking.py` mid-flight (their owned path, uncommitted at check time).
   Attribution per repo protocol; Z2 owns reconciling those pins before
   finale close. Every other suite was green at HEAD (see Verification).
8. **Engine dependency pin.** Upstream `deepseek-harness-sdk` stays pinned at
   0.1.1rc1 (no 0.2.0 exists on PyPI); re-run `task0/hello.py` on any bump.

---

## Upgrade & migration notes (0030–0033)

All four are additive tables/columns — no frozen table or column is altered,
and there are no data backfills. Fresh installs receive them via the normal
`migrate` one-shot; existing databases treat them as pending (the drift gate
passes pending chains and the one-shot applies each inside its own
transaction).

| Migration | What it adds | Operator action |
|---|---|---|
| `0030_pgvector.sql` | `context_chunks` + HNSW index, gated on `pg_available_extensions('vector')` | Optional. Use a pgvector-bearing db image (e.g. `pgvector/pgvector:pg18`) to get the feature; on stock `postgres:18-alpine` the migration lands a ledger row and skips DDL (NOTICE in logs). Embedding dim is `vector(256)` — widening means a NEW migration + re-embed, never ALTER in place. |
| `0031_tier_cap_overrides.sql` | `billing_plans.runs_per_day_override`, `.context_index_max_chunks_override` | Nothing. NULL = inherit tier defaults. Set per-team via the billing_plans row; global knobs: `HARNESS_TIER_RUNS_PER_DAY` / `HARNESS_TIER_CONTEXT_CHUNKS`. |
| `0032_stripe_webhook_events.sql` | Webhook idempotency ledger (PK = provider event id ⇒ exactly-once replays) | Only if enabling `HARNESS_BILLING=stripe`; harmless otherwise. |
| `0033_team_sso.sql` | `team_sso_configs` (Fernet-encrypted client secret via KeyVault) + `sso_login_states` (PKCE hashes, UTC ISO-8601 TEXT, atomic DELETE…RETURNING redemption) | Configure per team when enabling SSO; secrets are stored encrypted, never logged. |

Client/consumer upgrades shipping with this tag:

- **Contracts 0.4.2 → 1.0.0rc1**: all four version stamps moved atomically;
  `test_version_pin_conformance` now accepts PEP 440 pre-release suffixes in
  every leg. Consumers with floor pins (`>=0.2.0`) remain satisfiable.
- After pulling, refresh editable installs so distribution metadata matches:
  `./.venv/bin/pip install -q -e ../contracts` from any section dir.
- **SDKs 0.1.0 → 1.0.0rc1** (`harness-sdk`, `harness-ts-sdk`): version-string
  bumps only (user agents, package metadata, MCP server-version handshake);
  no API shape changes. Re-run your smoke scripts as usual.
- Section packages (`harness-context`, `harness-mcp`) align at 1.0.0rc1;
  FastAPI app-title version strings updated to match.

---

## Deployment checklist (verified against docs/runbooks/rollback.md)

Every step below was executed as written against the live local stack during
release preparation (health probes, migrate probe, compose config per
profile, entrypoint parse) except step 2's build, which is the operator's
deploy act. One defect found and fixed in the runbook during verification:
§5's migrate probe previously omitted `--env-file .env` and died at the
master-key guard before the drift gate could speak — corrected in
rollback.md.

```sh
# 0. Preflight: Docker + compose v2 on the host; repo at the release commit.
git -C /path/to/harness rev-parse --short HEAD     # record it next to the tag name

# 1. Configure deploy/.env (never committed):
cd /path/to/harness/deploy
#   HARNESS_MASTER_KEY=<32+ byte secret>            # required; or
#                                                   # HARNESS_ALLOW_DEV_MASTER_KEY=1 (local dev ONLY)
#   HARNESS_CORS_ORIGINS=["https://your.host"]      # JSON ARRAY — a plain string crashes every service
#   HARNESS_REQUIRE_OWNERSHIP=1                     # recommended for anything non-local
#   HARNESS_BILLING=stripe                          # optional (M3); needs signing secret
# HARNESS_IMAGE_TAG is written per-deploy in step 2.

# 2. Build + boot under an immutable image tag (rollback runbook §1 hygiene):
TAG="$(date -u +%Y%m%d-%H%M)"
grep -q "^HARNESS_IMAGE_TAG=" .env \
  && sed -i '' "s/^HARNESS_IMAGE_TAG=.*/HARNESS_IMAGE_TAG=$TAG/" .env \
  || echo "HARNESS_IMAGE_TAG=$TAG" >> .env          # Linux: sed -i WITHOUT ''
docker compose -f docker-compose.yml --env-file .env up -d --build
docker images 'harness-*' --format '{{.Repository}}:{{.Tag}}  {{.ID}}  {{.CreatedSince}}'
echo "$(date -u +%FT%TZ) deployed v$TAG (prev: <fill-in>)" >> DEPLOYLOG.txt

# 3. Schema: one-shot migrate; expect "drift gate: OK" as its verdict.
docker compose -f docker-compose.yml --env-file .env run --rm migrate
#   fresh volume  -> full 0001→0033 chain applies, exits 0
#   existing DB   -> pending 0030–0033 applied; REFUSED BOOT means stop and
#                    read rollback.md §3 before retrying anything

# 4. Health edges (all seven backends answer {"ok":true}; frontend answers):
for p in 8001 8002 8003 8004 8005 8006 8008; do curl -fsS localhost:$p/health; echo; done
curl -fsSI localhost:3000 | head -1

# 5. Ledger sanity (fresh volume expect 0033):
docker exec harness-db-1 psql -U harness -d harness -tc \
  'select max(version) from schema_migrations;'

# 6. Events flowing (seq advancing, no replay storms):
docker logs harness-orchestrator-1 --since 2m 2>&1 | tail -20

# Optional profiles (--profile <name>): walg (PITR backups), proxy (Caddy TLS),
# observability (Prometheus), events-retention, cost-rollup, maintenance,
# loadtest (isolated benchmark stack). Leave legacy-backend OFF unless you
# specifically need the deprecated all-in-one container.
```

Rollback quick reference (full decision tree: rollback.md):

- Bad app code, schema untouched → image-only: point `HARNESS_IMAGE_TAG` at
  the previous tag, `docker compose -f docker-compose.yml --env-file .env up
  -d --no-build`, re-verify §4–§6 above.
- Schema involved → freeze the scene, PITR to before the deploy timestamp,
  then image-only onto the previous tag. Never hand-edit
  `schema_migrations`.
- Verify any rollback with the drift-gate probe from step 3 (must print
  "drift gate: OK") — now with `--env-file .env` and the key env present.

---

## Tag prep (commander applies — this lane does NOT tag)

Apply the annotated tag to the commit that CONTAINS this release-prep change
(the version bump + this file + the rollback.md fix), never to an ancestor:

```sh
cd /path/to/harness
git switch master && git pull --ff-only        # or the local equivalent

# Pre-flight gate — must be green BEFORE tagging:
section1/.venv/bin/python -m pytest section1/tests/test_version_pin_conformance.py -q

git tag -a v1.0.0-candidate -m "v1.0.0 candidate — AI Engineering Harness

First release candidate of the governed agent-execution platform.
Contracts 1.0.0rc1 (PEP 440 pre-release); all four version stamps aligned;
suites verified at tag-prep time (see RELEASE_NOTES_v1.md for measured
capabilities, known limitations, and the 0030-0033 upgrade path)."
git push origin v1.0.0-candidate               # when a remote exists
```

Notes for the commander:

- Deploy image tags stay date-based (`HARNESS_IMAGE_TAG=YYYYMMDD-HHMM`); the
  git tag identifies the source snapshot, not the container tag.
- If Z2's chunking-pin reconciliation lands after tagging, that is normal
  rc flow: fixes ride forward toward a `v1.0.0` final (or `rc2` if the delta
  is material), per the one-release acceptance window in docs/api-versioning.md.
