# A3-S2 — Browser diagnostics, preview, replay and visual QA

## Added (`workers/browser/src`)
`diagnostics.ts` (sanitized capture + HAR + **subresource egress guard**), `preview.ts` (team-scoped preview registry, static preview), `profiles.ts` (mobile/touch), `replay.ts`, `visual.ts`; new actions `tap`, `diagnostics`, `pwaCheck`, `visualCheck`, `e2e`; server routes `POST /previews`, `GET …/har`, `GET …/replay-log`, artifact fetch by id from a per-session index (no caller paths).

## Security defects found and closed
1. **Subresource egress was unenforced** (only top-level navigation was checked): a page script could `fetch()` any host. Now every http(s) request is routed through the allowed-origin guard and aborted otherwise.
2. **Caller-supplied `allowedOrigins` accepted loopback/metadata/public hosts** on `POST /sessions` (SSRF-by-configuration). Private origins now require a team-scoped registered preview; public origins need an operator allowlist (empty by default).

## Evidence
| Command | Result |
|---|---|
| `cd workers/browser && npx tsc --noEmit` | exit 0 |
| `npx vitest run` | 12 files, 72 passed, 0 skipped, ~6 s |
| `npx vite-node ../../scripts/agent-program/a3/bench-browser.ts` | `bench-browser.json` |

Benchmark (tool latency only, **excludes model time**; headless Chromium, macOS arm64, 1280×800@1): cold browser+first session 644 ms, warm second session 42 ms; per-op p50/p95/p99 ms — navigate 11.3/15.6/19.6, type+verify 41/43/62.6, observe 2/2.4/2.5, click+postcondition 46.4/57.3/62.3. Accuracy on the form fixture: 80 steps, 0 failed/wrong-target. Model-inclusive timing was not measured (no model in this lane). Single run on one machine; no variance claim.

Secret test: cookie values, Authorization bearer, response `set-cookie`/custom headers, `?token=` query values, console passwords/JWT/Bearer text are absent from the diagnostics result and the HAR.

## Limits
- Visual baselines are Chromium/macOS only; no cross-platform tolerance study.
- Replay log is in memory; typed text is digest-only so those steps are skipped unless the caller supplies matching text.
- Preview of a *project's* dev server needs an S5 task and A2 wiring (not done).
- Redirect-based SSRF to a private IP is blocked by the origin allowlist; DNS rebinding of an allowed hostname is not addressed (only literal loopback origins are used in previews).

## Rollback
Delete the files above and revert `session.ts`, `actions.ts`, `server.ts`, `types.ts`, `index.ts`; remove `pngjs`/`pixelmatch` from `workers/browser/package.json`.
