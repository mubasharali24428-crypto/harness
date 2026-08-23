# Sweep H — Performance & Scale Findings (attempt 4)

Mode: pure write, zero exploration; all leads pre-verified upstream. Repo untouched.
Totals: L1 19 · L2 14 · L3 12 · L4 13 · L5 17 · L6 11 → **86 findings**.
Line format: `[file:component] issue → where it hurts → fix`.

## Lens 1 — Database Hotspots (19)

- H1.01 `[db/agent_events:notify-per-row]` every inserted event row fires its own pg_notify → notify volume scales O(events) regardless of listener interest → coalesce per transaction into a single NOTIFY carrying a batch id.
- H1.02 `[db/agent_events:fan-out]` each NOTIFY broadcasts to every LISTENing subscriber connection → combined cost O(events × subscribers), worst at exactly peak activity → replace broadcast with outbox table + dedicated consumer doing targeted delivery.
- H1.03 `[db/agent_events:payload-size]` event JSON serialized into NOTIFY payloads → oversized payloads near PG's 8000-byte NOTIFY limit can abort inserts mid-burst → send only event ids in payloads; deliver bodies via fetch.
- H1.04 `[db/agent_events:trigger-overhead]` per-row trigger executes on every insert during long runs → insert throughput ceiling drops under burst writes → drop trigger; use logical decoding/outbox worker.
- H1.05 `[db/meeting_messages:missing-index]` no index on meeting_id → list-messages-by-meeting is a sequential scan → `CREATE INDEX ON meeting_messages (meeting_id, created_at DESC)`.
- H1.06 `[db/meeting_messages:unbounded-list]` list endpoint returns all messages for a meeting with no LIMIT/keyset → long meetings produce unbounded result sets → keyset pagination on (created_at, id).
- H1.07 `[db/meeting_messages:io-amplification]` full message bodies fetched to render list previews → I/O + serialization cost ∝ body sizes, not viewport → project preview columns; lazy-load bodies on open.
- H1.08 `[db/meeting_messages:count-cost]` unpaginated lists force materializing every row to know totals → O(n) rows moved to count a number → index-only count or cursor UI without totals.
- H1.09 `[db/rate_buckets:hot-path-upsert]` every API request upserts rate_buckets → a durable write + row lock sits on the hottest path → in-memory or Redis token bucket; persist aggregates only.
- H1.10 `[db/rate_buckets:row-contention]` concurrent requests on the same bucket key serialize on the row lock → tail latency spikes on busy team/project keys → atomic INCR-with-expiry or sharded counters.
- H1.11 `[db/rate_buckets:fail-open-at-peak]` when the DB saturates, limiter errors resolve to "allow" → throttling disappears precisely under load, the moment it matters → fail closed for expensive ops; fall back to a local approximate counter otherwise.
- H1.12 `[db/rate_buckets:write-amplification]` one UPDATE per request feeds WAL + autovacuum churn on a permanently hot table → maintenance competes with the real workload → remove steady-state writes via cache-layer counters.
- H1.13 `[api/runs:derived-min-ts]` runs listing computes MIN(timestamp) over agent_events per run row → page latency grows with both run count and per-run event volume → denormalize first_event_at onto runs; maintain at insert.
- H1.14 `[api/runs:n+1-aggregates]` correlated MIN()/MAX() subqueries repeat per returned row (N+1 shape) → listing 50 runs fires dozens of scans over agent_events → pre-aggregated CTE / LATERAL join against rollup.
- H1.15 `[db/agent_events:no-partitioning]` agent_events grows monotonically with no partition/retention story → every scan that touches it (MIN(), replay, notify consumers) degrades monthly → range-partition by time; DROP old partitions.
- H1.16 `[db/audit_log:hot-path-inserts]` audit_log INSERTs run synchronously inside hot request paths → every op pays an extra durable commit of latency → async writer/outbox with batched commits.
- H1.17 `[db/audit_log:no-read-consumers]` zero readers exist today → pure write tax and permanent growth for unused data → gate behind a flag or sink to cheap append-only storage until a consumer exists.
- H1.18 `[db/audit_log:bloat-pressure]` unpruned audit_log drives autovacuum lag and index bloat → maintenance windows degrade neighboring hot tables → time-based partitions dropped wholesale.
- H1.19 `[db/general:dual-delivery-machinery]` NOTIFY fan-out and worker polling coexist across services → two consistency points to reason about, double the failure modes → standardize on one transport (outbox + stream).

## Lens 2 — Memory & Leaks (14)

- H2.01 `[services/orchestrator:runs-map]` `OrchestratorService.runs` dict never evicts terminal (DONE/FAILED) runs → heap grows ∝ cumulative runs for process lifetime → evict on completion after a short TTL grace.
- H2.02 `[services/orchestrator:restart-only-reclaim]` the only thing that clears the runs map is a process restart → leak is masked by redeploys, never fixed → expose `runs_map_size` gauge + alert on trend.
- H2.03 `[services/orchestrator:fat-run-state]` DONE entries retain full state objects (transcripts, artifact refs) in RAM → thousands of runs ⇒ multi-GB resident set → keep summaries in memory; offload bodies to DB/object store.
- H2.04 `[services/orchestrator:task-refs]` spawned asyncio Tasks are kept in collections without done-callback removal → finished Task objects (and their results) are retained forever → discard in a done callback; weakrefs where suitable.
- H2.05 `[services/orchestrator:swallowed-exceptions]` retained completed tasks suppress "exception was never retrieved" warnings → background failures vanish silently → consume/log exceptions in the done callback + failure metric.
- H2.06 `[dsh:sessions-root]` `DSH_SESSION_ROOT=/tmp` accumulates per-session state with no sweeper → /tmp fills on long-lived hosts → TTL sweeper (timer/tmpreaper) plus cleanup on normal session close.
- H2.07 `[dsh:orphaned-sessions]` an orchestrator crash leaves session state orphaned in the root → stale sessions are never reclaimed and can confuse reattachment → stamp pid/epoch; reap mismatches at startup.
- H2.08 `[workspace:/tmp-growth]` per-run scratch (logs, diffs, artifacts) written to /tmp unbounded → disk exhaustion takes down the whole single node → per-run quota + post-run GC pass.
- H2.09 `[workspace:large-artifacts]` arbitrarily large generated files land in tmp regardless of size → a few huge runs can fill the disk → hard size caps with overflow to object storage.
- H2.10 `[ws:slow-consumer-buffers]` outbound frames buffer per connection while a stalled client drains slowly → memory ∝ backlog of the slowest consumer → bounded send queues; disconnect on overflow.
- H2.11 `[ws:subscriber-leak]` subscriber registry appends on connect, removes only on clean disconnect → dead sockets linger after network drops → heartbeat + timeout reaping.
- H2.12 `[runtime:event-loop-cpu]` CPU-bound work (hashing, giant JSON encode) on the event loop inflates loop lag → p99 of *all* endpoints degrades together → offload to thread/process executors.
- H2.13 `[runtime:debug-rings]` in-memory log/event ring buffers used for debugging grow with traffic → secondary unbounded-growth vector during bursts → cap ring length; sample high-frequency entries.
- H2.14 `[observability:no-memory-metrics]` no per-service RSS/heap gauges surfaced → leaks discovered only as OOM kills in production → export process metrics; alert on sustained growth.

## Lens 3 — Scaling Ceilings (12)

- H3.01 `[deploy:compose-single-node]` only a single-node docker-compose exists → hard ceiling = one box; no horizontal path documented → provide k8s/Nomad manifests with replica'd services.
- H3.02 `[deploy:shared-failure-domain]` all 7 services + Postgres colocated on one host → one reboot/OOM/kernel upgrade is a full-platform outage → split data plane from control plane at minimum.
- H3.03 `[db:postgres-spof]` a single Postgres instance with no replica/failover in the topology → DB outage = total outage; minor version bumps need downtime → streaming replica + automated failover (Patroni or managed).
- H3.04 `[db:no-read-routing]` heavy reads (MIN() scans, replay exports) share the primary with OLTP → analytical load starves the transactional path → route heavy reads to a replica.
- H3.05 `[ws:process-pinned-connections]` WebSocket connections and their subscription registry live in one process's memory → adding a second app instance silently splits delivery → external pub/sub (Redis) fan-out beneath the WS tier.
- H3.06 `[ws:no-reconnect-cursor]` no resubscribe/replay-cursor protocol exists for reconnects → horizontal scaling later forces a breaking client protocol change → design `since=` cursor reconnect now (pairs with H4.01).
- H3.07 `[pg:pool-math]` 7 services × default pool sizes overshoot `max_connections=100` → connection-refused storms under load; retries amplify the spike → right-size pools so the sum stays < ~80% of budget.
- H3.08 `[pg:no-pooler]` no pgbouncer/pgcat in front of Postgres → per-service pools multiply; migrations compete with runtime connections for slots → transaction-mode pooling layer.
- H3.09 `[pg:headroom]` max_connections=100 minus app demand leaves little room for superuser-reserved + admin tooling during incidents → operators can lock themselves out mid-firefight → cut app usage via pooler; don't raise the server limit.
- H3.10 `[state:process-local]` in-process maps (runs, subscribers) assume a single instance → enabling replicas breaks correctness quietly, not just performance → inventory all process-local state before any scale-out.
- H3.11 `[fs:local-workspace]` run workspaces assumed on local disk (/tmp) → multi-instance requires a shared volume or object store → abstract artifact storage behind an interface now.
- H3.12 `[queue:none]` no job queue/backpressure between API and orchestrator → submission bursts hit the single process directly → bounded queue with admission control and queue-depth metrics.

## Lens 4 — Request-Path Cost (13)

- H4.01 `[api/replay:unbounded-response]` replay returns a run's entire event history in one response → multi-MB payloads on long runs: server memory spike + client parse jank → cursor pagination (`since=`/`until=`) with NDJSON streaming.
- H4.02 `[api/replay:full-projection]` every field of every event ships, including large tool outputs → wire cost ∝ the single biggest event, not what the UI shows → field selection (`?fields=`) + blob truncation with URI refs.
- H4.03 `[api/replay:blocking-serialize]` the giant JSON body is built fully in memory before responding → one replay request occupies a worker and queues others behind it → stream chunks as produced.
- H4.04 `[brief/generate:full-transcript-scan]` each brief call rescans the entire transcript → cost/latency ∝ transcript length and repeated calls redo identical work → incremental summarization checkpointed at byte offsets.
- H4.05 `[brief/generate:no-cache]` identical brief requests (same run, unchanged tail) recompute end-to-end → wasted LLM spend and latency → content-hash-keyed cache invalidated on new events.
- H4.06 `[reviewer:arbitrary-hash]` reviewer hashes client-supplied `diffText` of arbitrary size synchronously on the request path → a huge diff means seconds of CPU inside one request → cap input size; stream/chunk the hash; background-job oversize inputs.
- H4.07 `[reviewer:event-loop-blocking]` hashing runs inline in the async handler instead of an executor → the event loop stalls and ALL concurrent requests freeze with it → `run_in_executor` offload.
- H4.08 `[view:three-round-trips]` a single view needs status, engine, and plan = 3 sequential requests → perceived latency is the sum of three RTTs plus tripled server think → aggregate endpoint (or batched GET) returning one envelope.
- H4.09 `[view:duplicate-resolution]` each of the 3 endpoints repeats auth, project/team lookup, and validation → 3× redundant DB/CPU per view (compounds Lens 6 caching gap) → shared request-context resolution.
- H4.10 `[fe/view:serial-waterfall]` frontend awaits the 3 endpoints sequentially rather than in parallel → avoidable latency even while the API shape stays → parallelize immediately; aggregate endpoint remains the endgame.
- H4.11 `[api:buffer-entire-response]` list/history-shaped endpoints construct complete responses before sending → latency = full build time; memory ∝ response size → streaming responses wherever shape allows.
- H4.12 `[api:no-pagination-contract]` no consistent pagination contract across list endpoints (replay and meeting_messages are symptoms) → each becomes its own incident as data grows → one shared keyset-pagination helper adopted everywhere.
- H4.13 `[llm:inline-in-request]` LLM-backed endpoints (brief/reviewer) run model calls inside the request lifecycle → minutes-long requests hold connections; timeouts trigger retry storms → async job + poll/SSE completion pattern.

## Lens 5 — Frontend (17)

- H5.01 `[fe/ticker:30s-always-on]` global 30s ticker refetches regardless of tab visibility or activity → overnight/idle churn keeps servers and browsers busy for nothing → pause on `document.hidden`; back off when idle.
- H5.02 `[fe/events:refetch-per-event]` each incoming event triggers its own refetch cycle → during active runs this becomes a fetch storm (N events → N round trips) → coalesce to a flush interval or animation-frame batch.
- H5.03 `[fe/render:unmemoized-tree]` event-driven updates rerender large trees without memo boundaries → main-thread churn and dropped frames on long lists → virtualize lists + memoized selectors.
- H5.04 `[fe/codemirror:wholesale-import]` CodeMirror imported eagerly in full → hundreds of KB in the main bundle for users who rarely open the editor → `import()` dynamically at editor mount.
- H5.05 `[fe/bundle:no-code-splitting]` route-level code splitting unverified → likely one fat chunk gating first paint of every page → per-route dynamic imports; verify output with a bundle analyzer.
- H5.06 `[fe/ci:no-size-budget]` no bundle-size budget check in CI → bundle regressions ship silently → size-limit check on PRs with explicit budgets.
- H5.07 `[fe/images:no-next-image]` raw `<img>` tags without srcset/explicit dimensions → oversized transfers, no lazy loading, layout instability → `next/image` (or width/height + `loading=lazy` minimum).
- H5.08 `[fe/lcp:appshell-fetch-block]` LCP waits on AppShell data fetches before meaningful paint → LCP = API latency + render; blows past 2.5s on cold starts → stream the shell with skeletons; defer noncritical data below the fold.
- H5.09 `[fe/nav:no-prefetch]` nothing warms route/API responses ahead of navigation intent → every navigation eats the full fetch latency → prefetch on hover/viewport intent.
- H5.10 `[fe/cls:late-pills]` status pills insert into the DOM only after async data lands → layout shifts as rows/columns resize around them → reserve pill slots with fixed-height skeletons until data arrives.
- H5.11 `[fe/cls:async-panel-heights]` panels whose height depends on fetched content push footer/content downward → cumulative CLS on slow links → min-height placeholders matching expected bounds.
- H5.12 `[fe/ws:reconnect-herd]` on reconnect the client refetches full state plus ticker → thundering herd after any server blip → resume from last-seen cursor via replay `since=` (needs H4.01).
- H5.13 `[fe/state:duplicate-requests]` multiple components independently refetch the same resources (status/engine/plan) → duplicate in-flight requests and inconsistent views → shared cache/dedupe layer (react-query/SWR-style).
- H5.14 `[fe/lists:no-virtualization]` meeting/run/message lists render every loaded item → DOM node count explodes on long histories, scrolling janks → windowed/virtualized rows.
- H5.15 `[fe/markdown:reparse-churn]` event streams re-parse and re-render markdown/diffs wholesale per update → CPU spikes exactly during active runs → memoize parsed blocks keyed by content hash.
- H5.16 `[fe/network:poll-plus-push]` 30s polling AND event push are both always active → duplicate delivery and doubled backend QPS for the same data → trust the socket; poll only as jittered fallback when disconnected.
- H5.17 `[fe/obs:no-rum]` no web-vitals/RUM collection shipped → the LCP/CLS problems above are invisible in production → report vitals into existing telemetry with route labels.

## Lens 6 — Ops Noise & Cost (11)

- H6.01 `[ops/healthcheck:cascade]` 9 containers × 5s intervals ≈ 1.7 QPS of synthetic traffic hitting services forever → constant access-log noise buries real error signals → raise intervals to ~30s; stop logging the health path.
- H6.02 `[ops/healthcheck:log-volume]` healthcheck noise inflates log storage/rotation churn → you pay to store 200-OKs indefinitely → exclude healthcheck routes from the log pipeline entirely.
- H6.03 `[ops/healthcheck:depth-tiering]` if any check probes DB/deps at 5s cadence → self-inflicted load participating in the very pool contention from H3.07 → cheap liveness frequent, deeper readiness rare.
- H6.04 `[cache:project-team-lookups]` project/team rows looked up per request with zero caching → identical queries hundreds of times/sec against the SPOF database → 30–60s in-process TTL cache, then shared cache.
- H6.05 `[cache:config-and-flags]` config/flag values re-read per request likewise uncached → needless DB round trips on every hot path → cached snapshot with watch/refresh.
- H6.06 `[notifications:fixed-poll]` notification worker polls on a fixed interval regardless of backlog → idle waste when empty, lag spikes when bursts arrive between ticks → drain-to-empty with capped backoff, or LISTEN/NOTIFY wake-ups.
- H6.07 `[notifications:one-commit-per-item]` per-item processing commits/sends individually → throughput ceiling is low; per-item WAL/send overhead dominates → batch-drain with a single commit per cycle.
- H6.08 `[notifications:no-backlog-visibility]` no exposed queue-depth/age metric → operators can't see lag forming until users complain → gauge depth + age-of-oldest with alert thresholds.
- H6.09 `[ops/logs:mixed-streams]` health/access noise shares the pipeline with structured application logs → raises ingestion cost of any log tooling and slows searches → separate streams by logger/route.
- H6.10 `[ops/static:no-caching]` static assets served from app origin without immutable cache headers/CDN → every user re-downloads assets after each deploy → content-hashed filenames + `Cache-Control: immutable` + edge cache.
- H6.11 `[ops/cost:uncapped-llm-spend]` brief/reviewer LLM calls are uncached with full-transcript inputs (see H4.04/H4.05) → dollar spend scales linearly with usage with no lever → cache + input trimming + a spend metric per endpoint.

## Top Severity Shortlist (P0)

1. H1.11 — rate limiter fails open at peak: protection vanishes when hammered.
2. H3.07/H3.08 — pool math exceeds max_connections: guaranteed connection storms at scale.
3. H2.01 — orchestrator runs-map leak: OOM-bound on any long-lived deployment.
4. H4.01 — unbounded replay responses: single request can take down a worker.
5. H3.05 — WS pinned to one process: blocks all horizontal scaling of the app tier.
6. H1.05/H1.06 — meeting_messages unindexed + unbounded: degenerates as meetings grow.

*End of sweep H attempt 4. No repository files were modified.*
