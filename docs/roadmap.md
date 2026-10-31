# Roadmap v1.1 (Master prompt part 60) — reviewed 2026-10-19

Committed = scheduled in `docs/briefs/path-to-9-plan.md`. Exploratory = intent only, re-ranked from pilot data and customer interviews
(which do not exist yet). Exit criteria are checked against `docs/briefs/capability-matrix.md` rows being `verified`.

| Phase | Scope | Status | Exit criteria (evidence) |
|---|---|---|---|
| 1 Safe shared runs | shared runs, roles, redirects, handoff, digest-bound approval, audit, isolation (parts 04–12, 20–32) | Committed; built, residual gaps in plan S2 | all part 04–32 rows `verified`; two-user real-OTP browser proof; ops acceptance; threat model with no UNTESTED high |
| 2 Team workflows | presence, control leases, discussion/mentions, notifications, realtime UX, search (41–43, 46, 47, 50) | Committed (S4–S6) | invariant tests per row; keyboard/screen-reader pass on collab UI |
| 3 Platform | plans/artifacts, integrations, templates, admin console, usage controls, analytics (44, 48, 49, 51, 57) | Committed (S6–S7); Stripe is out of current scope; live OIDC and other provider evidence remains gated | signed webhooks tested; quota enforcement tests; provider drills recorded or marked gap |
| 4 Hardening | orgs/policy engine, security + compliance lifecycle, reliability, scale, observability (37–39, 52–55, 58) | Committed (S3, S8, S9); pentest/PITR/counsel externally gated | SLOs defined + failure-injection results; retention/legal hold/deletion tested |
| 5 Ecosystem | SDK/extension marketplace, public API tiers | Exploratory | only after phase 4 security review |

Rules: phases are not compressed into the four-session pilot promise. Pilot success (part 03) needs ≥5 real tasks per side before any
claim. Revisit order after the first real pilot and interviews; record changes by bumping this version.

## Session 10 review (2026-10-19)

The roadmap remains phase-based: “Committed” means included in the delivery plan, not production
readiness. No real pilot baseline or customer interviews are available, so priorities have not been
re-ranked. Full product readiness is **NO-GO** until the matrix gates and external evidence in
`docs/briefs/evidence/s10/readiness-report.md` are satisfied. The ecosystem phase remains exploratory
and follows the security review; billing with Stripe remains outside current scope.

## Hx execution track (2026-10-31) — capability, does not re-order the phases above

Hx is the agentic language in `packages/language`: a checked subset, a driver that runs one
workflow against a host bridge inside its declared budget, and evidence a reviewer can read.
It is a capability track, not a phase — it neither compresses the four-session pilot promise nor
substitutes for the security review, and it adds no approval or merge surface.

| Milestone | State | Gate evidence |
|---|---|---|
| M1 vertical slice — check → authorise → host op → typed validation → evidence; budgets, denial, cancellation, replay | done | language suite green; `hxr` exit codes 0–5 as documented |
| M2 Section 4 runs a workflow; events land in Section 1 | done | sections 1/4 green (1223 / 1933); ordering and durability asserted against the contract; replay reproduces the digest; isolation guard clean |
| M3 live Section 5 — `repo.read` and `verify.tests` through the shipped runner | done | `section5/tests/unit/test_hx_live_host_ops.py`: real confinement, loud skip where there is none |
| M4 model relay — declared roles, fail-closed without a router, no forced downgrade, secrets scrubbed by content | done | `test_host_model_relay.py` asserts on the captured prompt, the log, the events and the evidence |
| M5 enums, record construction, `for` bounded by the list and by `budget.steps` | done | code + fixture + golden per construct; `test_language_growth.py` also pins what did not arrive |
| M6 `budget.cost` enforced against a host price table; a grant revocable mid-run | done (`3637e3f`) | the run stops *before* the crossing it cannot pay and an unpriced crossing is refused, not free; `declared-not-enforced` is said out loud in the evidence; revocation denies the next crossing, cannot widen the request, and is logged with its reason; the driver's code set is pinned by `test_host_codes.py`. Language 295, spec `0.2.0` |

Open by design, each named in `docs/hx-execution-architecture.md` §4.2/§6: a price is the host's
estimate, not a settled bill (only Section 3's ledger knows what a provider charged); a revoked
capability stops effects but does not kill a call in flight; replay covers successes only;
secret detection is prefix-based, not entropy-based; `for` has no `break`; one workflow per run;
no modules, no mutation; the driver bounds host calls, not CPU — real confinement stays
Section 5's job.
