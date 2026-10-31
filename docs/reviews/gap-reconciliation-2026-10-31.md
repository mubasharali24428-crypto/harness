# Gap reconciliation — live session at rev `0df3e5a` (2026-10-31)

Scope: the open-work inventory through Marathon 2.0 W22. This session resolved
what the local environment can prove (live stack on this Docker daemon,
deployed model `anthropic/claude-haiku-5.5` over OpenRouter, Postgres at 5499,
~$0.10 approved spend) and marked the rest with its real blocker. Every PASS
below names its evidence file; nothing here is inferred from a unit test.

Baseline: HEAD was `211e740`; this session committed `0df3e5a` (W22 fail-closed
boundary status + repair of three tests that were red at `211e740`). A
concurrent lane was writing the workflow contract (contracts 1.0.0rc7,
`workflow_runs.py`) into the same tree during this session; per the operator's
decision nothing from that lane was committed, which also holds back the W17
terminal-publish retry (complete and green, but its file is shared).

Deployment under test: `vw22fix4` built from `git archive 0df3e5a` (clean tree,
concurrent-lane edits excluded); in-image
`container_runtime.py` md5 `56871c93a5d11059a812322159ff1044` matched the
committed blob. Backend, orchestrator, worker-daemon all run `vw22fix4`.

## Inventory rows, current status

| Gap (from the inventory) | Status now | Evidence / blocker |
|---|---|---|
| W1–W3 stop / torn-write / rewind at current revision | **RESOLVED** — all three re-run with a live chat model at `0df3e5a` | `w22/w22-stop-current.json` (live child, worker gone, ok), `w22/w22-partial-current.json` (29/120 torn, rewind removed `out.txt`, resume produced exactly 120 lines), `pilot-prep/w3-rewind.json` (`ok: true`, file restored) |
| W4 third-party MCP server review | OPEN (unchanged) | needs an external third-party server + operator review; recorded MCP evidence remains the local stub |
| W6–W7 Hermes rights, toolchain prereqs, H3 LSP for `.hms` | BLOCKED (unchanged) | rights/distribution clearance is a human/legal task; the W7 Python-LSP proof still does not establish `.hms` support |
| W7 full task in a real VS Code window | OPEN | needs a human GUI session; vsix packaging/activation smoke stands as-is |
| W8 overlapping concurrent runs, same path, conflict handling | **RESOLVED** — live drill PASS 22/22 at `0df3e5a` | `pilot-prep/w8-concurrency-1793433265.json`: `sharedPathInBothRuns`, `overlapDigestsDiffer`, `crossRunDigestRefused` (409 CHANGE_STALE) all true |
| W11–W12 fixed live set rerun on the selected model | **RESOLVED** — 3/3 PASS on `anthropic/claude-haiku-5.5` at `0df3e5a` | `multi-file-refactor` PASS 32.99 s, `tests-written` PASS 25.12 s (`eval/baseline-20261031T080830Z.json`); `w11-grounding` PASS 22/22 (`pilot-prep/w11-grounding-1793434581.json`) — its first FAIL was the drill's own probe (`object()` as DB factory crashed the refusal check); the product gate was proven correct by direct call and the drill fixed |
| W13 model-driven loop on a real remote customer host | BLOCKED | no remote customer host is reachable from this environment; local evidence covers approved test commands on the service host only |
| W13 runner accounting / redispatch / cancel-mid-command drill / transport hardening / runner UI | OPEN — feature work, not verification | each item is an implementation task with its own acceptance test; none can be "proved" against code that does not exist yet |
| W14 code-search comparison had no winner | OPEN | one invocation per model is the recorded limitation; a multi-run comparison is live spend + a decision on repeats, unchanged by this session |
| W15 scheduled restore fires and records | BLOCKED (external) | the trigger is the QoderWake automation `tr_a4315e8e62b81c7819aca10d` (next planned firing 2026-11-01T12:00Z) whose blocker was account credits — not inspectable or fireable from this repo or host. Manual restore drill remains PASS |
| W16 counsel sign-off, external security review | BLOCKED (human) | placeholder terms stand; needs counsel and a purchased review |
| W17 terminal-event publication defect candidate | **FIXED, HELD UNCOMMITTED** | bounded retry (4 attempts, backoff) for `ENGINE_COMPLETED`/`ENGINE_FAILED` in `service.py::_publish_safe`; `test_w17_terminal_publish.py` xfail deleted, 9 passed incl. negative controls. Held because `service.py` carries the concurrent lane's in-progress edits — commit with that lane's landing or right after |
| W17 real-provider load on target deployment | BLOCKED | requires the intended deployment + an approved provider-load budget; laptop dry-engine numbers stand as recorded |
| W17 remaining failure drills vs W22 coverage | **RECONCILED** (below in this doc) | provider + daemon coverage supplied; MCP-cleanup, Linux-shell-cleanup, NOTIFY-replica legs still open with reasons |
| W17 SLO/on-call owners, alert delivery | PARTIAL — routing verified, humans missing | Prometheus→Alertmanager proven live this session (2 alerts arriving); the only receiver is `web.hook → http://127.0.0.1:5001/`, which no process listens on — delivery to a named human is impossible until an owner supplies a real endpoint |
| W18–W19 independent RC review of the importer | BLOCKED | an independent reviewer cannot be produced from inside this session |
| W19 indexing admission across replicas | OPEN | admission exclusion is per-process by design today; the proof needs a two-replica deployment (multi-replica operation is a known v1 residual) |
| W20 release decision record filled | BLOCKED | needs an authorized human reviewer + decision + date |
| W21 advertised backends not exercised | OPEN per-backend | recorded backends PASS at verified revision; any NEW advertised backend still needs its own evidence — unchanged, no new backend was advertised here |
| W22 prompt cleanup after daemon death | **RESOLVED** — re-drilled at `0df3e5a` | see below |
| W22 replay + frontend backfill at current revision | **RESOLVED** | see below |
| W22 reliability repeats (gates passed once, replay 8/9, no real 5xx) | PARTIAL | replay now 9/10 consecutive passes at current revisions; the genuine upstream 5xx still cannot be produced on demand — recorded as not-claimed |
| Skipped-test inventory actionable | **RESOLVED** | census below |

## W22 — daemon-kill orphan, re-drilled at the committed revision

The uncommitted Docker-inspection hardening from the prior session
(`run_boundary_running`: unreadable inspect/ps now means *running*, never a
reap licence) was test-verified (26 passed) and committed as part of `0df3e5a`,
built into `vw22fix4` with hash match, and proven live:

* `w22/w22-dkill-76b5633be5b3.json` — **PASS 8/8**: child live while the
  daemon was dead, `mutationPossible: true` during that window (status honest),
  daemon back → targeted `POST /runs/<id>/reap` at 07:50:33, containers gone
  4 s later, lifecycle then truthfully `mutationPossible: false`.
  **Orphan lived 17 s after the daemon returned** (previous residual: up to
  grace+interval ≈ 15 min). The fast reaper (engine-side `POST /reap` retry
  loop) is what closed the window; the periodic sweep remains the fallback.

## W22 — replay, truncation and frontend backfill

* Backend: `w22_replay_overflow_drill.py` re-run at `0df3e5a` on `vw22fix4` —
  **PASS 8/8** (`w22/replay-load-9b9e224aa3a7.json`), including
  `B_recovered_everything` (0 missing) and `A_union_exact`. The one earlier
  `A_union_exact` mismatch has not recurred across the drills since: **9 of 10
  consecutive passes** counting the pre-existing record.
* Frontend backfill: the code is real and committed at `211e740`
  (`useEventStream.backfill(marker.afterSeq, marker.oldestSeqSent)`), deployed
  (in-container `useEventStream.ts` md5 equals the tree), and unit-proven:
  `vitest tests/unit/useEventStream.test.ts` **20 passed** at this session.
  The W22 plan's "frontend does not backfill" line is superseded.

## Skipped-test census (the actionable inventory)

Run with `-rs` and live environment knobs at `0df3e5a`:

| Suite | Plain run | With required env | What remains skipped |
|---|---|---|---|
| section1 | 63 skipped (historical count in W13 plan) | **1282 passed, 1 skipped** with `HARNESS_TEST_PG=1` + compose PG :5499 | 1 skip (`promtool not installed`) then **closed too**: promtool from the pinned `prom/prometheus:v2.53.0` image validated the file directly (**SUCCESS: 18 rules**) and via the test itself — `test_alert_rules_load.py` **9 passed**. With PG + a promtool shim, section1 has **zero skips** |
| section4 | 59 skipped | **1973 passed, 19 skipped** with the same PG env | PG no longer skips anything; remaining 19: opt-in Docker, opt-in real-local-model, opt-in OpenRouter live tests |
| section1 (at `211e740`) | — | 2 failures: contracts version-pin rot | caused by the concurrent lane's uncommitted rc7 bump with the TS mirror header at rc6 — theirs to close, not a HEAD defect once their lane lands |

Inventory method note for future censuses: one marker can cover several tests,
and `-rs` counts differ from marker counts (59 markers → 59 summary lines here,
but section1's historical 63 was markers, not tests). Always cite the `-rs`
run, the env knobs, and the commit.

## W17 drills vs W22 coverage (the requested reconciliation)

W22 (at `e9e5589`/`2adb551`, plus this session at `0df3e5a`) **does** supply, so
W17 should not repeat them: worker-daemon SIGKILL + orphan reaping
(now 4 records incl. this session's), provider-path outage + recovery
(network-cut, response-cut mid-stream), crash cleanup + launcher-client crash,
replay/overflow/backfill, stop-then-restart. W17 legs still NOT covered by any
record: MCP server cleanup inside the vendored worker runtime, Linux-confined
shell cleanup (needs a Linux host; macOS Seatbelt paths are proven), event-bus
behaviour across Postgres NOTIFY *replicas* (single-instance proven; two
orchestrators sharing the bus unproven), and a *genuine* upstream 5xx (cannot
be produced on demand — stays honestly unclaimed).

## Fixed live set (W11–W12) — result

`run_eval.py --include-live --task multi-file-refactor --task tests-written
--task w11-grounding` at `0df3e5a` against the deployed Haiku stack
(evidence `eval/baseline-20261031T080830Z.json`):

* `multi-file-refactor` — **PASS** (32.99 s)
* `tests-written` — **PASS** (25.12 s)
* `w11-grounding` — FAIL in that batch, then **PASS 22/22** standalone
  (`pilot-prep/w11-grounding-1793434581.json`) after fixing the drill's own
  vision-refusal probe. The failing check was `productRefusesTextOnlyModel`;
  the probe passed `object()` where `resolve_goal_images` needs a session
  factory, so the check crashed instead of exercising the gate. Direct calls
  prove the product gate is correct: a text-only model returns
  `refused="model … is not in HARNESS_VISION_MODELS…"` with 0 images attached,
  and an allow-listed model passes the gate. The model pin is resolved: the
  corpus entry keeps its pinned vision model (`qwen/qwen3.7-flash`) for the
  provider-rejection leg, while the grounding tasks ran on the deployed model.

Note the W11–W12 record (Qwen failures on design-to-code fidelity and provider
empty responses) is a MODEL-QUALITY finding about Qwen; this rerun establishes
Haiku passes the fixed live set, it does not re-adjudicate Qwen.

## Honest residuals

* Live gates in this session passed **once each** (daemon-kill 1/1 at
  `0df3e5a`, W8 1/1, W1–W3 1/1 each). Replay/overflow is now 9/10 consecutive.
  No reliability RATE is established by single observations — the inventory's
  last W22 row stays open by nature until a repeated-runs program exists.
* W17 retry: `service.py` edit + `test_w17_terminal_publish.py` remain
  uncommitted because the concurrent lane holds `service.py`; CI would fail the
  test without the source, so both wait for that lane's landing.
* The genuine upstream 5xx, real remote customer host (W13), multi-replica
  proofs (W19 indexing admission, NOTIFY across replicas), paid external
  review (W16), counsel sign-off, SLO/on-call ownership (W17), VS Code GUI
  session (W7), third-party MCP server (W4) and the release decision (W20)
  cannot be produced from this environment; each keeps its named blocker.
* `deploy/.env` did not carry `HARNESS_MASTER_KEY`; compose `up` needs
  `--env-file deploy/.env --env-file .env` (last-wins root `.env` selects the
  Haiku live block). Recorded so the next drill does not lose 10 minutes to it.

## Spend

Ledger-metered model spend for this session's drills
(`anthropic/claude-haiku-5.5`, 3 h window incl. pre-session W22 tail):
**$0.0295 total**, well under the approved ~$0.10 cap. This session's own
drills (daemon-kill $0.000734, W8 four runs + stop, W1–W3 reruns, fixed live
set, grounding rerun) are the bulk of it. Killed/short runs may lack ledger
rows, so recorded is a floor, not the exact provider bill (same disclosure
style as W22).
