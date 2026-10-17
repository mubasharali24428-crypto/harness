# A3 final report — 30 feature dispositions, 10 risk dispositions

Worktree `harness-a3` (`codex/harness-a3`, HEAD `31bf407`). **Nothing committed or pushed.** Edits to existing files: `section5/src/harness_sandbox/{api,docker_runner,local_runner,service}.py` only. Machine-readable ledger: `final-ledger.json`; per-session ledgers/reports in `session-1..5/`.

## Release verdict
**Not ready for production rollout.** 7 features are locally verified, 18 partial, 5 need a peer lane. Keep all A3 capabilities disabled until combined acceptance.

## Features (30)
| State | Rows |
|---|---|
| VERIFIED IMPLEMENTED (7) | F055 browser session persistence · F057 diagnostics · F047 visual regression · F104 snapshots/restore · F044 failure bundles · F048 contract testing (preserved + drift/golden extension) · F049 mutation testing |
| NEEDS INTEGRATION (5) | F051 browser control · F056 browser approval · F046 agent-invokable E2E · F108 worker pool (needs Postgres store/S1 migration) · F043 repair loop (needs A2 fixer) |
| PARTIAL (18) | F052, F053, F054, F058, F059, F060, F101, F102, F103, F105, F106, F107, F109, F110, F045, F041, F042, F050 — sub-capability detail in each session's `ledger.json` |

Preserved as instructed: F022/F024/F048/F080 untouched; F084 no-forced-fallback honoured (backend selection raises instead of downgrading).

## Risks (10) — instrument, signal, residual, owner, next decision
| VR | State | Control / validation instrument built | Monitoring signal | Residual exposure | Next decision |
|---|---|---|---|---|---|
| 007 resume/rollback | PARTIAL CONTROL | `EnvironmentSnapshots` (dirty refusal, auto safety snapshot, partial restore); fenced worker leases; bounded repair loop | `WORKSPACE_DIRTY` 409 rate; `LEASE_LOST` count; reaper requeues | SDK thread kill not demonstrated (A2); files only, no process/DB state | A2 wires kill + restore into run lifecycle |
| 013 sandbox escape / network | PARTIAL CONTROL | subresource egress guard; preview registry; brokered fetch (DNS-rebinding/redirect); quota guard; Docker argv hardening; capability tokens bound to run | `blockedByPolicy` network entries; `ResourceQuotaExceededError`; consent `denied` audit lines | Docker never run live; polling guard can overshoot; live SDK→S5 link absent | Run Docker/Linux confinement suite on a Linux host; A2/A5 wiring |
| 026 storage growth | PARTIAL CONTROL | snapshot retention + team byte quota; bundle count/bytes quota; cache invalidation | bytes per team in snapshot/bundle stores | no environment-cache eviction/TTL; artifact dirs unbounded | Operator policy for cache TTL and artifact retention |
| 027 restore proof | PARTIAL CONTROL | file DR drill with RTO + corruption/traversal detection; disposable PG18 dump/restore drill (0.132 s, checksum match) | drill report `ok`, `restoreSeconds` | no object-store drill; no PITR on real cluster | Schedule staging drill with real credentials (external) |
| 028 traces | OPEN | sanitized console/network/HAR for browser actions only | — | no distributed tracing; not an A3 deliverable | A5 telemetry lane |
| 030 vulnerable deps | PARTIAL CONTROL | wheel-only, hash-pinned, consented installs; hash-verified offline mirror | mirror `verify()` mismatches | no scanner run by A3; new deps added (playwright, pngjs, pixelmatch, vitest) unscanned | A5 runs dependency scanning on the new manifests |
| 035 provider API churn | OPEN | none (no compatibility matrix produced) | — | unvalidated | Product decision: matrix owner and cadence |
| 039 self-host difficulty | PARTIAL CONTROL | offline guide + mirror workflow; one-call environment creation | — | no independent install trial | Run an outsider install trial |
| 043 bespoke deployments | OPEN | none | — | margin unmeasured | Business decision |
| 046 blame for agent defects | OPEN | evidence digests + audit logs + scorecard give a factual record | — | terms/liability need counsel | Legal review (external) |

## What was actually executed
Browser worker 72 tests, desktop 9, Section 5 230 (1 pre-existing skip), verification 23; ruff/tsc clean; browser benchmark; quota mutation 0.50→0.93; OpenAPI drift run; PG restore drill. **Not executed:** Docker-backed runs (daemon down), real macOS input/capture, live Section 1 auth, live SDK→S5→browser path, object-store restore, `make gate`/Section11/MCP/full builds.

## Defects found along the way
Pre-existing partial S1 work was red (6 defects, fixed); subresource egress and caller-supplied origins were unenforced (fixed); stale `section5/openapi.json` (reported, not changed); S3 quota tests were weak per mutation (strengthened).

## Pending integrations and decisions
A2: SDK→S5 enforcement, fixer/generator plug-ins, kill-on-quota. A1/S1: Postgres lease and consent tables. A4: approval/takeover UI. A5: wiring, deployment, scanning, regenerate or retire `section5/openapi.json`. External: real-OS smoke authorization, object-store drill credentials, Linux/Docker host, legal and pricing items.
