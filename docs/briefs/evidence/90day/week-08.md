# Week 08 — P-15 GitHub verdict bot MVP (I-02): engineering delivery and independent acceptance

Opened 2026-11-13. Base checkout: `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5`,
branch `w28-s1-delegate`, with uncommitted changes. **Not a clean CI-certified release.**
Engineering only, offline, zero spend. **No real PR was commented on, no GitHub call was
made, no paid run happened** — no provider call, no network, no Docker (daemon down). The
builder and the posting path were exercised only through `httpx.MockTransport` and a scratch
SQLite DB. P-14 (mid-pilot go/no-go, written pay-intent) is owner-only and was not touched.

Plan row: `docs/plans/90-day-execution-plan-2026-11-11.md` P-15 (W8, S+B): "I-02 GitHub verdict
bot MVP: plan-approved-by, sandbox boundary, tests, cost, risk flags on every agent PR.
Acceptance: bot comment on a real partner PR; negative test: no secrets in comment."

## 1. Scope and states

| Work | Role | State | Evidence / limitation |
|---|---|---|---|
| Comment builder (pure, section4) | S+B | **Implemented — verified offline** | §3.1; 11 builder tests pass; no network/DB |
| Secret-pattern scrubber (section4 redact) | S+B | **Implemented — verified offline** | §3.1; additive `scrub_secret_patterns`; builder tests |
| Posting client + service + route (section2) | S+B | **Implemented — verified offline** | §3.2; 8 posting tests + full section2 suite 56 pass (MockTransport) |
| Orchestrator fact-assembly + trigger wiring | S+B | **Not started — integration** | Builder + route are ready; the glue that reads recorded facts, builds the body and POSTs to the route is not wired here (needs the live stack/DB) |
| Comment on a real partner PR | R / owner | **Not started — owner-gated** | Needs partner consent, the team admin's GitHub write opt-in, a token; acceptance is a real PR comment |
| Marketplace/App distribution; "verifies/approves" claim | R / owner | **Not started — owner-gated** | The bot only comments; it never verifies/approves/merges |
| P-14 pay-intent / go-no-go | R / owner | **Not started — owner-gated** | Out of scope for P-15 engineering |

## 2. Audit table — where each recorded fact lives (read-only)

Unknown stays **unknown** — never defaulted to a pass, `$0`, "safe" or "approved".

| Comment field | Recorded fact (file:line) | Read path for a run | Unknown / not recorded today |
|---|---|---|---|
| **Plan approved by** | `agent_events` type=`plan_approved`, payload `approvedBy`(userId)/`approvedAt` + `ts` (`state_machine.py:200-216`); audit `plan.approved` → `audit_log.detail.userId` (`plans.py:1451-1463,:162`). `agent_plans.status`+`version` only (`0001:167-182`, 0013); `plan_versions.approved_sha256/approved_at` (`0047_plans.sql:54-55`) | latest `plan_approved` event for run (`service.py:2296-2301`); rehydrate `898-916` | **Approver display name** (only userId persisted; display read live `plans.py:608-617`). Decision time on `agent_plans`. `_sync_legacy` stamps `approved_at`=mirror time, not decision time (`plans.py:456-457,472-480`). `high_impact_approvals.decided_by/at` (`0035:73-74`) is for high-impact actions, not plans |
| **Sandbox boundary** | mode resolved at spawn: `confine.py:81-98` `resolve_mode` (env `HARNESS_ENGINE_CONFINEMENT:35`; container/seatbelt/none), `engine.py:787-792` | live `workspace_identity.isolation = resolve_mode()` at read time (`engine.py:658-672`, `service.py:3240-3248`) — current host config | **NOT recorded per run**: `agent_runs` has no confinement/backend column (`0001:142-153`, `0023:351-365`); no event carries it (`EXECUTION_STARTED` payload only contextWindow, `state_machine.py:257-262`). Only durable: `team_runners.confinement` (`0068:32,43`) = runner attestation, not per-run. → comment says **"not recorded"** |
| **Tests** | `review_summary.py:204-292` `tests={known,runs:[{command,result}]}`; result passed/failed/timeout/refused/**unknown** (`_test_runs:147-164`, unknown:162 — never defaults pass). Codes `TOOL_CALLS_NOT_SURFACED:231`, `NO_TESTS_RUN:257`, `TEST_RESULT_UNKNOWN:260`, `EDITED_AFTER_LAST_TEST:264`; `verification_evidence:167-201` | `agent_tool_calls` (`0044_agents.sql:95-113`) `reported_by='engine'`; writer `run_metering.py:236-287`; GET review-summary `artifacts.py:1294-1326` | Pass/fail counts and failure reasons (note `:238-240`). No exit-code column — exit rides in summary (`call_summary:75-81`, parsed `84-89`) |
| **Cost** | **ACTUAL** `cost_ledger.cost_usd` NUMERIC(12,6) (`0001:184-196`); writer `run_metering.py:143-233`; per-run SUM `service.py:3152-3168`; `_cost_wire:3226-3238` None→None. **ESTIMATE** `agent_plans.estimated_cost_usd` (`0001:175`) → `estimatedCostUsd` (`api.py:535`, `service.py:3362`) | per-run ledger SUM; unknown=None never 0 (`review_summary.py:268-273,288`, `artifacts.py:1327-1332`) | Favourable estimate defaults exist (`planner.py:109`=0.05, `plans.py:247`=0.0, `service.py:965` `or 0`) — the comment labels estimate vs actual and shows **unknown** when actual is None. Ledger has no estimated-vs-measured flag |
| **Risk flags** | section8 `models.py:28-39` `RiskFlag{path,severity,rule,reason}`; `PreReviewDigest:92-109`; rules `risk_analyzer.py:22-98`, `analyze:112-137`, `get_risk_summary:159-169`; digest advisory `pre_review.py:78-146` | recomputed per request (`service.py:402-414,429-465`); **not durably recorded** (store writes only pull_requests/review_comments) | section4 has no risk types in contracts and no shared table/event → **flags must be passed in by the caller as data** (isolation law: section4 must not import section8). Digest rendered only if recorded, labelled advisory |

**PR → run linkage:** `pr_open.py:55-60` `run_id_from_branch` (`agent/<runId>`); `link_run_to_pr:63-104`
inserts `pull_requests(run_id, github_pr_number)` (`:35-41`) + `agent_runs.pr_id` (`:33`). Tables:
`pull_requests` (`0001:198-212`); `agent_runs.pr_id` added by `0017_pr_id.sql:10-14`. The PR-open
route carries no runId field — the branch name is the binding signal; a non-`agent/` branch is an
unbound (human) PR open.

## 3. What was built (reuse first; additive only)

### 3.1 Comment builder — `section4/src/harness_orchestrator/verdict_comment.py` (pure)
- `build_verdict_comment(facts, *, pr_number, secrets=())` renders Markdown sections: Plan approved
  by, Sandbox boundary, Tests, Cost, Risk flags (+ advisory digest only if recorded), and the fixed
  footer **"Advisory summary of recorded facts. Not an approval to merge. A human merges."**
- Hidden marker `<!-- harness-verdict:v1 run=<id> -->` (`verdict_marker`) so the bot updates one
  comment in place. Hard cap `MAX_COMMENT_CHARS = 60000` (< GitHub's 65536) with a "truncated" note;
  per-field length clamps; the footer and marker survive truncation.
- Unknown stays unknown: tests unknown → "unknown"/"not run (NO_TESTS_RUN)", never "passed"; cost
  actual None → "unknown", never `$0`; sandbox unrecorded → "not recorded"; plan unrecorded → "not
  recorded". Wording never says safe/approved/passed-review unless that exact fact was recorded.
- No raw diffs/tool output/logs/prompts/env/file contents: the builder reads only the structured fact
  keys and ignores anything else (a `diff`/`log`/`file_contents` key in the input is not rendered).
- Every free-text value passes `redact.scrub_secrets` (known literals) **and** the new additive
  `redact.scrub_secret_patterns` (PEM blocks, KEY=value/KEY: value incl. Bearer/Basic scheme, bare
  Bearer/Basic credentials, sk- keys). section4-local — no section8 import.
- Lives in section4 where the facts are read; risk flags/digest are passed in as data.

### 3.2 Posting path — section2 (behind the existing write opt-in)
- `client.py`: `comment_on_pr` (POST `/repos/{repo}/issues/{number}/comments`), `update_comment`
  (PATCH `/repos/{repo}/issues/comments/{id}`), `list_pr_comments` (GET). **`_guard` unchanged** —
  these are POST/PATCH/GET on `/issues/...`, so the merge/force/branch-delete guard still blocks
  (`MergeGuardViolation`); a test pins both that the guard still raises and that comment endpoints pass.
- `service.py`: `postVerdictComment(...)` gated by `_require_writes` (team-admin opt-in per project),
  tenant-scoped via `get_repo_full_name` (wrong team → `LookupError`). Idempotent through the existing
  `github_write_keys` (reserve/read/store/release, operation `verdict_comment`): first call creates
  (or updates an existing comment found by the server-derived marker, see section 8); later calls with the same key update that same
  comment. The body is supplied by the caller; **section2 does not import section4**.
- `app.py`: route `POST /api/v1/projects/{project_id}/pulls/{pr_number}/verdict-comment` with a
  locally-defined `VerdictCommentBody{runId, bodyMd, marker}`. `GitHubWritesDisabledError`→403,
  `LookupError`→404 (also: run not linked to that PR), `WriteInProgressError`→409, `MergeGuardViolation`/`ValueError` (marker mismatch, body without the marker)→422.
- **Trigger decision: explicit route.** The body is built from section4 recorded facts; section2
  cannot import section4 and the frozen `GitHubService` Protocol (`contracts/signatures.py:73-104`)
  lacks the new method, so the orchestrator supplies the body over HTTP. The existing `pr_opened`
  event is the signal that prompts the orchestrator; the posting capability is the route.
- **No contract change** (additive service method + local route body; `pr_opened` already exists).
  **No new migration** (reuses `github_write_keys`, `0058`). The frozen `GitHubService` Protocol is
  not `@runtime_checkable`; an extra implementation method does not modify `contracts/`.

## 4. What was run — raw commands, output, exit codes

Host: darwin arm64; Python 3.13 (section venvs); source `7c1acf4` working tree (uncommitted).
Zero spend; no Docker; no network; no real GitHub call.

```text
$ python3 -m py_compile <8 new/changed .py: redact, verdict_comment, test_verdict_comment (s4);
                          client, service, app, conftest, test_verdict_comment (s2)>
PYC_EXIT=0

$ cd section4 && .venv/bin/python -m pytest tests/unit/test_verdict_comment.py -q -rxXs
11 passed in 0.21s
S4_VERDICT_EXIT=0

$ cd section2 && .venv/bin/python -m pytest tests/test_verdict_comment.py -q -rxXs
8 passed in 0.18s
S2_VERDICT_EXIT=0

$ cd section2 && .venv/bin/python -m pytest -q          # full suite, conftest regression check
56 passed, 2 warnings in 1.32s

$ cd section4 && .venv/bin/python -m pytest tests/unit/test_spec_drift.py -q
5 passed, 40 warnings in 7.33s

$ cd section4 && .venv/bin/python -m pytest tests/unit/test_review_summary.py tests/test_done_signal.py -q
81 passed, 11 warnings in 1.09s

$ cd section8 && .venv/bin/python -m pytest tests/unit/test_pre_review.py -q
12 passed in 0.01s

$ cd scripts/eval && ../../section1/.venv/bin/python -m pytest test_w7_bench.py -q
8 passed in 0.19s

$ python3 scripts/check_env_knobs.py
env knob catalogue complete: 63 runtime reads, all in .env.example      ENV_EXIT=0
$ python3 contracts/ts/generate.py --check
TS mirror up to date.                                                   TS_EXIT=0
$ make guard-isolation
GUARD[isolation] OK: 284 src file(s) across 10 section(s)               ISO_EXIT=0
$ git diff --stat -- contracts
(empty)                                                                 CONTRACTS_EXIT=0
$ git diff -- docs/TUTORIAL.md | shasum -a 1 | cut -c1-8
4b6a900c
```

**Spec drift (new github route):** `section4/.venv/bin/python scripts/spec_drift.py --write`
(WRITE_EXIT=0) → `git show HEAD:section5/openapi.json > section5/openapi.json` (RESTORE_EXIT=0) →
`python3 scripts/sdk-gen/generate.py` (GEN_PY_EXIT=0) and `--target ts` (GEN_TS_EXIT=0) →
`spec_drift.py` re-check "orchestrator specs and UI calls match the router." (CHECK_EXIT=0).
github SDK now 22 operations (+1 verdict-comment). **Footprint kept to github only:** `--write`
also showed an orchestrator schema difference (an `estimatedCostUsd` field) that the build agent
attributed to the cost/metering lane. **Not independently confirmed:** HEAD's recorded orchestrator spec
already mentions `estimatedCostUsd` twice, so which schema lacked it is unverified. The four orchestrator files
(`section4/openapi.json`, `specs/orchestrator.openapi.json`, `clients/python/harness_sdk/orchestrator.py`,
`clients/ts/src/orchestrator.ts`) were restored to HEAD via `git show`. Net spec/SDK diff:
`github.openapi.json`, `github.py`, `github.ts` only. (The orchestrator
spec question is left for the owner/test agent, not fixed here.)

**section4 note:** the focused suite (`test_review_summary.py` + `test_done_signal.py`) passed 81.
The two Docker-dependent real-container tests elsewhere in section4 are outside this selection and
were NOT run (daemon down); no claim is made about them.

## 5. Invariants honored

- **Never merge / never auto-approve.** `_guard` is byte-for-byte unchanged; the bot only comments;
  the footer states a human merges. Tests pin merge/force/branch-delete still raise.
- **No secrets in the comment.** Known literals (`scrub_secrets`) + secret shapes
  (`scrub_secret_patterns`); negative tests prove sk-/OPENAI_API_KEY=/DB_PASSWORD=/Bearer/PEM never
  appear; no raw diff/log/contents rendered even if present in the input.
- **Unknown stays unknown** — never a pass, `$0`, "safe" or "approved" without the recorded fact.
- **Isolation law.** section4 imports no section8 (risk flags passed in as data); section2 imports no
  section4 (body supplied by caller). `make guard-isolation` green (284 files / 10 sections).
- **Frozen contracts untouched** (`git diff --stat -- contracts` empty); TS mirror up to date; the
  additive service method/route needed no contract event/field. No new migration.
- **Write opt-in + tenant scope** preserved (`_require_writes`, team-scoped project lookup).
- **Other lanes untouched:** `docs/TUTORIAL.md` (pre-existing diff, SHA-1 of its git diff `4b6a900c…`
  unchanged), `week-04.md`, contracts/, docker, and the orchestrator spec/clients restored to HEAD.
  No commit, push, `git add -A`, reset, checkout or stash.

## 6. Test-agent handoff — execute and attach raw results

Offline (no Docker/network):

```sh
cd section4 && .venv/bin/python -m pytest tests/unit/test_verdict_comment.py tests/unit/test_spec_drift.py -q
cd section2 && .venv/bin/python -m pytest tests/test_verdict_comment.py -q   # then the full suite
python3 scripts/spec_drift.py        # must stay green; github spec carries the verdict-comment route
```

Then the **owner-gated live** acceptance (partner consent, team-admin GitHub write opt-in, a token;
never customer data without consent): assemble the recorded facts for a run (plan_approved event,
review-summary, cost_ledger SUM, risk flags passed as data), build the body with
`verdict_comment.build_verdict_comment`, and POST it to
`/api/v1/projects/{pid}/pulls/{n}/verdict-comment`. Verify: one comment per PR (create then update in
place by marker), the body equals the builder output, no secret in the comment, writes-off refused,
wrong team refused, and merge still impossible. Record command, exit code, raw output, run/PR ids,
host, source hash. Do not weaken assertions. The bot must never be described as "verifying" or
"approving" code.

## 7. Owner gates that engineering cannot complete

- Commenting on a real partner PR (consent + write opt-in + token) — the P-15 acceptance.
- GitHub Marketplace/App distribution.
- Any claim the bot "verifies" or "approves" code (it only comments).
- P-14 mid-pilot go/no-go and written pay-intent.
- Independent test/security acceptance and release go/no-go at the tested revision.

**No real PR was commented on, no GitHub call was made, no paid run happened.** No commits, pushes,
provider calls, Docker operations, partner messages, customer-data uploads or signatures were made.
The builder and posting path are verified offline; the orchestrator wiring and the live PR comment
are not done (integration / owner-gated).

## 8. Fix pass (2026-11-13) — gaps found by independent verification

An independent re-run reproduced these gaps; all were fixed offline (no GitHub call, no paid run).

| Gap (reproduced before the fix) | Fix | Test |
|---|---|---|
| Bare `ghp_`/`xoxb-`/`AKIA`/`AIza`/JWT tokens survived in free text; `password = "a b c"` left `b c` visible | `redact.scrub_secret_patterns`: bare-token regex, quoted-value alternative | `test_bare_tokens_and_quoted_secrets_masked` |
| `run_id` went unsanitized into the hidden marker (`x --> <img …> <!--`) | `safe_run_id` (`[A-Za-z0-9_.-]`, else `_`); marker built from it | `test_run_id_cannot_break_the_marker` |
| Goal/path/reason text could `@mention`, cross-reference `#12`, embed images/links/HTML, break a code span, auto-link | `_neutralize` for prose; backtick swap for code spans; scrubber placeholders kept readable | `test_free_text_is_inert` |
| Non-numeric cost raised `ValueError` | `_usd`: finite, non-negative, else "unknown" | `test_bad_cost_is_unknown_not_a_crash` |
| Caller-supplied marker `"LGTM"` made the bot overwrite another comment (mock) | marker derived server-side from the run id; a different caller marker or a body without it → 422 | `test_foreign_marker_or_markerless_body_refused_and_human_comment_untouched` |
| Any PR number, run id and body was accepted | run must be linked to that PR in that project (`pull_requests`), else 404 and no GitHub call | `test_unlinked_pr_or_run_refused_no_github_call` |
| Only the first 100 comments were searched | `list_pr_comments` pages up to 10 × 100 | `test_marker_search_pages_past_100_comments` |
| A comment deleted on GitHub made the update 404 unhandled | 404 on update → create a new comment | `test_comment_deleted_on_github_is_recreated` |
| Section2 and section4 marker formats could drift apart | format pinned by a test on each side | `test_marker_format_is_pinned_…` (section2) + section4 marker tests |

**Authorship (not previously stated):** the comment is posted with the connected user's GitHub token, so it
appears under that person's account, not a bot identity. A distinct bot/App identity is owner-gated
(Marketplace/App distribution, section 7).

**Known limits left:** secret scrubbing is pattern-based (unknown token shapes can pass); the section8
`_redact_secrets` helper was not changed here; the comment still lists a free-text goal (scrubbed, clamped,
neutralized); posting is an explicit route, the orchestrator-side trigger is still not wired.

Raw results after the fix: section4 `test_verdict_comment.py` 15 passed; section2 `test_verdict_comment.py`
13 passed, full section2 suite 61 passed; section4 focused + spec-drift 101 passed; full section4
2340 passed / 59 skipped / 2 failed (the same two Docker-daemon tests, `test_real_container_*`, daemon down);
section8 12 passed; `test_w7_bench.py` 8 passed; `spec_drift --check` ok; env knobs 63; TS mirror up to date;
`guard-isolation` OK (284 files); `contracts/` diff empty; TUTORIAL.md diff SHA-1 `4b6a900c`.
