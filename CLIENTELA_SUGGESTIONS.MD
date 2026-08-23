# CLIENTELA_SUGGESTIONS.MD
**Product:** AI Engineering Harness — meeting-driven AI coding-agent platform with governance
**Audit date:** 2026-08-23 · **Method:** ALEX_TEAM analyst fleet, 40+ agents across 3 waves, all findings evidence-based (file:line)
**Scale:** ~1,130 raw findings → deduplicated registry → 30 solution clusters → 5-wave remediation campaign

---

## Executive summary

The harness's engineering core is genuinely differentiated: a human plan-approval gate no competitor has, an event-sourced audit trail of every agent action, an OS-level sandbox allow-list, team-scoped multi-tenancy, and true self-host with BYO-model. But every surface a user touches in their first ten minutes is broken or fabricated: login mints fake project IDs, the plan gate is unreachable from any screen, @ai is a regex echo-bot, review never loads a PR, and the S3 model router is fully unauthenticated. The budget system aggregates a ledger nobody writes to; the rate limiter guards a route that doesn't exist and is never installed; audit logging protects an empty table.

This document is the complete inventory and the plan to fix it.

## How to read this document

| Part | Contents |
|---|---|
| [Part 1A — CRITICAL & HIGH registry](docs/clientela/part1a-critical-high.md) | C-001…C-006 + H-001…H-025, deduplicated, evidence-linked |
| [Part 2 — Solutions catalog](docs/clientela/part2-solutions.md) | S-001…S-030 clusters: fixes / effort / priority / approach |
| [Part 3 — Strategic roadmap](docs/clientela/part3-roadmap.md) | Competitive position, adoption killers K1–K10, Now/Next/Later/Future horizons, pricing sketch, metrics |
| [Part 4 — Remediation campaign](docs/clientela/part4-remediation-plan.md) | 5 waves, dispatch-ready Wave-1 briefs, verifier pairs, risk register, effort totals |
| [Raw evidence](docs/clientela/) | 23 unfiltered audit reports (raw-01…raw-23) — every claim's provenance |

## The six CRITICALs

| ID | Finding | One-line fix |
|---|---|---|
| C-001 | Unverified-email team creation + invite account-injection chain | Enforce OTP ownership default; delete legacy direct-add path |
| C-002 | `write_audit` has zero production call sites | Wire allowlist events into every sensitive handler |
| C-003 | Budget caps inert end-to-end (no writers + fail-open + TOCTOU) | Record dsh usage; atomic check; fail closed on money paths |
| C-004 | Engine config lost on recreate; placeholder counts as live | Boot validation; no silent fallbacks |
| C-005 | Plan gate unreachable from any UI screen | Add API clients + PlanGate card |
| C-006 | Login fabricates projectId/runId | Fetch real identity post-auth or route to create-flow |

## Remediation at a glance

| Wave | Focus | Lanes | Verifiers |
|---|---|---|---|
| W1 | P0 security + flow blockers | 8 | 8 PRIME+SUB pairs |
| W2 | Core UX completion (@ai, 401, roster/review pairs, envelope, idempotency) | ~8 | per-vertical pairs |
| W3 | Hardening/perf (pool math, PITR drill, E2E, NUMERIC, migration harness) | ~6 | seam probes |
| W4 | Product features (roadmap NOW/NEXT items) | flexible | per-feature |
| W5+ | Roadmap program (indexing/RAG, editor, media) | program-scale | per-mission |

Full briefs, seam probes, risk register, and effort math: Part 4.

## Provenance

Every finding traces to a pasted command output in `docs/clientela/raw-*.md`. Analysts were read-only; the only writes were these documentation files. Provider outages killed ~19 agent runs during collection; every casualty was footprint-audited and its exploration inherited by its successor.
