# Demo prototype verification — 2026-11-11 (accelerator video readiness)

**Verdict: WORKING PROTOTYPE, verified live end-to-end on 2026-11-11.** The web hero journey
(goal → plan gate → human approval → confined live execution → pause → redirect → resume →
completion → cost ledger) executed with real model calls and real files. This document is the
evidence record for the accelerator demo video (`docs/yc/VIDEO-PLAN.md`, `docs/demo/yc-demo-runbook.md`).

## 1. Stack configuration (as verified)

| Item | Value |
|---|---|
| Compose project | `harness-demo` (fresh DB volume; **runs volume `harness_runs` is shared with the old project** — per-run dirs are isolated, but the volume is not demo-exclusive) |
| Backend images | `harness-backend:vw24b1` — 11/11 services, `stack_tags.py` agreement: AGREE |
| Frontend | `harness-frontend:vw24b2` (Pause button), `http://localhost:3010` (`HARNESS_FRONTEND_PORT=3010` in `.env`) |
| Worker daemon | `harness-worker-daemon:vw24b1`, **requires `--profile worker`** (missing from the Nov-1 runbook boot path — root cause of run-1 failure) |
| Engine | `ENGINE_LIVE_MODE=live`, `HARNESS_ENGINE_CONFINEMENT=container`, `HARNESS_ENGINE_ISOLATED=1` → engine-worker container path (strongest boundary per P-04 review: per-run internal network, DNS kill, cap-drop ALL, secrets→/dev/null) |
| Provider | OpenRouter, model `anthropic/claude-haiku-5.5`, patch `openrouter-vision.patch.yml`; key auth verified (~$4 credit at start) |
| Auth | Dev-mode email sign-in (no SMTP); no OTP prompt observed for `demo@harness.local` |
| Source baseline | checkout `w28-s1-delegate` @ `7c1acf4`; images are the Nov-1 verified demo set, NOT current source |

### Corrected boot commands (use these for recording)

```sh
cd "/Users/user/untitled folder 2/harness"
HARNESS_IMAGE_TAG=w24b1 docker compose -p harness-demo --env-file .env \
  -f deploy/docker-compose.yml --profile worker up -d
HARNESS_IMAGE_TAG=w24b2 docker compose -p harness-demo --env-file .env \
  -f deploy/docker-compose.yml up -d --no-deps --no-build frontend
python3 scripts/ops/stack_tags.py   # expect: 11/11, AGREE (vw24b1), frontend vw24b2
```

## 2. Run records (all live, real model spend)

| Run | Goal | Outcome | Events | Cost (ledger) | Files verified in sandbox workspace |
|---|---|---|---|---|---|
| `run-c74e1d87b479` | temperature converters | **FAILED honestly**: `worker daemon: Name or service not known` (daemon profile not started). No fake completion. | 5 | $0 | — |
| `run-a230d8306753` | temperature converters + tests | **engine_completed** ~18s after start | 20 | **$0.003521** (29,644 in / 1,114 out tokens) | `temperature.py` (both functions, docstrings), `test_temperature.py` (known points + round-trip), `__pycache__` (tests actually ran) |
| `run-248e9a4f4ea8` | calculator package (4 ops + tests) | **paused → redirected → resumed → engine_completed** | 40 | **$0.010075** (80,414 in / 4,068 out) | `calc/{add,subtract,multiply,divide,power}.py`, `tests/test_calc.py`, `__pycache__` |

### Pause / redirect / resume evidence (run-248e9a4f4ea8)

- `agent_events`: seq 41 `paused`, seq 42 `resumed`, seq 60 `engine_completed`; `model_routed` ×2 (pre/post pause); 16 tool_call/tool_result pairs.
- UI at pause: "Paused: the agent's process is stopped; it cannot change files until you resume." Steps frozen at 21.
- Direction sent while paused via Team tab → session leader: "Also add calc/power.py with a power function and cover it in the tests." UI: "The agent gets this when the run next starts or resumes."
- UI at resume: "Resumed: the agent continues from where it stopped, with any direction sent while paused."
- **Behavioral proof the direction landed: `calc/power.py` exists** and `engine_completed` payload references it.
- Discrepancy vs Nov-1 runbook: no separate `guidance.delivered` row found in `agent_events` (searched types + payloads). Direction delivery is proven by output, not by that event name. Minor; does not block the demo.

## 3. Integrity rules for the video (inherited + observed)

1. **Plan text is a fixed template** ("Inspect codebase / Apply edits / Run unit tests", est. $0.050) — observed again today. Say "the plan gate", NOT "the agent wrote this plan for my task". (W24 finding 4.)
2. **The /demo page is a labelled replay** — never present it as live. The live journey above is what to record.
3. **Changes tab shows "No snapshots yet"** for web-only runs — diff recording happens in the VS Code flow ("Record this result"). For the per-file Alt+Y/Alt+N shots, use the VS Code segment (runbook 1:00–1:55; W24 evidence `docs/briefs/evidence/w24/`, rehearsed Nov 1 — re-rehearse before recording).
4. Voiceover "no network beyond what's allowed, no access outside the repo" is accurate for the **container engine path verified here**. Do not use it for Seatbelt process-mode runs (P-04 G2: broad reads).
5. Label any cut; no credentials on screen; record date/tags/model/run IDs (this file).
6. Sandbox is **pilot-grade** (P-04 review: 14 gaps, G1–G3 High). The video shows a local demo, not a production-readiness claim. `docs/demo/techstars-local-demo.md` wording applies.

## 4. Recording checklist

- [ ] Boot with §1 commands; `stack_tags.py` AGREE; frontend :3010 HTTP 200
- [ ] Rehearse the web journey once (longer goal so Pause is clickable; short goals finish in ~18s)
- [ ] Re-rehearse VS Code segment with `scripts/demo/techstars_fixture.py` (fresh temp dir — **do NOT use `yc_demo_vscode.sh`: it wipes `~/harness-demo`, which contains another tool's `.workbuddy-ai` data**)
- [ ] Sign in before recording; Do Not Disturb; runbook shot list 0:00–1:55
- [ ] After recording: log take date, run IDs, costs here

## 5. States

- Demo web journey: **Verified at 2026-11-11 live runs** (this file, run IDs + ledger + files).
- VS Code segment: historical W24 evidence (Nov 1); **re-rehearsal pending** before recording.
- Stack: demo-only (w24b1 images ≠ current source; NOT READY verdict unchanged for production).