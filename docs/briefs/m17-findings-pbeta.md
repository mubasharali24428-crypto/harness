# M17 Phase A — P-BETA findings (code quality / architecture)

> Seat: P-BETA, ELITE-FORGE. Baseline audited: HEAD `3c794b2` (working tree clean
> except this file and `m17-findings-odelta.md`). Method: grep/AST sweeps + targeted
> reads; every finding cites `file:line`. No fixes applied, no git writes.
>
> Headline: the CI isolation guard only catches *literal* `sectionN/...` strings.
> 17 production files import sibling sections **by installed package name** or via
> **runtime `sys.path.append("../sectionN/src")`** and pass every gate. The same
> blind spot let 1,372 lines of byte-identical middleware fork four ways.

## [CRITICAL] C-1 — Section-isolation law is violated by package-name imports the guard cannot see

`section7/scripts/ci_route_module_guard.sh:139-141` forbids only regex shapes
`(from|import) (\.+)?section[0-9]+` and `'...section[0-9]+/'`. Every real
cross-section edge in the codebase uses the section's *distribution name*
(`harness_identity`, `harness_router`, …), which matches neither pattern.

Inventory (17 edges, all in shipped src, all invisible to `make guard-isolation`):

| Importer | Imports | Evidence |
|---|---|---|
| s2 → s1 | `harness_github/app.py:47` (`from harness_identity.eventbus import EventBus`) | runtime |
| s3 → s1 | `harness_router/ratelimit_hooks.py:32-33` (+ sys.path injection) | module level |
| s4 → s1 | `harness_orchestrator/service.py:26` (`from harness_identity.timeutil import db_now`), `service.py:494` (lazy `audit_emit`), `plan_gate.py:43-44`, `run_queue.py:25-26`, `api.py:21-22`, `ratelimit_hooks.py:32-33` | 6 files |
| s4 → s3 | `harness_orchestrator/run_metering.py:114` (`from harness_router.pricing import PricingEngine`) + `:111-112` sys.path append of both s1/s3 trees | lazy |
| s6 → s1 | `harness_meeting/{service.py:16,prompt_store.py:13,api.py:15,chat.py:13,summary_service.py:13,ratelimit_hooks.py:32}` | 6 files |
| s8 → s1 | `harness_review/api.py:97` (`install_security_headers`) | lazy |
| **s1 → s8** | `harness_identity/settings_api.py:69,83,108` imports `harness_review.heuristic` | reverse edge — S1 depends on S8 |

Resolution mechanism: 12 files execute `sys.path.append(str(_ROOT/"section1"/"src"))`
at import time (see table); pyprojects deliberately omit these deps
(`section4/pyproject.toml` deps list has no harness_identity; `section2/pyproject.toml:30-31`
says "resolved from the monorepo checkout, never installed into this venv";
`deploy/runners/services.py:10-12` inserts every `section*/src` globally).

Why it matters beyond law-lettering:
- The "sections depend ONLY on contracts" invariant (AGENTS.md layout, M13 audit,
  guard docstring lines 20-27) is enforced against a threat model that no longer
  exists — actual coupling flows through names the guard ignores. A refactor of S1's
  `limits.py`/`timeutil.py`/`eventbus.py` can break S2/S3/S4/S6/S8 with zero CI
  signal at the isolation layer.
- s1→s8 (`settings_api.py`) means even the *direction* story ("everyone may depend
  on S1") is broken: identity now imports review-domain config.

## [CRITICAL] C-2 — Byte-identical 343-line envelope middleware forked ×4; only S1 ever installs it

`md5` equal across:
- `section1/src/harness_identity/envelope_middleware.py`
- `section3/src/harness_router/envelope_middleware.py`
- `section4/src/harness_orchestrator/envelope_middleware.py`
- `section6/src/harness_meeting/envelope_middleware.py`

(343 lines each = 1,372 duplicated lines; identical `except Exception` blocks at
lines 231/279/309/323/342 in all four.) The docstring (line 26-27) admits it: "This
file is a section-local canonical copy; contracts/ is FROZEN and stays untouched."

Dead-copy proof: `install_error_envelope` (def at line 75 of each copy) is invoked
from exactly one production site — `section1/src/harness_identity/app.py:141-143`.
Deploy wiring never calls it for other services (`deploy/runners/services.py:83-106`
`simple_app()` installs CORS + security headers only; `app_models`:141, `app_orchestrator`:206,
`app_sandbox`:302). S3/S4/S6 copies are imported by their tests
(`section3/tests/unit/test_f1_envelope.py:16,22,40`; `section4/tests/test_f1_envelope_idempotency.py:26,96`;
`section6/tests/test_f1_envelope_idempotency.py:25,84`) — i.e., 686 dead lines in
production whose only consumers are their own tests.

Consequence: the frozen error-envelope contract (api-conventions.md:14) is actually
enforced on ONE service. S3/S4/S6 handlers raise bare `HTTPException(detail=…)`;
S7 compensates with a three-shape parser (`section7/src/lib/api.ts:36-72`: contract
shape, FastAPI-wrapped detail, plain-string detail). The frontend has become the
error-envelope normalizer — contract drift absorbed client-side.

Same pattern, second instance: `limits.py` byte-equal ×3
(`section1/src/harness_identity/limits.py` == `section4/src/harness_orchestrator/_limits.py`
== `section6/src/harness_meeting/_limits.py`, md5 `8a4896b4`). Parity is test-pinned
(`section1/tests/test_request_size_limits.py:226-232`), but the pin itself carries a
stale skip branch: `test_mirrored_section_limit_copies_match_registry` still skips when
mirrors "land with their sections' G3 commits" (line 228-229) — those commits landed
in M12; the escape hatch can silently disable the drift guard if paths move.
Near-duplicate: `ratelimit_hooks.py` s4 vs s6 = 42 diff lines out of ~120, same skeleton.

## [HIGH] H-1 — TS mirror marks always-serialized pydantic fields optional; S7 tests enshrine the wrong wire shape

`contracts/ts/generate.py:129-135`: any field with a pydantic default
(`Field(default_factory=...)` or `default=...`) is emitted as optional (`?`)
in the TS mirror. But pydantic v2 **always serializes** defaulted fields —
`AgentPlan.estimatedCostUsd` (contracts plan.py:12, default `0.0`) is present in
every JSON response. Proof from the live wire: `section3/tests/conformance/test_router_conformance.py:74-75`
asserts `data["estimatedCostUsd"] > 0` on a real response; S4's own conformance golden
lists it as an unconditional key (`section4/tests/conformance/test_plan_gate_conformance.py:38`,
key-set equality at :62).

Yet the generated mirror says `estimatedCostUsd?: number` (`contracts/ts/src/plan.ts:14`),
and S7 codifies absence as legal: `section7/app/dashboard/planGateApi.test.ts:64-71`
("tolerates optional AgentPlan fields being absent on the wire", asserting each
optional field `toBeUndefined()`). Same for EventEnvelope.payload
(`generate.py` marks `Field(default_factory=dict)` optional → `payload?` in
`contracts/ts/src/events.ts:28`, while Python `events.py:35` guarantees `{}`).

Impact: frontend written strictly to this mirror will dereference `.steps?.map(...)`
etc. and silently render empty states for data that is always present — a systematic
client/server shape disagreement laundered through the type system by the codegen
that was supposed to make drift "mechanically impossible" (generate.py:13-14).

Related residue: `contracts/ts/types.d.ts` (hand mirror, header v0.3.0 after 6254fe5)
still lacks the four post-v0.2 event types that `ts/src/events.ts` has — two parallel
TS sources of truth; only one feeds section7's `@contracts/*` alias
(`section7/tsconfig.json:19`). AGENTS.md:14 still advertises types.d.ts as "the"
TS mirror.

## [HIGH] H-2 — Tautological asserts: four tests contain assertions that cannot fail

- `section1/tests/test_ws_replay_cap.py:480` —
  `assert all(e.seq < page.oldest_seq_sent or True for e in resumed.envelopes)`:
  `or True` makes the predicate constant-True. The seq-window resume property the
  test name promises is asserted nowhere else in the function (only count ≤ cap).
- `section1/tests/test_fk_deletion_story.py:386` —
  `assert pr.approved_by is None or True` (comment admits seed never sets it).
- `section1/tests/test_runs_index_tuning.py:395` —
  `assert "created_at" in line and '"id"' in line or "id=?" in line or True` —
  `and/or/or True` ⇒ tautology; ordering-column coverage unpinned.
- `section1/tests/conformance/test_edge_cases_lifecycle.py:282` —
  `assert "error" in body or True`.

All four look like deliberate "keep the shape visible but don't fail" edits; they
belong in comments, not asserts. Cheap grep tripwire: `assert .* or True`.

## [HIGH] H-3 — Authz ownership check silently degrades to allow on DB-less wiring and missing project row

`section4/src/harness_orchestrator/authz.py:50-58`:

```python
factory = getattr(service, "session_factory", None)
if factory is None:
    return run_data          # bare unit mode: no DB, nothing to check against
...
if not project_id:
    return run_data          # run never bound to a project
```

Two silent-pass branches inside the tenant boundary. The no-factory path is
test-pinned as intended behavior (`section4/tests/unit/test_authz.py:94-99`
`test_authorize_run_skips_check_without_db`), and route wiring uses `_authz_dep`
(api.py:294) where deploy always supplies a factory — but nothing structurally
prevents a future entrypoint (script, runner, new route) from calling the service
without one and getting authorization-free access. The missing-project-id branch
returns success without logging. Given charter law "Nothing outside Section 5's
allow-list executes," fail-closed would be the conservative posture here; at minimum
the skip should be observable (log/metric), not silent.

## [MEDIUM] M-1 — Orphaned helpers (zero non-self references repo-wide, incl. deploy/scripts/config)

Verified by AST sweep + whole-repo word-boundary grep (src, tests, deploy/, scripts/,
Makefile, workflows):
- `section5/src/harness_sandbox/output_parser.py:78` `parse_output` — ORPHAN
- `section5/src/harness_sandbox/docker_runner.py:4` `DockerSandboxRunner` — ORPHAN
- `section8/src/harness_review/store.py:35` `get_pr_by_github_number` — ORPHAN
- `section8/src/harness_review/team_thresholds.py:67` `parse_stored_thresholds_for_admin` — ORPHAN
- `section8/src/harness_review/risk_analyzer.py:102` `RiskReport` — ORPHAN
- `section10/src/harness_onboarding/steps.py:27` `ServiceProbe` — ORPHAN
- `section1/src/harness_identity/audit_emit.py:326-340` `emit_plan_rejected_contract` —
  ORPHAN and self-documenting as process debt: returns a dict telling ATLAS which
  helper another lane should call. Its consumer (`emit_plan_rejected`) IS wired
  (s4 service.py:494); this coordination stub should have been deleted at merge.
- Contracts-level: `ws_envelope` exported from `harness_contracts/__init__.py:10`
  (`api.py:10-11`) — zero references anywhere (and its 4-key shape omits
  `projectId`/`runId`, so it contradicts the frozen WS convention it mirrors).
  `EVENT_TYPES` has zero src consumers outside contracts' own conformance tests.
- Test-only-alive (production-dead): `heartbeat_timed_out` (run_queue.py:90),
  `get_queue_snapshot` (run_queue.py:315) — no production caller; kept alive solely
  by unit tests, masking that the SQL predicates they mirror (`_HEARTBEAT_STALE_SQL`,
  run_queue.py:102-108) are what actually runs.
- Framework-alive (NOT flagged): FastAPI route handlers, ORM models, protocol impls.

## [MEDIUM] M-2 — Golden fixtures re-implemented per section instead of shared via contracts

AGENTS.md rule: "Every section ships golden-fixture conformance tests against
`harness_contracts`." In practice five near-independent fixture modules exist
(all distinct md5): `section{3,4,5,6,8}/tests/fixtures/golden_fixtures.py`
(sizes 23/22/37/21/71 lines). Only S4/S8 share values (identical GOLDEN_GOAL
string, s4:5 vs s8 fixtures); the rest mint their own goldens. There is no
fixtures package under `contracts/` at all — the "golden" vocabulary is per-section
convention, so cross-section checkpoint tests cannot rely on common goldens.

## [MEDIUM] M-3 — Error-envelope bypasses in S8 routes (blanket except leaking str(detail))

`section8/src/harness_review/api.py:135,209,248`: `except Exception as exc:` →
`HTTPException(status_code=500, detail=str(exc))`. This violates api-conventions.md's
frozen error body (`{"error": {code, message}}`) twice over: raw string detail (no
machine code) and unbounded internal text surfaced to clients (contrast S3's
`api.py:292` which wraps with logger.warning + coded envelope). With S3/S4/S6's
envelope middleware uninstalled (C-2), nothing downstream re-normalizes these.

## [MEDIUM] M-4 — Blanket-except census: 118 sites, 8 fully silent

`except Exception` appears 118× across section src. Most carry `# noqa: BLE001` +
rationale comments (fail-open limiter `ratelimit.py:327`, telemetry guards, etc.) —
consistent house style, acceptable. The unannotated minority deserves review:
- `section5/src/harness_sandbox/service.py:90-91` — swallows terminal-output tap
  failures with bare `pass`, no noqa, no comment (telemetry loss invisible).
- Un-annotated blanket excepts also at `eventbus.py:199` (documented race — fine,
  but style-inconsistent), `envelope_middleware.py` ×5 per copy (see C-2).
Silent-`pass` total: 8 sites (billing.py:225-226, service.py:89-90 s2,
pr_open.py:102-103/135-136 s2, service.py:196-197 s4, service.py:54-55/90-91/110-111 s5).

## Observations (no severity — context for Phase B planning)

- The isolation guard's self-description is honest about scope (literal patterns
  only), but Makefile/CI present it as THE isolation law (ci.yml:44-46). Any fix
  lane for C-1 should extend the guard to distribution-name imports
  (`from harness_(identity|github|router|orchestrator|sandbox|meeting|review|onboarding)`)
  originating outside the owning section — mechanically greppable today.
- C-2's clean fix direction is contracts-level promotion (the file already imports
  only `harness_contracts.error_body` + fastapi/starlette), which AGENTS.md's
  freeze rules would need a versioned exception for — flagging for ATLAS, not fixing.
- Duplicate-looking test pair `section6/tests/unit/test_m16_x2_message_pagination.py`
  vs `test_m16x2_message_pagination.py` is NOT duplication: structural pins vs
  behavioral suite, cross-referencing each other in docstrings (:3-4).

## Verification performed

- md5 dupes: `find section*/src ... -exec md5 -r {} +` → exactly 2 duplicate groups (C-2, limits ×3).
- Cross-package imports: per-package grep loop, manually confirmed each hit is an
  import statement (not comment/docstring) before listing.
- Orphans: AST top-level def/class sweep (99 candidates) filtered by repo-wide
  `grep -rn -w` across *.py/*.ts/*.tsx/*.sh/*.yml/*.toml/Makefile excluding
  venv/node_modules/cache dirs; each ORPHAN above had zero hits outside its own file.
- Tautologies: literal grep `assert .* or True` → 4 hits, all read in context.
- Ran `section1/.venv/bin/python -m pytest tests/test_version_pin_conformance.py -q`
  → 2 passed (version-stamp alignment verified green at HEAD; earlier audits'
  v0.2.0 findings are historical).
