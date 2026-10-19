# Roadmap v1 (Master prompt part 60) — versioned 2026-10-18

Committed = scheduled in `docs/briefs/path-to-9-plan.md`. Exploratory = intent only, re-ranked from pilot data and customer interviews
(which do not exist yet). Exit criteria are checked against `docs/briefs/capability-matrix.md` rows being `verified`.

| Phase | Scope | Status | Exit criteria (evidence) |
|---|---|---|---|
| 1 Safe shared runs | shared runs, roles, redirects, handoff, digest-bound approval, audit, isolation (parts 04–12, 20–32) | Committed; built, residual gaps in plan S2 | all part 04–32 rows `verified`; two-user real-OTP browser proof; ops acceptance; threat model with no UNTESTED high |
| 2 Team workflows | presence, control leases, discussion/mentions, notifications, realtime UX, search (41–43, 46, 47, 50) | Committed (S4–S6) | invariant tests per row; keyboard/screen-reader pass on collab UI |
| 3 Platform | plans/artifacts, integrations, templates, admin console, usage controls, analytics (44, 48, 49, 51, 57) | Committed (S6–S7); live Stripe/SSO drills externally gated | signed webhooks tested; quota enforcement tests; provider drills recorded or marked gap |
| 4 Hardening | orgs/policy engine, security + compliance lifecycle, reliability, scale, observability (37–39, 52–55, 58) | Committed (S3, S8, S9); pentest/PITR/counsel externally gated | SLOs defined + failure-injection results; retention/legal hold/deletion tested |
| 5 Ecosystem | SDK/extension marketplace, public API tiers | Exploratory | only after phase 4 security review |

Rules: phases are not compressed into the four-session pilot promise. Pilot success (part 03) needs ≥5 real tasks per side before any
claim. Revisit order after the first real pilot and interviews; record changes by bumping this version.
