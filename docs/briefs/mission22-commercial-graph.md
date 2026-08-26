# MISSION 22 — COMMERCIAL SKELETON — TASK GRAPH

> ALEX-ELITE dual teams. Baseline: cc69d21. Source: mission17-10x-plan.md
> §LATER commercial skeleton (O-DELTA killer-gap #2). This closes the
> "cannot lawfully charge anyone" gap. Migration slots: 0031+ if needed.

| Lane | Mission | Owned paths | Team |
|---|---|---|---|
| M1 | Legal foundation: LICENSE (choose per user direction — default MIT for code), TERMS.md, PRIVACY.md skeletons with honest placeholders marked; PRICING.md defining tier structure (Free/Pro/Team) mapped to existing PLAN_TIERS + metering | LICENSE · TERMS.md · PRIVACY.md · PRICING.md · docs/ | 2 |
| M2 | Tier enforcement: PLAN_TIERS become behavior-bearing — Free caps runs/day + context index size; Pro unlocks full; Team adds seats/audit retention; enforcement at run-start + index API; upgrade CTA in UI on cap-hit | section1 plans/tiers storage · section4 run-start gate · section11 index API · section7 cap-hit UX · tests | 1 |
| M3 | Stripe live adapter: instantiate the existing Protocol-backed client behind HARNESS_BILLING=stripe; checkout session create, webhook receiver (signature-verified, idempotent), subscription status → plan tier sync; test-mode only in CI | section3 or section1 billing adapter (existing stub location) · webhooks route · tests w/ mocked Stripe | 1 |
| M4 | SSO hooks: OIDC discovery-based SSO for Team tier (standard flows only; no vendor SDKs); per-team IdP config storage; SSO login path alongside OTP; documented self-host setup | section1 auth OIDC flow (NEW) · team IdP config storage · tests w/ mock IdP | 1 |
| M5 | MCP server expose: harness as MCP tool-server exposing run-start/run-status/context-search as MCP tools over the standard protocol; enables Claude/Cursor-class clients to drive the harness | section11 or NEW mcp/ module · tool registry · tests w/ MCP inspector pattern | 2 |

Gate law: same as all waves. Exit criteria: a stranger can read LICENSE +
PRICING and know exactly what they get free vs paid; cap-hit produces an
upgrade CTA not a dead end; Stripe test-mode checkout completes E2E against
the mock; OIDC login works against a mock IdP; `mcp-inspector` lists the
harness tools.
