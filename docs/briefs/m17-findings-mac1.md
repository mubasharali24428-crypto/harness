# M17 Phase A — MAC1 findings (DX/ecosystem lens)

> Seat: MAC1 (Survivor-tier, math) · Repo `/Users/user/untitled folder 2/harness`
> @ HEAD `3c794b2` · Charter: ALEX_FORCE_CHARTER.md · Graph: mission17-elite-audit-graph.md
> Method: every claim below was executed against the LIVE compose stack
> (10 containers, all healthy at probe time) or traced to file:line. No fixes proposed.
>
> Commander's intent restated (≤5 lines): audit whether a stranger could adopt
> this harness in an afternoon and extend it in a week. Constraints: evidence
> only, severity-tagged, no fixes, disk delivery.

---

## Verdict in one paragraph

The operator-facing story is far stronger than expected — `.env.example`,
ONBOARDING.md, api-versioning.md, and the glossary are genuinely good, and I
drove login → project → plan → approve → run → events **purely through the
REST API in under 15 minutes**. The stranger still cannot adopt this in an
afternoon, because (a) their first agent run always ends in a dry-mode dead
end the Quick start never warns about, and (b) they cannot extend or script
anything: there is no client SDK of any kind, no plugin/extension surface,
and the documented contributor gate (`make test-section1`) fails on a fresh
clone because nothing creates the eight per-section venvs. The API contract
story ("every error is the frozen envelope") is contradicted by the live
stack on 4 of 7 backends — measured, not guessed.

---

## Findings

### [CRITICAL] F-1 · Contributor path is unbootable from a fresh clone

The README tells contributors that PRs must pass `make gate` /
`make test-section1`. Those targets hardcode `./.venv/bin/python` per section
(`Makefile:92` TEST_RECIPE, `Makefile:61` LINT_RECIPE). Nothing in the repo
creates those venvs: the Makefile header states provisioning "stays in CI as
setup steps" (`Makefile:7`), there is no `setup`/`bootstrap` target, and the
root README never mentions venv creation. The recipe exists only buried in
`docs/ci-gates.md:101-102` (`pip install -q -e ../contracts -e ".[dev]"`)
and in two section-level READMEs (section8:55, section10:38) — for 2 of 8
sections. A stranger who clones and runs the FIRST documented command
(`make test-section1`, README:49) gets `./.venv/bin/python: No such file or
directory` immediately, and must reverse-engineer 8 venvs × editable
contract installs to proceed. This severs the "extend it in a week" half of
the intent at step zero.
Evidence: `Makefile:7,61,88-92`; `README.md:43-54`; `docs/ci-gates.md:101-102,128,144`.

### [HIGH] F-2 · Frozen error-envelope contract is false on most of the live stack

`docs/api-versioning.md:115-118` claims coverage of `RequestValidationError`,
Starlette HTTPExceptions, and 500s "from every section". Reality, probed live:

| Probe | Service | Response |
|---|---|---|
| `POST :8004 …/{id}/plan/approve` `{}` | orchestrator | bare `{"detail":[{"type":"missing",…}]}` (FastAPI 422 array) |
| `POST :8005 /api/v1/sandbox/tasks` `{}` | sandbox | same bare array |
| `POST :8006 /api/v1/meetings` `{}` | meeting | same bare array |
| `POST :8001 /api/v1/auth/verify-otp` `{}` | identity | **also** bare array |
| `GET :8001/api/v1/me` (no token) | identity | `{"detail":"missing bearer token"}` |

Root causes, two independent ones:
1. `deploy/runners/services.py::simple_app()`:83-106 boots S3/S4/S5 and
   installs CORS + security headers but never `install_error_envelope`. The
   middleware copies exist per-section (S1/S3/S4/S6 each carry
   `envelope_middleware.py`) yet exactly ONE production call site exists:
   `section1/src/harness_identity/app.py:143` (grep-verified).
2. Even that one wired service answers bare: the deployed identity image was
   built 02:45 Aug 23; the wiring landed in `d2922ff` at 21:28 Aug 23
   (`git log -S`). Compose never rebuilt. An operator following the README
   today ships the pre-fix behavior and cannot know it.

A third-party client coded against the documented envelope breaks on first
validation error against 4+ services. Operational trap included: source and
running containers disagree, invisibly.
Evidence: probes above; `deploy/runners/services.py:83-106,141,206,302`;
`grep -rn "install_error_envelope(" --include="*.py"` → 1 prod site;
image timestamps `docker images`.

### [HIGH] F-3 · No client SDK in any language; both existing clients are trapped inside the frontend

- TS: `contracts/ts/types.d.ts` is 59 lines of **types only** (generated,
  `--check` gated — good). No functions, no npm package, not published.
- Python: `harness-contracts` is a path dependency (`pyproject.toml` in every
  section); never published to PyPI. No client library at all.
- Working client logic exists but is private to section7: `lib/api.ts`
  (567 LOC: bases, ApiError, envelope parsing, 401 session-clear) and
  `lib/useEventStream.ts` (347 LOC: ws-ticket mint, 1013 replay-on-gap, seq
  dedup). A scripter must reimplement ticket/replay/dedup from scratch.
- Confusion hazard: the ONLY pip-installable artifact named like this platform
  is `deepseek-harness-sdk` (PyPI, pinned 0.1.1rc1) — which is the *external
  upstream* product, NOT this API. The glossary polices the words but the
  package ecosystem actively misleads a searching stranger.

Verified workaround: raw REST works end-to-end (see F-8 positive) — but that
is discovery by archaeology, not an adoption path.
Evidence: `contracts/ts/types.d.ts`; `section7/src/lib/api.ts`;
`section7/src/lib/useEventStream.ts`; skill/pitfall note "deepseek-harness-sdk
pinned 0.1.1rc1".

### [HIGH] F-4 · The Quick start guarantees every newcomer's first run fails

README "Quick start" (lines 25-32) ends at `open http://localhost:3000`. It
never mentions `ENGINE_LIVE_MODE` / `DEEPSEEK_API_KEY` / `DSH_MODEL`. The
shipped `.env.example` defaults `ENGINE_LIVE_MODE=dry` (:125), whose documented
semantics are "explicit not-configured response, never faked success"
(`engine.py:1-21`). Measured: propose→approve→start on a fresh project ends
`state:"blocked"` + `engine_failed {reason:"DeepSeek Harness not configured:
set DEEPSEEK_API_KEY…"}` (honest, and the message names the cure — good — but
the newcomer has already completed the entire happy path to a dead end). The
one thing an evaluator wants to SEE in five minutes — an agent doing work —
requires provider setup the front door never advertises.
Evidence: `README.md:25-41`; `.env.example:119-134`; `section4/.../engine.py`;
live run `a84505a7-caec-4040-ba32-6c68933d6a92` (probe project `m17-dx-probe`).

### [HIGH] F-5 · Zero learning-oriented docs; example gallery is empty

- No tutorial, no getting-started narrative, no "your first run" walkthrough.
  `grep -rli "tutorial|getting.started|quickstart" docs/ *.md` → only
  EXPANSION_PLAN roadmap mentions. docs/ contains reference material only
  (glossary, api-versioning, ci-gates, pool-math, runbooks/, audits/,
  briefs/ — the last two are insider archives).
- Copy-paste value in 5 minutes: nothing. Closest artifact `task0/hello.py`
  exercises the EXTERNAL DeepSeek Harness SDK, not this platform. No examples/
  dir, no curl cookbook, no Postman/Insomnia collection, no generated snippets.
  (The QA audit `docs/audits/2026-08-23-testing-qa-brutal-audit.md:94-98`
  already flagged the related fixture-from-OpenAPI gap.)
- The in-app `/onboarding` checklist (section7/app/onboarding/page.tsx, derived
  from real cross-section signals) is the one bright spot — it is UI-only and
  stops at "steps exist", teaching no concepts.
Evidence: `ls docs/`; `task0/hello.py`; `section7/app/onboarding/page.tsx:1-40`.

### [HIGH] F-6 · Extension surface: three hard ways, zero documented ways

How a third party would today add each requested extension — all paths
reverse-engineered, none documented anywhere:

1. **Custom execution engine**: `DeepSeekHarnessWrapper.__init__` accepts a
   `backend_runner` callable ("tests / custom runtimes", `engine.py:38-48`),
   but injection happens only via the constructor inside section4's service
   assembly; there is no registry, entry-point, plugin dir, or factory hook.
   Today = fork section4 or patch `deploy/runners/services.py:173-206`.
2. **Custom reviewer heuristic**: `ReviewerSubAgent.evaluate_changes`
   (`section4/.../reviewer.py:40-52`) hardcodes three string-match heuristics
   (`<<<<<<< HEAD`, `SyntaxError`, test-summary). No strategy interface. A
   partial seam exists — per-team "reviewer thresholds" settings CRUD
   (M15-G6b, `section1/.../settings_api.py`) — but nothing maps those
   settings into reviewer.py's pass/fail logic, and no doc mentions either.
3. **New event type**: lawful path is frozen-contract surgery — edit
   `EventType` (`contracts/.../events.py`), 4-way version bump
   (pyproject/__init__/TS header/dist metadata, enforced by
   `test_version_pin_conformance.py`), regenerate TS mirror, update consumer
   mocks, same commit. Coherent but strictly maintainer-gated; an outsider
   cannot ship one.
4. **Undiscovered gem**: project rules (M12-G10, `.cursorrules` equivalent,
   `rules_loader.py` + `rules_api.py`) ARE a real user-facing extension point
   with a sane advisory-prompt contract and hard-boundary doctrine — and it is
   documented nowhere outside docstrings and tests. The best extension story
   in the repo is its best-kept secret.
Evidence: paths inline; `grep -rn "backend_runner" section4/src` (no registry);
`AGENTS.md` contract law; `section4/.../rules_loader.py:1-30`.

### [MEDIUM] F-7 · README/AGENTS carry a stale, broken schema command

Both root docs instruct `psql -q harness_fresh -f migrations/0001_initial.sql`
(README:62, AGENTS.md:27). No `harness_fresh` database exists in the compose
path; the real flow is the `migrate` one-shot container (`.env.example:244-249`,
skill pitfalls). A dev who trusts the README creates a stray local DB and
applies the frozen baseline outside the tracked migration ledger.
Evidence: `README.md:58-62`; `AGENTS.md:25-27`; `.env.example:240-249`.

### [MEDIUM] F-8 · Wire shapes discoverable only by tripping over them

`POST /runs/{id}/plan/approve` requires `planId` (measured: `{"decision":…}`
→ 422 bare array; correct body → 200). Propose returns `{"runId","planId"}`
without stating the approval contract; no prose doc records it. Same class:
runs-list pagination is documented beautifully in api-versioning.md, but
meeting-message paging (`?limit=&before=`), the ws-ticket dance, and
`events/replay?afterSeq=` are discoverable only via OpenAPI or source.
OpenAPI itself is reachable on ALL 7 backends (verified 200s) with measured
surface 19/14/8/13/5/10/7 paths — but typing is islanded (top-39 routes,
STATUS.md:520); generated clients get loose dicts elsewhere.
Evidence: probe transcript above; `section4/.../api.py:469-504,324-326`.

### [MEDIUM] F-9 · Glossary: excellent format, thin coverage for day-one nouns

9 entries, each code-anchored (best-practice pattern). Missing nouns a
stranger meets in hour one: **project**, **sandbox** (allow-list semantics),
**dry vs live mode**, **agent seat**, **KeyVault**, **Review Area gate
decision flow**, **budget/metering**. README's terminology table covers
naming law but not definitions.
Evidence: `docs/GLOSSARY.md` (9 rows counted); `README.md:64-81`.

### [MEDIUM] F-10 · Adoption blockers at the repo root

No LICENSE, no CONTRIBUTING.md, no CODE_OF_CONDUCT, no SECURITY.md
(`ls | grep -iE "license|contributing|code_of|security"` → empty). Legally a
stranger cannot reuse anything; procedurally there is no "how to propose a
change" despite rich internal doctrine (docs/doctrine/, docs/verifiers/).
Root also carries insider artifacts that read as noise to outsiders:
CLIENTELA_SUGGESTIONS.MD, 35KB STATUS.md, empty debug_canon/.
Evidence: `ls` transcript; root listing.

### [MEDIUM] F-11 · GitHub connect is hard-walled behind operator OAuth pre-setup

By design and honestly signaled (503 NOT_CONFIGURED, ONBOARDING.md §GitHub
OAuth, 6 documented steps incl. exact callback URI). Counted as friction, not
a defect — but it IS a mandatory external detour (register an OAuth app)
before any real-repo value; nothing in-repo can remove it.
Evidence: `deploy/ONBOARDING.md:55-79`; `.env.example:100-116`.

---

## Positives worth keeping (so Phase B doesn't "fix" them)

- `.env.example` (254 lines): every var named/purposed/defaulted/required-flagged,
  with a verified-date stamp. Best-in-repo doc.
- `docs/api-versioning.md`: real deprecation policy WITH a worked migration
  example and a 4-way version-pin conformance test. Rare and good.
- Full REST onboarding works: `auth/request-login` → OTP via
  `docker compose logs identity | grep HARNESS-OTP` → `auth/verify-otp`
  returned token + auto-created solo team (role admin) in three curls.
- Dry-mode honesty: failed run names the exact missing env vars in its
  `engine_failed` payload; no fake success anywhere in the walked path.
- `make static-gates` ran green locally at HEAD (entrypoint parse, compose
  config, TS-mirror `--check` all passed) — the parity contract holds.
- ws-ticket flow verified: single-use 30s project-bound ticket, minted cleanly.
- Onboarding checklist UI derives completion from real signals, deep-links steps.

## Probe residue disclosure

Live-stack writes made during this audit (dev stack, harmless):
team/user `stranger@example.com` (OTP verified), project `m17-dx-probe`
(id `435efdef-…`), run `a84505a7-…` left in state `blocked`. Note: S1 offers
no DELETE /projects/{id} — cleanup via API impossible (itself a small DX gap).

---

## "Stranger's first afternoon" — stuck-point timeline

| T+ | Step | Outcome |
|---|---|---|
| 0:00 | `git clone` | OK — but no LICENSE/CONTRIBUTING → first doubt (F-10) |
| 0:05 | `cp .env.example .env`, master key | OK, superbly guided |
| 0:10 | `docker compose up -d` | OK after multi-minute image build (Docker prereq implied, never stated) |
| 0:25 | localhost:3000 → email login | 403 OWNERSHIP_REQUIRED → hunts README footnote → finds OTP-in-logs trick (findable, mildly humbling) |
| 0:35 | Dashboard + onboarding checklist | OK — checklist deep-links real steps |
| 0:45 | Creates project, starts first run | **STUCK #1** — run dies `blocked`, engine_failed; Quick start never said providers need configuring (F-4) |
| 1:00 | Reads .env.example top-to-bottom, finds ENGINE_LIVE_MODE trio; no provider key → afternoon's demo value = zero OR burns 20 min standing up Ollama |
| 1:15 | Has key/Ollama → rerun | **WORKS** — first green run; genuine delight moment |
| 1:25 | Wants to script flows in Python | **STUCK #2** — no SDK/PyPI package; starts reading section7/lib/api.ts source to learn auth+tickets+replay (F-3) |
| 1:45 | Script hits 422 from orchestrator | **STUCK #3** — bare FastAPI array, not the documented envelope; assumes their client is broken; it's the server (F-2) |
| 2:00 | Wants a custom reviewer rule | **STUCK #4** — no plugin docs; greps into reviewer.py, realizes fork-required; discovers project-rules seam only by luck (F-6) |
| 2:20 | Decides to contribute: `make test-section1` | **STUCK #5 (CRITICAL)** — no venvs; recipe hidden in ci-gates.md; hand-builds 8 venvs (F-1) |
| 3:00+ | Runs README's psql baseline command | **STUCK #6** — `harness_fresh` doesn't exist; stray DB created (F-7) |
| Day 2+ | Verdict | Operator journey: adoptable with grit. Extender journey: blocked at every seam until they've read ~5K lines. |

Net: time-to-login ≈ 35 min (good), time-to-first-value ≈ 60–80 min and
provider-key-gated (poor), time-to-first-extension ≈ days (poor).

— MAC1, Survivor seat (math), M17 Phase A · evidence captured live @ 3c794b2
