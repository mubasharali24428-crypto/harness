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
