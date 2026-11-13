# Week 06 — engineering delivery and independent acceptance

Opened 2026-11-12. Base checkout: `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5`,
branch `w28-s1-delegate`, with uncommitted changes. **Not a clean CI-certified release.**
The user assigned test execution to another agent. The original build pass ran only `python -m
py_compile` on the changed files and `git diff --check` — **no pytest, `make gate`,
real-PG probe, e2e-compose, paid model run or security drill was executed in that pass.**
A later defect-fix pass (2026-11-12) ran the focused section4/section8/section10 suites and
the static gates; raw results are recorded in §8. Full release acceptance is still pending.

## 1. Scope and states

| Work | Role | State | Evidence / limitation |
|---|---|---|---|
| P-11 / I-09 verification-first done-signal | S+B | **Implemented — focused tests passing** | §2; defect-fix pass ran the section4 tests (§8); full gate + live seam still pending |
| P-11 / I-10 cross-agent adversarial pre-review | S+B | **Implemented — focused tests passing** | §3; defect-fix pass ran the section8 tests (§8); full gate + real-model pass still pending |
| P-12 measure acceptance/time/cost/intervention trend | R (owner) | **Not started — owner-gated** | Needs a live pilot's raw numbers; engineering cannot produce partner measurements |

P-11's acceptance also asks for "human review time measured before/after" — that is a
pilot measurement (owner/test-agent), not buildable here. The code capability is delivered;
the before/after evidence is not.

## 2. I-09 verification-first (done means verified, not "diff attached")

**Gap fixed:** `service.signal_done` called `reviewer.evaluate_changes` without test
evidence, so the reviewer's `test_results_summary="passed"` default made verification
vacuous. The done-signal gate only required a non-empty diff.

**Design (recorded evidence, not caller-asserted):**
- `review_summary.verification_evidence()` (`section4/src/harness_orchestrator/review_summary.py:167`)
  derives `green` / `red_to_green` / `failed` from the engine's recorded `agent_tool_calls`
  (same source as the review summary). The latest run of each command decides; **`green`
  requires EVERY latest command to have passed** — a single failed/timeout/refused/unknown
  latest run blocks it even when another command passed (defect-fix 1). Unknown and "no test
  ran" are never a pass.
- `signals.VerificationEvidenceError` (`section4/src/harness_orchestrator/signals.py:69`,
  400 `VERIFICATION_REQUIRED`) and `validate_done_signal(..., verification_required, verification)`
  (`signals.py:108`) add a fourth gate step after role/liveness/diff. Default off.
- `OrchestratorService._verification_first_enabled()` (`service.py:2919`) reads
  `HARNESS_VERIFICATION_FIRST` (default false). `gate_done_signal` (`service.py:2925`) reads
  recorded evidence via `_recorded_verification()` (`service.py:2951`, queries
  `agent_tool_calls WHERE reported_by='engine'`) and enforces the gate only when enabled.
  `signal_done` passes the real derived `test_results_summary` to the reviewer when enabled;
  when disabled the existing call is byte-for-byte unchanged.
- New knob documented in `.env.example` (W15 env-knob gate).

**Why default-off:** enabling it unconditionally would break the dry-mode demo and golden
conformance flow, which record no live tests. The owner enables it for live supervised
pilots. This matches the existing flag pattern (`HARNESS_REQUIRE_OWNERSHIP`, `HARNESS_BILLING`).

**Tests (run in the defect-fix pass — raw results in §8):**
`section4/tests/unit/test_review_summary.py` (10 `verification_evidence` cases:
unknown/no-tests/green/red→green/latest-failure/unknown-exit/refused, plus one-green-one-red,
same-command-fail-then-pass, pass-plus-unknown) and `section4/tests/test_done_signal.py`
(4 pure gate cases + 5 service cases: default-off preserves flow, rejects without green,
accepts recorded green, rejects recorded failure, rejects green-plus-red). A minimal
`agent_tool_calls` scratch mirror was added to that file's DDL.

## 3. I-10 cross-agent adversarial pre-review digest

**Capability:** a cheap adversarial reviewer digests risk BEFORE the human reviews.
- `models.PreReviewDigest` (`section8/src/harness_review/models.py:92`) — advisory payload:
  `risk_flags`, `highest_severity`, `concerns`, `model_used`, `cost_usd`,
  `requires_human_attention`, `basis`. **No pass/fail, approve or merge field.**
- `pre_review.build_pre_review_digest()` (`section8/src/harness_review/pre_review.py:76`)
  combines deterministic `RiskAnalyzer` path flags with an adversarial `ModelRole.REVIEWER`
  pass. `_redact_secrets()` (`pre_review.py:51`) masks PEM blocks and secret-bearing
  KEY=value / KEY: value assignments before model context; the diff is framed as untrusted data.
  `Authorization: Bearer/Basic <value>` credentials are redacted only as of the item-1 fix in
  the 2026-11-13 pass (§9) — before it the scheme word was masked but the credential value
  leaked; proven by `test_bearer_and_basic_credentials_redacted`.
  Over-budget or an outage withholds the model call honestly and falls back to deterministic
  rules — never a fabricated clean pre-review. The model used is recorded (no silent downgrade).
- `ReviewService.get_pre_review_digest()` (`section8/src/harness_review/service.py:429`)
  resolves the diff exactly like `get_review_data` and returns the digest. **Additive** —
  `approve`, `request_changes`, `get_review_data` and `evaluate_verdict` are unchanged; the
  human verdict and manual merge are untouched.

**Tests (run in the defect-fix pass — raw results in §8):**
`section8/tests/unit/test_pre_review.py` (11 cases: deterministic risky/low-risk, recorded
model pass, budget withhold, outage fallback, secret redaction, prefixed secret names
redacted, ordinary identifiers preserved, PEM block redacted, untrusted framing,
advisory-only/no-authority).

## 4. Invariants honored

- **Frozen contracts untouched.** No file under `contracts/` was modified. `DoneSignalRequest`
  (`section4/src/harness_orchestrator/api.py:580`) was NOT changed — I-09 reads recorded server-side evidence instead of
  adding a request field, so the frozen contract surface and the TS mirror are unaffected.
- **Isolation law.** section8 imports only `harness_contracts` (+ its own modules); the
  ModelRouter is injected, as `SummaryGenerator` already does. section4 does not import section8.
- **No auto-merge / no silent downgrade / mandatory human approval.** I-10 is advisory; I-09
  strengthens the human done-gate. Model use is recorded.
- **Secrets never in model context.** I-10 redacts before the model call; I-09 reads only
  tool/target/outcome/summary, not secret values.
- **Non-breaking.** Both features are additive; I-09 is default-off. Existing flows preserved.

## 5. Test-agent handoff — execute and attach raw results

Bootstrap first (`make bootstrap`). From the repo root:

```sh
make lint-section4
make test-section4
make lint-section8
make test-section8
# Focused new coverage:
./section4/.venv/bin/python -m pytest \
  section4/tests/unit/test_review_summary.py section4/tests/test_done_signal.py -q -rxXs
./section8/.venv/bin/python -m pytest section8/tests/unit/test_pre_review.py -q -rxXs
# New env knob must be documented (W15 gate):
python3 scripts/check_env_knobs.py
# Frozen-contract TS mirror drift (expected unaffected — no contracts/ change):
python3 contracts/ts/generate.py --check
# Isolation law (section8 imports only contracts):
make guard-isolation
```

Then exercise the live seam on a disposable scratch team (never customer data without consent):
with `HARNESS_VERIFICATION_FIRST=true`, a done-signal on a run with no recorded green test must
return 400 `VERIFICATION_REQUIRED`; with a recorded green test it must pass; with the flag unset
the prior behavior must hold. Record command, exit code, raw output, run IDs, host, Python/Node
versions, source hash and any XFAIL/XPASS reasons. Do not weaken assertions to make them pass.

## 6. Acceptance still required (not supplied by engineering)

- Independent execution of the new tests + full section4/section8 gates at this revision.
- P-11 "human review time before/after" and P-12 acceptance/time/cost/intervention trend —
  these need a live supervised pilot's raw numbers (owner/test-agent).
- A real-model I-10 pre-review on a partner PR (this pass used no provider; dry/fake only).
- Owner decision to enable `HARNESS_VERIFICATION_FIRST` for the pilot.

## 7. Owner gates that engineering cannot complete

- Signed partner SOWs + live supervised pilot (P-05/P-09) to produce measurement data.
- P-12 measurement and the P-11 before/after review-time evidence.
- P-D6 approval-envelope policy (NOT touched here; I-05 remains gated/separate).
- Independent test/security acceptance and release go/no-go at the tested revision.

## 8. Defect-fix pass (2026-11-12) — raw test results

A defect-fix pass corrected two verified defects in the W6 work and re-ran the focused
suites from each section's OWN directory (the root `.env` breaks
`test_f1_envelope_idempotency`). Host: darwin arm64; Python 3.13 (section venvs); source:
`7c1acf4` working tree (uncommitted). No provider/paid calls; no docker changes; `contracts/`
untouched.

Defects fixed:
- **Fix 1 (I-09 correctness):** `verification_evidence` `green` now requires EVERY latest
  command to pass (`bool(latest) and all(r["result"] == "passed" ...)`), so one green plus one
  red command is no longer green. `red_to_green`/`failed` unchanged. Added 3 unit cases + 1
  service case (`test_verification_first_rejects_green_plus_red`).
- **Fix 2 (I-10 security):** `_SECRET_ASSIGN_RE` now allows an uppercase/underscore prefix
  (`OPENAI_API_KEY=`, `DB_PASSWORD=` redacted) while preserving `tokenizer=`/`token_count=`.
  Added 3 redaction cases.

Raw results (command → output → exit code):

```text
$ cd section4 && .venv/bin/python -m pytest tests/unit/test_review_summary.py tests/test_done_signal.py -q -rxXs
81 passed, 11 warnings in 1.17s
EXIT_CODE=0

$ cd section8 && .venv/bin/python -m pytest tests/unit/test_pre_review.py -q -rxXs
11 passed in 0.02s
EXIT_CODE=0

$ cd section10 && .venv/bin/python -m pytest tests/unit/test_compose_journey_health.py -q
14 passed in 0.04s
EXIT_CODE=0   # 14 tests, not the 13 estimated in the fix request; all pass

$ cd section1 && .venv/bin/python -m pytest tests/test_deploy_volume_isolation.py -q
9 passed in 0.45s
EXIT_CODE=0

$ cd scripts/ops && ../../section1/.venv/bin/python -m pytest test_stack_tags.py test_stack_tags_exit.py -q
12 passed, 1 xfailed in 0.05s
EXIT_CODE=0

$ python3 scripts/check_env_knobs.py
env knob catalogue complete: 63 runtime reads, all in .env.example
ENV_EXIT=0

$ python3 contracts/ts/generate.py --check
TS mirror up to date.
TS_EXIT=0

$ make guard-isolation
GUARD[isolation] OK: 283 src file(s) across 10 section(s) —
                 no cross-section tree/package/sys.path/star-reexport edges.
ISO_EXIT=0

$ git diff --stat -- contracts
(empty)
CONTRACTS_DIFF_EXIT=0
```

**Open defect (the `1 xfailed` above):** `test_main_should_exit_nonzero_when_docker_ps_fails`
(`scripts/ops/test_stack_tags_exit.py`, `xfail(strict=True)`) asserts `stack_tags.main()` should
exit non-zero when `docker ps` fails; today it exits 0. This is a known defect marked xfail,
recorded as OPEN — not an expected/benign skip. The 2026-11-13 pass left the code unchanged
because the zero exit is documented by design (§9, item 4).

Still pending (not run here): full `make gate`/lint, real-PG probes, e2e-compose, the live
`HARNESS_VERIFICATION_FIRST` HTTP seam on a scratch team, and a real-model I-10 pass. The
focused unit/service tests above pass; that is not full release acceptance.

**Fix 4 (tutorial routes) was NOT applied — premise contradicted by the code.**
`POST /api/v1/orchestrator/plans` and `/plans/{planId}/approve` exist (`plan_gate.py:303`,
`:343`) and are mounted unconditionally in the deployed orchestrator app
(`deploy/runners/services.py:580`); approve returns `{runId}` (`plan_gate.py:374,385`). The
`api.py` `/runs/{runId}/plan` route requires a pre-existing runId and is not the run-minting
entry point. Tutorial §4 and week-04 §2 are accurate as written and were left unchanged.

No commits, pushes, provider calls, partner messages, customer-data uploads or signatures
were made. I-09/I-10 focused tests pass; full acceptance remains with the test agent/owner.

## 9. Follow-up defect-fix pass (2026-11-13) — raw test results

Closed the remaining verified gaps in the uncommitted W5/W6 work; ran the focused suites from
each section's OWN directory (the root `.env` breaks `test_f1_envelope_idempotency`). Host:
darwin arm64; Python 3.13 (section venvs); source `7c1acf4` working tree (uncommitted). No
provider/paid calls; no docker changes; `contracts/`, `docs/TUTORIAL.md` and `week-04.md`
untouched (TUTORIAL.md carries a pre-existing diff from another lane, not this pass).

- **Item 1 (I-10 security — Bearer/Basic leak):** `_SECRET_ASSIGN_RE`
  (`section8/src/harness_review/pre_review.py:45`) now allows an optional auth scheme word
  (`(?:(?:bearer|basic|token)\s+)?`) between the separator and the value, so
  `Authorization: Bearer abc123` redacts `abc123`. Before the fix it returned
  `Authorization: [redacted] abc123`, leaking the credential. Prefixed-secret
  (`OPENAI_API_KEY=`, `DB_PASSWORD=`), `tokenizer=`/`token_count=` and PEM behaviour unchanged.
  Over-redaction of `*_TOKEN` names (e.g. `MAX_TOKEN = 5`) is accepted and commented next to the
  regex; no test asserts it. Added `test_bearer_and_basic_credentials_redacted` (Bearer + Basic).
  section8 pre_review count: 11 → 12.
- **Item 2 (I-09 docstring):** rewrote the garbled `verification_evidence` paragraph
  (`section4/src/harness_orchestrator/review_summary.py:167`) to state, in order: the latest run
  of each command decides; green means ≥1 latest command ran and every latest command passed; a
  fix-then-green on the same command counts as green; `red_to_green` marks a command that failed
  earlier and passed later; unknown/refused is never green. Docstring only — no code change;
  section4 count unchanged.
- **Item 4 (stack_tags exit) — NOT changed:** `scripts/ops/stack_tags.py:10` docstring
  deliberately documents "this script always exits 0". Per the fix instruction's stop condition
  (the docstring documents the zero exit by design), the code and the `xfail(strict=True)`
  marker were left in place. The exit behaviour is recorded as an OPEN defect (§8), not resolved.

Raw results (command → output → exit code):

```text
$ cd section8 && .venv/bin/python -m pytest tests/unit/test_pre_review.py -q -rxXs
12 passed in 0.02s
EXIT_CODE=0

$ cd section4 && .venv/bin/python -m pytest tests/unit/test_review_summary.py tests/test_done_signal.py -q -rxXs
81 passed, 11 warnings in 1.09s
EXIT_CODE=0

$ cd scripts/ops && ../../section1/.venv/bin/python -m pytest test_stack_tags.py test_stack_tags_exit.py -q -rxXs
12 passed, 1 xfailed in 0.02s
XFAIL test_stack_tags_exit.py::test_main_should_exit_nonzero_when_docker_ps_fails
EXIT_CODE=0

$ python3 scripts/check_env_knobs.py
env knob catalogue complete: 63 runtime reads, all in .env.example
ENV_EXIT=0

$ python3 contracts/ts/generate.py --check
TS mirror up to date.
TS_EXIT=0

$ make guard-isolation
GUARD[isolation] OK: 283 src file(s) across 10 section(s) —
                 no cross-section tree/package/sys.path/star-reexport edges.
ISO_EXIT=0

$ git diff --stat -- contracts
(empty)
CONTRACTS_DIFF_EXIT=0
```

Still pending (not run here): full `make gate`/lint, real-PG probes, e2e-compose, the live
`HARNESS_VERIFICATION_FIRST` HTTP seam on a scratch team, and a real-model I-10 pass — these
remain with the test agent/owner. No commits, pushes, provider calls or signatures were made.