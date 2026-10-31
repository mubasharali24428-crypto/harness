# W20 rerun evidence (2026-10-30)

Raw runner output for the Marathon 2.0 W20 rerun. The tables below were
generated from these JSON files, not typed by hand. This is the evidence for a
release decision; it is not the decision (see `docs/m2-w20-plan.md`).

## Revision under test

- Git HEAD `12fb2213152a1079369c2f120ed6cf16cb16df37` (branch `w1-engine-tools`).
  The worktree was dirty only in docs, eval scripts and the W17 agent's files;
  the runtime source dirs baked into the images (`contracts`, `section4`,
  `section5`, `deploy/patches`, `deploy/mcp`) were clean.
- `run_eval.py --verify-only`: `verified: true` for the orchestrator container,
  the worker image **and** the worker daemon (the daemon check was added during
  W20; before it, a stale daemon passed unnoticed).
- Images: worker `sha256:a5f587aeb1f0…`, daemon `sha256:00c0b7107bee…`, backend
  `sha256:5973222fefd5…`.
- Model: `qwen/qwen3.7-flash`, hosted by OpenRouter (`DEEPSEEK_BASE_URL`).
  Nothing ran on a local model; local Ollama holds only `nomic-embed-text`.

### How the stack was brought to that revision

1. **Worker image.** 12 baked-in files differed from HEAD and
   `harness_contracts/rate_card.py` was missing. Rebuilt as a thin overlay:
   the Dockerfile's own `pip install --no-deps` step re-run from `git archive HEAD`
   over the previous image (the Chromium apt layer sits after the source copy, so a
   clean build re-downloads ~50 minutes of packages). Rollback tag:
   `harness-engine-worker:pre-w20-overlay`.
2. **Worker daemon.** Its image was 21 hours old (24 files drifted, `engine.py`
   among them), and its `HARNESS_WORKER_DAEMON_TOKEN` no longer matched the
   orchestrator's: the daemon answered the orchestrator `401`, so **no agent run
   could dispatch**. The profile-gated daemon had been left behind when the backend
   was recreated. Fixed by an overlay image and recreating only `worker-daemon`
   from the root `.env`; a hashed before/after diff of the container config shows
   only the token and `DSH_MCP_SERVERS` changed. Rollback tag:
   `harness-worker-daemon:pre-w20-overlay`. Runbook: `docs/runbooks/ops.md` §3.11.
3. **Clean build check.** HEAD's own `worker.Dockerfile` was then built under
   separate tags (`harness-engine-worker:vclean-w20` `sha256:166d8306e962…`,
   `harness-worker-daemon:vclean-w20` `sha256:fe0533073f81…`). Both match HEAD on
   all 108 baked-in runtime files, as do the overlays, so the code under test
   equals a clean build. The stack was not switched to the clean images mid-run.

## Results

Columns are stages of the same day, newest first; every run is kept, failures
included, as the protocol requires. The stages after HEAD `12fb221` are
described under "Follow-up" below.

| task | held-out | cost class | Haiku 5.5 + loop-guard fix | qwen + loop-guard fix | qwen, maxTokens 16384 | qwen, maxTokens 4096 | pre-daemon-fix (history) |
|---|---|---|---|---|---|---|---|
| `ast-lsp-confinement` | no | infra | - | - | PASS 3.86s | PASS 3.99s | PASS 3.84s |
| `cli-diagnostics` | no | infra | - | - | PASS 2.62s | PASS 2.87s | PASS 2.15s |
| `code-search-heldout` | yes | live | PASS 16.78s | - | PASS 16.79s, PASS 24.8s, PASS 16.73s | PASS 20.9s, PASS 20.86s, PASS 16.83s | - |
| `lsp-real-server` | no | infra | - | - | PASS 4.75s | PASS 4.6s | PASS 4.63s |
| `multi-file-refactor` | no | live | - | - | PASS 85.24s, PASS 77.17s, PASS 77.19s | PASS 73.15s, FAIL 52.99s, FAIL 65.14s | - |
| `skills-governance` | no | infra | - | - | PASS 0.15s | PASS 0.16s | PASS 0.14s |
| `stack-compose-journey` | no | infra | - | - | PASS 5.12s | PASS 3.07s | PASS 3.04s, PASS 2.91s |
| `stale-edit` | yes | infra | PASS 0.8s | - | PASS 0.87s | PASS 0.87s | PASS 0.84s, PASS 0.9s, PASS 0.84s, PASS 1.08s |
| `tenant-isolation-api` | yes | infra | PASS 0.76s | - | PASS 0.75s | PASS 0.71s | FAIL 0.18s, FAIL 0.81s, PASS 1.11s, PASS 0.85s, PASS 0.86s |
| `tests-written` | no | live | - | - | PASS 33.05s, PASS 37.31s, PASS 41.19s | PASS 33.0s, PASS 45.01s, PASS 53.2s | - |
| `w10-browser-session` | no | infra | - | - | PASS 2.72s | PASS 2.82s | FAIL 2.76s |
| `w11-grounding` | no | live | - | - | PASS 260.63s, PASS 265.94s, PASS 228.6s | PASS 276.74s, PASS 259.56s, PASS 309.38s | FAIL 266.14s |
| `w11-vision` | yes | live | PASS 46.67s, PASS 50.25s, PASS 46.18s | FAIL 344.34s | FAIL 525.1s, PASS 351.76s, FAIL 520.32s | FAIL 122.95s, FAIL 98.74s, FAIL 2.28s | - |
| `w12-skills-memory` | no | infra | - | - | PASS 18.16s | PASS 18.65s | PASS 18.15s |
| `w13-byo-runner` | no | infra | - | - | PASS 2.71s | PASS 2.8s | PASS 2.65s |
| `w14-design-heldout` | yes | live | PASS 31.71s, PASS 31.55s, PASS 43.71s | PASS 83.96s | FAIL 361.24s, FAIL 59.18s, PASS 228.64s, FAIL 265.0s | FAIL 82.95s, FAIL 78.93s, FAIL 384.28s | - |
| `w4-egress-deny` | no | infra | - | - | PASS 1.46s | PASS 1.1s | PASS 1.31s |
| `w8-agent-quotas` | no | infra | - | - | PASS 1.42s | PASS 1.5s | PASS 1.41s |
| `w8-apply-conflict` | no | infra | - | - | PASS 0.42s | PASS 0.44s | PASS 0.46s |
| `w8-crash-cleanup` | yes | live | PASS 27.4s | - | PASS 27.27s, PASS 26.81s, PASS 26.76s | PASS 28.15s, PASS 27.1s, PASS 27.31s | - |
| `w8-quotas-isolation` | no | infra | - | - | PASS 2.81s | PASS 2.81s | PASS 2.76s |
| `w9-visuals` | no | infra | - | - | PASS 5.53s | PASS 5.88s | FAIL 0.12s |

Live tasks ran as **separate** runner invocations (`live-r*`, `fix-r1`,
`haiku-r*`, `haiku-heldout-*`): `--repeats` retries until the first pass and
cannot support a rate. The two deterministic held-out tasks (`stale-edit`,
`tenant-isolation-api`) make no model calls.

## Follow-up the same day: vision gate passes on `anthropic/claude-haiku-5.5`

After the 12fb221 rerun failed the vision gate, four changes were made, one at a
time, each measured before the next. **No pre-registered threshold was
changed** (pixel diff ≤ 0.03, colour checks, ratio tolerance 0.005).

1. **`maxTokens` 4096 → 16384 for `DSH_MODEL`** (`deploy/patches/openrouter-vision.patch.yml`).
   qwen is a reasoning model and reasoning counts against `maxTokens`; the
   "empty response" failures were turns ending `max-tokens` with no answer. After
   the change, qwen completed attempts (columns "qwen, maxTokens 16384") but
   still failed on layout and on the two defects below.
2. **Loop guard false positive** (`section4/src/harness_orchestrator/loop_guard.py`, product
   defect). The guard counted identical calls for the whole run, so a design run
   that took the same `browser` screenshot after each of four `edit`s was
   stopped as `loop_detected` (w14 qwen r6; worker log captured live). A
   `write`/`edit` now resets the counts of non-mutating calls; identical writes
   still count, and `maxToolCalls` still bounds the run. Three unit tests added.
3. **RATIO parser in both drills** (`scripts/e2e/w11_vision_drill.py`,
   `w14_design_heldout_drill.py`, verifier defect). It read the *first*
   `RATIO:` line; w14 qwen r4 narrated `RATIO: 0.01419 is below 0.03 threshold.`
   before a correct three-line block (claimed 0.01419, measured 0.014237) and
   failed. It now reads the last `RATIO:` line with a number. **This is a
   post-hoc verifier change**; the failed r4 is left as recorded, not re-scored.
   The Haiku runs answered in exactly three lines and do not depend on it.
4. **Model `qwen/qwen3.7-flash` → `anthropic/claude-haiku-5.5`** (OpenRouter,
   $0.10/M in, $0.50/M out; `DSH_MODEL` in the root `.env`, rate-card overlay in
   both env files). With fixes 1–3 deployed, qwen still failed `w11-vision`
   (fix-r1): a 26.6% pixel diff, and a final turn of ~55k characters of
   reasoning that wrote its answer 58 times *inside* the reasoning and ended
   `length` without emitting it. That is model capability; more tokens would not
   fix a 26.6% diff. Haiku passed a one-attempt image + tool smoke
   (`smoke-claude-haiku-5.5.json`), then **`w11-vision` 3/3** (diff 0.15%, 0.32%,
   0.21%) and **`w14-design-heldout` 3/3** (0.0%, 0.19%, 0.12%), in under a
   minute each. The rest of the held-out set was rerun once on Haiku: all pass.

Revision for the Haiku columns: HEAD `9e34f42` **plus the uncommitted changes
above** (`loop_guard.py` and the patch are baked into the images by single-file
overlays; rollback tags `*:pre-loopguard`, `*:pre-maxtokens`).
`run_eval.py --verify-only` printed `verified: true` against that worktree, and
`drift.py` found 0 of 108 baked-in files different. Images: worker
`sha256:09e3b10734fe…`, daemon `sha256:9e03e476f7a0…`, backend
`sha256:5d53a21dce7c…`. The orchestrator and daemon were recreated after the
model switch (daemon from the root `.env` only). Committing the changes makes
this revision reproducible from git.

Other runs in the qwen columns, for the record: w14 qwen r5 (59.18s) is
**void** — the Mac slept with the lid closed and the run ended
`heartbeat_lost`; it was replaced by `live-r5b` (PASS). w11 qwen r4 and r6
failed on layout (≈26% diff from nested absolute positioning).

### Why each failure happened

| Failure | Cause | Counts as |
|---|---|---|
| `w11-vision` r1, r2; `w14-design-heldout` r1, r2 | the hosted model returned an empty response mid-run; the engine stopped the run as `blocked` instead of reporting success | **model** failure on vision work; product guard correct |
| `w14-design-heldout` r3 | `heartbeat_lost` after a ~6-minute vision step; the orchestrator later logged "claim lost to another worker; fencing local engine" | possible lease/heartbeat weakness during long model calls — **W17 reliability** follow-up |
| `w11-vision` r3 | the drill's login hit a **Django dev page** on `127.0.0.1:8001`: another app briefly bound the identity port on this Mac | **invalid run** (outside interference), not a product or model result |
| `multi-file-refactor` r2, r3 | code refactor correct (hidden API check and shipped tests pass), but `README.md` still named `calc_price` | **agent** incompleteness, caught by the verifier as designed |
| history: `tenant-isolation-api` r1 | driver defect: assumed a `GET /projects/{id}` route that does not exist; fixed from the identity spec | driver |
| history: 3 tasks failing in under 1 s | `429 RATE_LIMITED` on the credential edge (about 5 logins/min/IP) from back-to-back drills; `--gap 65` added | batch pacing |
| history: `w11-grounding` | the drill imports `harness_orchestrator` into the host process; needs `PYTHONPATH` (now in `driver.env`) | runner environment |

### Spend (measured)

Cost ledger before the first live run: 152 rows, $0.053271; after round 3:
170 rows, $0.089903 (**$0.036632**). `w11-grounding` calls the provider directly
and reports its own usage ($0.002530 + $0.002089 + $0.002636). Total for 21
live runs: **about $0.044**, against an authorized cap of about $2.16.

Follow-up, ledger deltas: qwen rounds 4–6 plus the no-spend set $0.101876
(+ $0.007545 `w11-grounding` self-reported) ; `live-r5b` $0.010176; qwen
`fix-r1` $0.007600; Haiku vision 6 runs **$0.045568**; Haiku held-out $0.006733;
Haiku smoke ≈ $0.0021 (from its usage at the rate card; it calls the provider
directly). **Whole day ≈ $0.226** (ledger $0.053271 → $0.261856 plus direct
calls). A Haiku vision run costs about $0.0076, against qwen's $0.003–0.005.

### Local gates at the same revision

`make static-gates` and `make lint` passed. Per-section pytest: Section 1 1202
passed / 63 skipped; Section 2 48; Section 3 146; Section 4 1886 / 59 skipped;
Section 5 436 / 8 skipped; Section 6 123; Section 8 91; Section 10 91;
Section 11 56; MCP 18. Section 7: `tsc --noEmit`, `vitest run` and
`next build` passed. Skips are opt-in live-Postgres/Docker tests and are not
passes. The `agent_events` table has partitions through 2026-12-31, so the
2026-11-01 write cliff is covered.

## Owner-readiness refill (prepared 2026-10-30 evening, for the authorized owner)

An engineering session wrote this after W21 and W22 so the release decision is
easy and honest. **It is evidence, not a decision.** The decision record in
[`docs/m2-w20-plan.md`](../../../m2-w20-plan.md#decision-record) was not edited and
is still blank; nothing below ticks, signs or approves anything for an owner.
The checklist boxes in that plan were also left as they were, so some are now
stale (M01 is closed by W21 but still unticked there). One-page companion:
[`owner-brief.md`](owner-brief.md). Commands and raw results of this pass:
[`refill-20261030/verification-log.md`](refill-20261030/verification-log.md).

Status words. **PASS@rev** was measured in this pass. **PASS (older)** passed at
a different revision or model and was **not** re-measured; it is not a pass of
the current revision. **FAIL**, **NOT RUN**, **OWNER-GATE** (a person must
decide or act), **IN PROGRESS** (another session is changing it).

### Which revision is which

| Evidence | Revision | Model |
|---|---|---|
| W20 checklist as written (images, fixed set, journey) | `12fb221` | `qwen/qwen3.7-flash` |
| W20 vision gate (the pass) | `9e34f42` + uncommitted fixes, committed as `53924be` | `anthropic/claude-haiku-5.5` |
| W21 M01 drills | `7341d50`; native-tool-confinement rerun on `2adb551` | scripted stub, $0 |
| W22 drills | overlays `w22-v3`/`v5`, clean build `w22clean` (`2adb551`), `w22fix`, `w22fix2` (tree `e5bc4856` = `2adb551` + fix files; runtime source identical to `e9e5589`) | `anthropic/claude-haiku-5.5` |
| Running stack, read 2026-10-30 23:07 PDT | `harness-backend:vw22fix2`, `harness-worker-daemon:vw22fix2`; `DSH_MODEL` in the orchestrator is `anthropic/claude-haiku-5.5` | Haiku |
| Committed HEAD during this pass | `e9e5589` until 23:24:08 PDT, then `55ad4b1` (W13 commit) | none |
| Built, not running | `vw22fix3` images exist; their source edits are uncommitted | |

Seven runtime files changed between the gate commit `53924be` and `e9e5589`
(identity `app.py`; Section 4 `confine.py`, `container_runtime.py`, `engine.py`,
`service.py`, `worker.py`, `worker_daemon.py`; +178/−19 lines), and `55ad4b1`
changed more (`runner_api.py`, `run_queue.py`, `service.py`, `byo_runner.py`,
`policy_enforce.py`). `engine.py` is on every agent run's path, so the vision
gate's pass does not automatically carry to HEAD. `docs/m2-w20-plan.md` keeps
`9e34f42` as the gate revision.

### Release checklist refilled

Numbering follows the plan's checklist. "Evidence" paths are relative to this
directory.

| # | Item (as written in the plan) | Status | Measured at | Evidence and note |
|---|---|---|---|---|
| 1 | Images verified against HEAD by hash, daemon included; clean build matches | **FAIL now** (PASS older) | older: `12fb221`. Now: `e9e5589`, 23:07 PDT | `run_eval.py --verify-only` printed `verified: false` ([`refill-20261030/verify-only-e9e5589.json`](refill-20261030/verify-only-e9e5589.json)). The only mismatch is `engine.py`: the worktree copy (`3498333f…`, uncommitted W22 follow-up) differs from the deployed one (`bcf995d5…`), which equals `e9e5589`'s committed file. The running images were built from tree `e5bc4856`, whose runtime source dirs are identical to `e9e5589`. HEAD has since moved to `55ad4b1`, which changed runtime source, so deployed ≠ HEAD again ([`verify-only-55ad4b1.json`](refill-20261030/verify-only-55ad4b1.json)). Older pass: [`rerun-fixed-infra.json`](rerun-fixed-infra.json). |
| 2 | Fixed set rerun: 15/15 PASS; six tasks deferred (local model) and `native-tool-confinement` | **PASS (older)** | `12fb221`, qwen | [`rerun-fixed-infra.json`](rerun-fixed-infra.json), `-r2`, `-r3`. Not re-run at HEAD (see "Not run on purpose"). `run_eval.py --check` now: 39 tasks, 33 implemented, 6 held-out; the six blocked tasks are unchanged (`m03-scope`, `m04-streaming`, `m07-api-run`, `m07-daemon-topology`, `w3-rewind`, `w3-partial-write`). `native-tool-confinement` is implemented and passes (item 6). |
| 3 | Held-out set rerun, live tasks 3 independent runs with the pinned model; gate | **PASS (older revision and model); not re-measured at HEAD** | `9e34f42` + uncommitted (→ `53924be`), Haiku. `code-search-heldout` again on `7341d50` | `w11-vision` 3/3 ([`haiku-r1`](haiku-r1-w11-vision.json), [`r2`](haiku-r2-w11-vision.json), [`r3`](haiku-r3-w11-vision.json); pixel diff ≤ 0.32% vs 3% gate). `w14-design-heldout` 3/3 ([`r1`](haiku-r1-w14-design-heldout.json), [`r2`](haiku-r2-w14-design-heldout.json), [`r3`](haiku-r3-w14-design-heldout.json)). `code-search-heldout`, `stale-edit`, `tenant-isolation-api`, `w8-crash-cleanup` 1/1 each ([`haiku-heldout-*`](haiku-heldout-code-search-heldout.json)). Only `code-search-heldout` was repeated later: PASS, n=1, on `7341d50` with W22 overlays ([`../eval/baseline-20261030T171246Z.json`](../eval/baseline-20261030T171246Z.json)). The qwen results stay: vision 0/3 each, `fix-r1-w11-vision` FAIL ([`fix-r1-w11-vision.json`](fix-r1-w11-vision.json)). Disclosed in the README above: a post-hoc RATIO-parser fix, one void run (host sleep), one invalid run (port interference). **Model attribution caveat below.** |
| 4 | The follow-up's fixes committed | **PASS@rev** | `53924be` | `git show 53924be --stat`: `loop_guard.py`, its tests, both drills, `openrouter-vision.patch.yml`. Byte equality between the earlier overlay images and this commit was not re-checked. |
| 5 | Fixed live set rerun on Haiku | **NOT RUN** | - | Scheduled for W25 (`docs/m2-continuation-plan.md`); `w11-grounding` pins its own model. |
| 6 | M01 native-tool confinement closed | **PASS (older)**; box in the plan is stale | `7341d50`, rerun on `2adb551` | 5 backend cases pass ([`../w21/`](../w21/native-confinement-container.json), `-open`, `off`, `off-open`, `seatbelt`); runner PASS with revision verified ([`../eval/baseline-20261030T171026Z.json`](../eval/baseline-20261030T171026Z.json), [`…T174419Z.json`](../eval/baseline-20261030T174419Z.json)). Limits recorded by W21: a secret file created after launch or a hard link to one is not masked; `tool-fs-search` is not advertised so untested; seatbelt is refused before start. Not re-run here (see below). |
| 7 | Compose live journey passes on the verified stack | **PASS (older)** | `12fb221` | [`rerun-compose-journey.json`](rerun-compose-journey.json). Not re-run at HEAD. |
| 8 | `make static-gates`, every section's pytest and Ruff, Section 7 typecheck/tests/build | **PASS@rev with caveats** | committed `55ad4b1` (exported), plus `e9e5589` and the dirty worktree | See "Verification re-run". `next build` was **not** run. Two test-hygiene problems found (below); no product failure. |
| 9 | No unsafe action | **PASS (older), qualified** | `12fb221`; W21/W22 later | Boundary tasks passed and live failures stopped `blocked`. Qualification from W22: after a worker-daemon SIGKILL the orphan can run up to grace + interval (~15 min) while lifecycle reports `mutationPossible: false` ([`../w22/w22-dkill-ae686d336065.json`](../w22/w22-dkill-ae686d336065.json) reproduced it; fixed once on `w22fix2`, [`…4ac502b1bf2c`](../w22/w22-dkill-4ac502b1bf2c.json); the residual window remains). |
| 10 | W17 owner's SLOs defined and measured; capacity envelope | **NOT MET; OWNER-GATE; IN PROGRESS** | W17 work on `35f3ce5` + uncommitted `eventbus.py` | Proposed targets only. Dry-engine load: 100 users control p95 1.4 s against the proposed 250 ms ([`../w17/README.md`](../w17/README.md)). No real-agent capacity figure exists. |
| 11 | Restore drill on the real deployment; scheduled restore observed | **NOT RUN** (scratch drills pass) | W17 scratch drill, 2026-10-30 | [`../w17/pitr.json`](../w17/pitr.json) (MinIO scratch project; `result: pass`). The file was rewritten at 23:06:59 PDT by the W17 session, saying `backupRecorded: true`, while the README beside it, written earlier, says the backup could not be recorded. Treat it as unreviewed. Not the real deployment. |
| 12 | Backup monitoring and on-call/status ownership assigned to named people | **OWNER-GATE; NOT MET** | - | `docs/runbooks/w17-reliability.md`: no roster, no public status page; `../w17/README.md`: backup-freshness monitoring "not proven". |
| 13 | W16 documentation claims match the code | **PASS (older, static)** | as recorded in the plan | Not re-audited in this pass. |
| 14 | W13 execution leg proven with a real BYO runner | **NOT MET (partial)** | `55ad4b1`, committed 23:24:08 PDT | The commit says the gate "stays PARTIAL: same-host, test commands only, no agent loop"; evidence in `../w13/` was written by that session and not reviewed here. Not proven with a customer-controlled host. |
| 15 | W15 scheduled restore observed | **NOT MET** (not yet due) | - | Next run 2026-11-01T12:00Z; earlier trigger hit an account credit limit. |
| 16 | `agent_events` partitions through 2026-12-31 | **PASS@rev** | live DB, 2026-10-31 06:10 UTC | `pg_inherits` read-only query: `agent_events_2026_09` … `_2026_12`; the last ends `2027-01-01 00:00+00`. The next write cliff is therefore 2027-01-01; whether partitions are created automatically was not checked. |
| 17 | Package publish credentials and publish step | **OWNER-GATE** | - | No decision found in the records read. |
| 18 | Pricing amounts approved by counsel | **OWNER-GATE** | - | W18 plan: amounts still `[PLACEHOLDER: counsel review]`. |
| 19 | SSO/OIDC (R-45) pilot need-by | **OWNER-GATE** | OIDC drill 2026-10-25 | Engineering shipped and drilled 14/14 (`STATUS.md`); the need-by is the owner's. |
| 20 | W18 release-candidate review by someone other than the author | **NOT MET; OWNER-GATE** | - | Unfinished per the W19 open-gates table; reviewer unassigned. |
| 21 | External security review scheduled or done | **OWNER-GATE; NOT MET** | - | No scheduling evidence found. |
| 22 | Hermes rights evidence, or launch claim narrowed | **OWNER-GATE** | - | W6 audit: written rights evidence absent. |
| 23 | No auto-merge; plan approval and rollback documented and drilled at this revision | **NOT RUN** (documented) | - | Documented in `docs/runbooks/degraded-mode.md` ("No auto-merge"), `rollback.md`, `release-compatibility.md`. No rollback drill at this revision; image-tag rollback was only used incidentally in W22. |
| 24 | Every removed item recorded with an owner and a follow-up | **NOT VERIFIABLE by engineering** | - | The W19 table assigns roles, not named people; no "removed items" register was found in the W19–W22 records. |

Tally by primary status (24 items): measured as passing in this pass 3 (4, 8,
16); failing now 1 (1); passed only at an older revision or model 6 (2, 3, 6, 7,
9, 13); not run 3 (5, 11, 23); not met 4 (10, 14, 15, 20); owner decisions 6 (12,
17, 18, 19, 21, 22); not verifiable by engineering 1 (24).

### Beyond the W20 checklist: W21, W22, W7, W13

| Gate | Result | n | Evidence | Residual |
|---|---|---|---|---|
| W21 M01 confinement | 5 backends pass after fix; 2 leaks found first (container `.env` read, `off` mode reads/writes) | 1 per backend | [`../w21/`](../w21/native-confinement-container.json) | limits listed in item 6; the container JSON in the worktree was rewritten by a later rerun and is uncommitted |
| W22 worker and child cancellation | PASS: stop removed the selected worker and relay, neighbour kept; stop then restart of orchestrator and identity 8/8 | 1 each | [`../w22/stop-bc2a3406679e.json`](../w22/stop-bc2a3406679e.json), [`w22-restart-8bc3c7cdbdb1.json`](../w22/w22-restart-8bc3c7cdbdb1.json) | spend unknown for stopped runs |
| W22 partial-write reconciliation | PASS once (attempt 4) after 3 attempts that were a false pass, a verifier error and a miss | 1 of 4 | [`../w22/partial-write-attempt-4.json`](../w22/partial-write-attempt-4.json) | overlay images, not the final build |
| W22 crash cleanup | launcher-client crash 11/11 (2nd attempt; 1st failed the neighbour check); daemon SIGKILL: FAIL, false pass, FAIL, then PASS 8/8 on `w22fix2` | 1 pass after 3 non-passes | [`../w22/w22-dkill-4ac502b1bf2c.json`](../w22/w22-dkill-4ac502b1bf2c.json) | orphan lifetime up to grace + interval; two real bugs fixed (settled-run sweep, network-age parse) |
| W22 event replay | 8 of 9 runs passed every check | 9 | [`../w22/replay-load-*.json`](../w22/replay-load-b9b25e1510e2.json) | one unexplained ordering failure; frontend `replayTruncated` backfill is uncommitted and in progress |
| W22 provider outage | in-flight cut: run `blocked`, no partial file, fresh run recovered; network-path outage blocked and recovered | 1 each | [`../w22/w22-cut-6002596d294d.json`](../w22/w22-cut-6002596d294d.json), [`w22-outage-045f99d07d7f.json`](../w22/w22-outage-045f99d07d7f.json) | a real upstream 5xx cannot be produced on demand and is not claimed; no fallback model |
| W22 lease/heartbeat | cause found (host sleep; the reaper beat the orchestrator's own heartbeats), reproduced, fixed in `worker.py`, passed once | 1 | [`../w22/w22-suspend-7d886b237c25.json`](../w22/w22-suspend-7d886b237c25.json) | overlay image at the time; a possible-lease-weakness note from W20 is explained, not a long-tool-call defect |
| W7 `.vsix` and real window | activated, four commands registered, no errors | 1 | [`../w7/vsix-smoke.md`](../w7/vsix-smoke.md) | **end-to-end task in the window not run**; `clients/vscode/package.json`, `package-lock.json` and `.vscodeignore` that produced the `.vsix` are uncommitted |

### Verification re-run in this pass

Cheap and safe only: no model calls, no compose restarts, no live-stack writes.
Exact commands, directories and per-section counts are in the
[verification log](refill-20261030/verification-log.md). Summary:

* **Committed `55ad4b1`, exported and run with empty workspace directories:**
  Ruff clean in all ten trees; `spec_drift.py`, its pytest wrapper, the env-knob
  catalogue and the TS mirror pass; `run_eval.py --check` ok. pytest: Section 1
  1208 passed, 1 failed, 66 skipped; Section 2 48; Section 3 146; Section 4 1904
  passed, 1 failed, 60 skipped; Section 5 451 passed, 8 skipped; Section 6 123;
  Section 8 91; Section 10 91; Section 11 56; MCP 18. The two failures are not
  product defects we could show: Section 1's version-pin test only refuses an
  *export* (the venvs are editable installs of the real tree), and the Section 4
  failure (`test_lifecycle_recovery`) passed 5 of 5 alone.
* **Committed `e9e5589`, exported:** the same, with Section 1 1193 passed and
  Section 4 1894 passed and one failure each time (a different timing-sensitive
  test on each full run; all pass alone).
* **Dirty shared worktree:** Section 7 `tsc --noEmit` and `vitest run` (93 files,
  1015 tests) pass; the Ruff and pytest failures there are all in other
  sessions' unfinished files (W13's, W17's), and spec drift flipped from PASS to
  FAIL within five minutes as W13 added routes.
* **`run_eval.py --verify-only`: `verified: false`** on `e9e5589` and again on
  `55ad4b1`. The mismatch is the uncommitted `engine.py`; the running image
  equals committed `engine.py`. The check only hashes three files per target,
  so it says nothing about the identity image still carrying the pre-W13
  `runner_api.py`.
* Net: no failure attributable to the product at a committed revision, and three
  test-hygiene findings (shared default workspace, timing-sensitive Section 4
  tests, export-sensitive version-pin test), described in the log.

### Not run on purpose

* **Live and stack-touching eval tasks** (`stack-compose-journey`, the fixed
  infra set, the held-out tasks, `native-tool-confinement`): they write to the
  running stack, the credential edge allows about 5 logins a minute per IP so
  they would collide with the W22 follow-up session's imminent deploy and drill,
  and the live ones spend money. They are listed as "not re-run at HEAD", not as
  passing.
* **`next build`** and the opt-in live-Postgres/Docker tests (skips are not
  passes).
* Anything that edits or restarts the shared stack.

### State of the other sessions when read (2026-10-30 23:05–23:30 PDT, read only)

* **W22 follow-up (deploy and drill):** uncommitted edits to
  `container_runtime.py`, `engine.py`, `worker_daemon.py`, their two unit test
  files, `scripts/e2e/w22_daemon_kill_drill.py`, and Section 7 `api.ts`,
  `useEventStream.ts` and its test. The running stack is still `vw22fix2`;
  `vw22fix3` images exist. `vitest` passes with those edits in the tree.
* **W13 execution leg:** `runner_api.py`, `tests/test_runner_exec.py`,
  `byo_runner.py` were mid-edit at 23:07–23:13 (14 of that file's tests
  failing, spec drift failing because two new routes were missing from
  `identity.openapi.json`); committed as `55ad4b1` at 23:24:08, after which the
  drift and Ruff failures were gone in the exported commit.
* **W17:** `eventbus.py` guard uncommitted since 00:01; `pitr_drill.sh` edited at
  23:05; PITR scratch containers (`harness-pitr-20261031t060635z-*`) were up at
  23:07; `docs/runbooks/backup-and-pitr.md`, `scripts/loadtest/run_ladder.sh`,
  `summarize_ladder.py`, `audit_terminals.sql`, `deploy/docker-compose.yml`
  (unattributed) and Section 5 sandbox files were being edited. The W17 plan and
  evidence directory are still untracked.

### Open risks carried from W1–W22

1. **W7 end-to-end editor task not run** (activation only; packaging config
   uncommitted).
2. **W13:** execution leg committed but PARTIAL (same host, test commands only);
   no real customer host.
3. **W17:** in progress; no SLO met or owned; dry-engine, one host.
4. **W22 residuals:** orphan lifetime up to grace + interval while lifecycle says
   no mutation is possible; frontend `replayTruncated` handling not yet
   committed or deployed; the unexplained 1-in-9 replay ordering failure; the
   follow-up images are built but not deployed or drilled.
5. **Single observations:** live gates are n = 1 to 3; each W22 drill passed
   once, some after several failed attempts that remain in the pack.
6. **Speed claims come only from fixtures;** no live-agent capacity and no
   comparison with another harness.
7. **Spend recorded as unknown** where turns after a daemon death, stopped or
   killed runs have no ledger row; recorded W22 spend $0.008640 is a floor.
8. **Vision gate not re-measured at HEAD** (above), and the model attribution
   rests on README and ledger.
9. **Moving target:** three sessions edit this tree; HEAD changed during this
   pass; `verify-only` cannot be `true` until the W22 follow-up is committed and
   deployed and the stack is rebuilt from the W13 commit.
10. **No fallback model:** a provider outage blocks runs.
11. **`agent_events` write cliff moves to 2027-01-01.**
12. **`STATUS.md`** was last updated 2026-10-24 and still says NOT READY; it does
    not describe W20–W22.

### Evidence caveats

* **Model attribution.** The raw runner files under this directory do not name
  Haiku: their `model` field is the corpus's declared label (`qwen/qwen3.7-flash
  (OpenRouter, vision patch)` or "the stack DSH_MODEL"). That Haiku produced the
  gate runs rests on this README, the patch commit `53924be`, and the cost
  ledger, which shows `anthropic/claude-haiku-5.5` rows in the 16:00 UTC hour
  (12 rows, $0.053030) matching the runs' start times (16:04–16:20 UTC), and
  qwen rows before and after. A reviewer should treat it as consistent, not as
  proven by the runner files.
* **Dirty-tree runs.** Every W20 follow-up file records `dirty: true`. The
  committed fixes are `53924be`; equality with the overlays that ran was not
  re-hashed.
* **Moving worktree.** A test run on the shared worktree measures other
  sessions' unfinished edits. For that reason the numbers that count are from
  `git archive` exports of committed revisions.
* **Test hygiene.** See the verification log: Section 4's default workspace is
  the shared `/tmp/harness-dsh-workspace`, and a second full run fails three
  tests until it is empty; one other Section 4 test failed once in a full run
  and passed in isolation.

## Files

| file | sha256 (first 16) | bytes |
|---|---|---|
| `fix-r1-w11-vision.json` | `495340393f0da69d` | 11736 |
| `fix-r1-w14-design-heldout.json` | `7b7915a0ffa1edd9` | 11576 |
| `haiku-heldout-code-search-heldout.json` | `123d2b2e370076c3` | 11475 |
| `haiku-heldout-stale-edit.json` | `17b962a204dec5fd` | 11647 |
| `haiku-heldout-tenant-isolation-api.json` | `ddc0133acfb7ffdb` | 11634 |
| `haiku-heldout-w8-crash-cleanup.json` | `24c86a2576288d0a` | 11571 |
| `haiku-r1-w11-vision.json` | `bd66e5b9938dd7a9` | 11492 |
| `haiku-r1-w14-design-heldout.json` | `972499e26b8c441a` | 11576 |
| `haiku-r2-w11-vision.json` | `6d53350d1a825ba0` | 11492 |
| `haiku-r2-w14-design-heldout.json` | `77c2eb604b0faca7` | 11576 |
| `haiku-r3-w11-vision.json` | `c46add1c47024c1e` | 11492 |
| `haiku-r3-w14-design-heldout.json` | `f8da2527357b25f3` | 11576 |
| `live-r1-code-search-heldout.json` | `850b75dfd957773d` | 11473 |
| `live-r1-multi-file-refactor.json` | `7e02948fe83840cc` | 11896 |
| `live-r1-tests-written.json` | `34b858693daf0c89` | 11951 |
| `live-r1-w11-grounding.json` | `bd7db7a5448459ed` | 12821 |
| `live-r1-w11-vision.json` | `7448c6918e258405` | 11940 |
| `live-r1-w14-design-heldout.json` | `b9bacd934fc460cb` | 12177 |
| `live-r1-w8-crash-cleanup.json` | `3e00d2f6cd6d87fd` | 11617 |
| `live-r2-code-search-heldout.json` | `8fad0860d57e26db` | 11475 |
| `live-r2-multi-file-refactor.json` | `6f611d76a798c5bf` | 11964 |
| `live-r2-tests-written.json` | `9f1c90c3b9ed3f8f` | 11953 |
| `live-r2-w11-grounding.json` | `8f444bf307bbb1e6` | 12821 |
| `live-r2-w11-vision.json` | `e3f22cd3c5a62e64` | 12015 |
| `live-r2-w14-design-heldout.json` | `62db98d0fdbffd20` | 12177 |
| `live-r2-w8-crash-cleanup.json` | `943df1675f839fcb` | 11571 |
| `live-r3-code-search-heldout.json` | `7b4fee7f91d7168c` | 11475 |
| `live-r3-multi-file-refactor.json` | `4cfbd501e26add1b` | 11964 |
| `live-r3-tests-written.json` | `1b73a7b7b4bfc577` | 11951 |
| `live-r3-w11-grounding.json` | `55279ac57fadd7ce` | 12821 |
| `live-r3-w11-vision.json` | `b31987a90b689581` | 17634 |
| `live-r3-w14-design-heldout.json` | `b06ebee7b0a9761c` | 12179 |
| `live-r3-w8-crash-cleanup.json` | `524c2cfbe182cb56` | 11573 |
| `live-r4-code-search-heldout.json` | `1e4ec972a51a57b0` | 11475 |
| `live-r4-multi-file-refactor.json` | `afe05412a17bb6ce` | 11896 |
| `live-r4-tests-written.json` | `c4230d25c21f44c8` | 11953 |
| `live-r4-w11-grounding.json` | `9705e6c79f6a6241` | 12821 |
| `live-r4-w11-vision.json` | `755c9c10f13c8197` | 11657 |
| `live-r4-w14-design-heldout.json` | `d51bd0b9079f7b51` | 11668 |
| `live-r4-w8-crash-cleanup.json` | `7f4284507462b85d` | 11573 |
| `live-r5-code-search-heldout.json` | `edd37d7cb8267ee0` | 11473 |
| `live-r5-multi-file-refactor.json` | `5bfe8bedd5e3a9d2` | 11896 |
| `live-r5-tests-written.json` | `45c2ec2784ee7650` | 11953 |
| `live-r5-w11-grounding.json` | `6e14b3784953a82b` | 12821 |
| `live-r5-w11-vision.json` | `cb8343e762cfe79c` | 11494 |
| `live-r5-w14-design-heldout.json` | `e709b5e03ad69ce5` | 12177 |
| `live-r5-w8-crash-cleanup.json` | `af7d7348e800444a` | 11617 |
| `live-r5b-w14-design-heldout.json` | `ed7e00aee4c740ce` | 11578 |
| `live-r6-code-search-heldout.json` | `5af0c38ab5412dd0` | 11475 |
| `live-r6-multi-file-refactor.json` | `8444277d27852501` | 11896 |
| `live-r6-tests-written.json` | `118191d61caf1add` | 11953 |
| `live-r6-w11-grounding.json` | `17471fbae48434e4` | 12819 |
| `live-r6-w11-vision.json` | `e2d1dd1397449ec2` | 11736 |
| `live-r6-w14-design-heldout.json` | `ea7f56660f392965` | 11666 |
| `live-r6-w8-crash-cleanup.json` | `c48ed198f6571cf9` | 11573 |
| `rerun-compose-journey.json` | `180a1f4e0a2460eb` | 11209 |
| `rerun-fixed-infra-r2.json` | `6118e4514823424a` | 37790 |
| `rerun-fixed-infra-r3.json` | `ed2f8ab3d1e28bec` | 37798 |
| `rerun-fixed-infra.json` | `3e0cbfa21fcbefec` | 39150 |
| `rerun-stale-edit-2.json` | `83d23f641e96eb68` | 10827 |
| `rerun-stale-edit-3.json` | `4ec79722cd704ec9` | 10829 |
| `rerun-stale-edit.json` | `f4ef8886c599cb1e` | 10829 |
| `rerun-tenant-isolation-api-1.json` | `b33b656e5ce2185e` | 10700 |
| `rerun-tenant-isolation-api-2.json` | `59e7b9ad62f80b9f` | 10620 |
| `rerun-tenant-isolation-api-3.json` | `b6f5ed547ad51c72` | 10620 |
| `rerun-tenant-isolation-api-4.json` | `b6ebe7532dcc40a7` | 10620 |
| `smoke-claude-haiku-5.5.json` | `6c545e2baf8be246` | 771 |

Driver-native evidence named in each file's `stdoutTail` lives under
`docs/briefs/evidence/eval/` and `docs/briefs/evidence/pilot-prep/`.

### Files added by the owner-readiness refill

Hashes taken when these files were written (2026-10-30, 23:37 PDT); this README is not hashed because it contains the table.

| file | sha256 (first 16) | bytes |
|---|---|---|
| `owner-brief.md` | `37874f42c8ff5f9b` | 7648 |
| `refill-20261030/verification-log.md` | `4d8b055c460d7345` | 7409 |
| `refill-20261030/pytest-results.txt` | `592fdaaeac73418f` | 4070 |
| `refill-20261030/verify-only-e9e5589.json` | `a8243619efffa62f` | 2460 |
| `refill-20261030/verify-only-55ad4b1.json` | `c61f0d98a5cdc539` | 2487 |
| `refill-20261030/worktree-snapshot-start.txt` | `3e92ffc7caf9ea0b` | 1279 |
| `refill-20261030/worktree-snapshot-end.txt` | `df37e173785f271b` | 1799 |
