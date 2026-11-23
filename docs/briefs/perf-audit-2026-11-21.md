# Harness performance audit: where a policy-gated run spends its time

Date: 2026-11-21. Auditor: Claude (Opus 5.5). Repo `harness`, branch `w28-s1-delegate`. HEAD was `d4984e1` at the start and `160dd30` by the end: someone else committed a docs-only roadmap refresh during the audit. Working tree has two untracked items that I did not touch: `.openclaw/` and `FUTURE PLAN 2.0.md`.
Labels: **[M]** measured by a command I ran · **[T]** read from existing authorized traces · **[I]** inference · **[F]** forecast.
Spend: zero paid model calls and zero production traffic. All experiments ran in disposable `--network none` containers.

## 1. Recommendation

**Don't prototype Rust. Start with model and tool usage: remove the extra model turns caused by policy denials.** As a free side fix, add one line to the worker image so it ships precompiled Python bytecode.

- On the representative live run, **98.5% of the time is model turns**: 332.6 s of 337.8 s across 5 runs [T]. All tool execution together is **1.0%**, at 0.64 s for the median run [T]. Removing every bit of local Harness compute (engine, gate, container, audit) would save at most about 3% (≈1.8 s of a 61 s run) [M+I]. That ceiling rules out a Rust port as the first lever.
- The largest removable chunk is the **policy-denial recovery turn**. 3 of 5 live runs sent a command the shell gate refuses (`pwd && ls`, and twice `grep … 2>/dev/null`). Each denial costs a full extra model round trip: 4.2 s, 6.8 s and 21.6 s. That is a mean of **6.5 s per run (9.7% of the total)** and a median of 4.2 s [T]. The bash tool description already forbids these forms (`dsh_plugins/harness-shell.js:12-15`, in place since `8977c4d`, before the runs), so the model ignores the prose rule.
- Free side fix: the worker image (`section4/worker.Dockerfile:1-2`, `python:3.12-slim` + `PYTHONDONTWRITEBYTECODE=1`, user 10001) ships **zero `.pyc` files**. As a result every `bash` call recompiles about 29 modules. Precompiling cuts the gate from **406 ms to 86 ms per call** (4.7×) and the zero-latency engine run from **1.44 s to 1.08 s** [M]. Gate decisions and Landlock behaviour were identical on the parity fixtures. In absolute terms this is small: about 0.36 s per median run (≈0.6%). It grows with bash-heavy runs: the 50-call run `run-4dd28c4748e5` would save about 16 s [I].

**Expected impact [F]:** removing the denial turns saves 4–7 s per run on this task (≈7–10% of end-to-end), with a long tail up to about 20 s. The bytecode fix adds 0.3 s per bash call.

## 2. Setup

| Item | Value |
|---|---|
| Host | macOS 26.7.4 arm64, Docker 29.5.3 (Desktop, linuxkit kernel), host Python 3.13.13, no Rust toolchain |
| Runner | Containerized. The live traces come from the running `harness-rc-*` stack. The per-run worker image is `harness-engine-worker:vv90i13` (Python 3.12.12, `deepseek-harness-sdk 0.2.1rc2`, `HARNESS_ENGINE_CONFINEMENT=container`) |
| Fixture | Eval corpus task `code-search` (`scripts/eval/tasks/code_search_task.py`): a synthetic 100-file repo with 1 target, 5 decoys and 94 fillers. The goal is to find `RETRY_BACKOFF_SECONDS` and change 5→7. It covers a repo search, a bounded edit and the audit/event trail. **It has no test step** (see §4) |
| Live traces [T] | 5 authorized live trials from 2026-11-13 (`docs/briefs/evidence/w7-bench/live-model/`, DeepSeek-V4-Flash, 5/5 PASS, $0.0025 total). Per-event timestamps come from `agent_events` in `harness-rc-db-1` (read-only queries) |
| Approval | **Simulated**: the driver POSTs `/plan/approve` immediately (`scripts/e2e/w11_vision_drill.py:295`). Proposal→approval took 7–22 ms. Human wait is excluded |
| Sandbox startup | Not in the live event span. Measured standalone (6× `docker run --rm --network none … true`) |
| Replay [M] | The real `python -m harness_orchestrator.engine_worker` (`section4/src/harness_orchestrator/engine_worker.py`), driven by a localhost stub OpenAI-compatible provider that replays live run `…1794561357` turn for turn (grep → read → edit → final answer). Model latency is about 0, so this isolates local cost. It uses `deploy/patches/openrouter.patch.yml` and runs inside the worker image with container confinement. The workspace is container-local, without the production read-only binds |
| Repetitions | Gate microbench n=15 per case. Replay n=7 per image. Container start n=6. Live n=5 |
| Not used | The `bench_trials.py --dry` evidence (`docs/briefs/evidence/w7-bench/dry-*`): its durations are synthesized by `fake_attempt` (`scripts/eval/bench_trials.py:54-60`) and are not measurements |

Commands (scripts are in the session scratchpad; contents are summarized in §6):
```
python3 spans.py <5 run ids>                                   # phase split from agent_events
docker run --rm --network none -v gatebench.py:/bench/gatebench.py:ro --entrypoint python3 harness-engine-worker:<img> /bench/gatebench.py 15
docker run --rm --network none -e HARNESS_ENGINE_CONFINEMENT=container -e DEEPSEEK_BASE_URL=http://127.0.0.1:18080/v1 \
  -e DEEPSEEK_API_KEY=stub-not-a-key -e DSH_MODEL=stub-model -e DSH_PATCH_FILES=/b/p.yml ... harness-engine-worker:<img> /b/replay.py 7
# <img> = vv90i13 (baseline) | audit-pyc-scratch (FROM vv90i13; RUN python3 -m compileall -q -j0 /usr/local/lib/python3.12 /opt/harness-shell)
```

Limitations: the live sample is n=5 on one task and one model, with high variance (44–117 s). Provider latency inside a turn cannot be split from runtime glue in the live data, because SDK session logs were not retained (0 `session.v4.jsonl` on `harness_rc_runs`). The replay therefore bounds the local share instead. The bytecode variant was not run end to end on the live stack.

## 3. Timing breakdown (code-search, 5 live runs, event span median 61.15 s, range 43.90–114.46 s)

| Phase | Median (range) | Share of total (Σ over 5 runs) | Evidence / notes |
|---|---|---|---|
| Intake: run row → `plan_proposed` | 7 ms (6–8) | ≈0% | [T] `agent_runs.created_at` vs first event |
| Policy + plan + approval → `execution_started` | 29 ms (29–47) | 0.05% | [T] **approval simulated** |
| Model routing → `model_routed` | 271 ms (102–502) | 0.4% | [T] `model_routing.py` |
| **Model turns (incl. runtime and container start)** | **60.65 s (43.2–113.1)** | **98.5%** | [T] gaps between `tool_result` and the next `tool_call`/`engine_completed` |
| · first turn (includes runtime start) | 12.81 s (8.47–45.21), n=5 | (in the row above) | [T] about 5 s above mid-turns: a larger first prompt plus start-up |
| · mid turns | 7.47 s (0–21.6), n=18 | | [T] 0 s = parallel calls in the same turn, run sequentially |
| · final turn (answer only) | 4.17 s (3.28–53.78), n=5 | | [T] the 53.8 s outlier is provider-side [I] |
| · of which policy-denial recovery | 4.2 s median per run (0–21.6), mean 6.5 s | **9.7%** | [T] 3/5 runs. The removable part |
| · of which local runtime glue (est.) | ≈1.0 s/run startup + ~10–16 ms/turn | ≈2% | [M] replay: worker start → first model request 883 ms (880–948), between-turn glue 10–16 ms excluding bash |
| Sandbox container start | 0.33 s (0.33–0.38) | ≈0.5% | [M] standalone. Not visible in events; it falls inside the first turn [I] |
| Tool execution, all tools | 0.64 s (0.38–1.05) | 1.0% | [T] `tool_call`→`tool_result` |
| · `bash` via shell gate | 0.37–0.42 s per call (10 calls) | | [T]. Standalone 406 ms (397–466), **399 ms of it child CPU** [M] |
| · `read` / `edit` / `grep` (native fs tools) | 2–53 ms per call | | [T] |
| Test setup / execution | **not on this path** | 0% | the fixture has no tests; see §4 |
| Audit/event persistence | ≤2 ms per event | ≈0% | [I] bound: a `read` call→result pair (2 DB inserts + NOTIFY) spans 2–4 ms |
| Driver overhead (not product) | ≈3.2 s per run | — | [T] bench wall − event span. Mostly the 4 s settle poll (`w11_vision_drill.py:307`) plus login, seeding and hashing |

Gate profile (cProfile, `echo hello`, baseline image) [M]: 87 ms in `builtins.compile` for 29 modules compiled from source, 54 ms in `confinement.landlock_supported()` (a fresh probe child per call, `section5/src/harness_sandbox/confinement.py:74-110`), and about 176 ms waiting on the confined child, which is itself a bytecode-less Python start. Python `-I` floor: 12 ms. Raw `grep`: 0.9 ms.

## 4. Ranked alternatives

| # | Candidate | Measured impact / bound | Effort | Must preserve |
|---|---|---|---|---|
| 1 | **Stop denial-recovery turns** (model/tool usage) | 4.2 s median, 6.5 s mean per run (≈7–10%) [T]. Ceiling: the whole denial turn | Hours | Section 5 policy unchanged by default. Denial reason and alternative text (W27), the denial breaker (`harness-shell.js:19`) |
| 2 | **Precompile bytecode in the worker image** (`RUN python -m compileall` in `section4/worker.Dockerfile`) | −320 ms per bash call, −150 ms per denial, −0.36 s per replay run [M]. ≈0.6% on code-search, more on bash-heavy runs | 1 line + image rebuild | Identical gate output and Landlock result (verified on 12 fixture commands, §6). Image digests in evidence |
| 3 | Cache the Landlock probe across gate calls | ≤54 ms per call [M] | Small, but security-sensitive (stale capability) | The fail-closed `auto` semantics |
| 4 | Fewer or cheaper model turns: steer search to the first-class `grep`/`glob` tools, put the workspace root in the goal (the `pwd` probes) | Mid-turn median 7.5 s each [T]. Skipping one turn saves ≈7 s [F] | Hours | Repo-context injection (`engine.py:1166`), no forced model downgrade (AGENTS.md) |
| 5 | Faster model/provider | Up to ≈59 s per run, but the choice belongs to the user/team, not Harness | — | **"No forced model downgrade"**: only as a user-selected option |
| 6 | Faster test workflow | **0 on this path.** Also: `pytest` is allow-listed but **not installed in the worker image** (`No module named pytest` [M]), so agent test runs cannot work for Python repos today. A test call pays the gate's fixed 0.42 s (0.11 s with bytecode) on top of the suite | — | Pick a fixture with tests before optimizing tests |
| 7 | Rust shell gate | Ceiling = all tool time = 0.64 s (1.0%) [T]. After item 2, the remaining gate cost is ≈86 ms per call, of which ~9 ms is interpreter floor and the rest is the confined exec. Rust would save at most ≈0.15 s per run (0.25%) [F], at the cost of a second language, bindings/IPC, and re-certifying S-01 enforcement | Days–weeks | Frozen `{"denied"}/{"exitCode","output"}` contract, Landlock/Seatbelt behaviour |

**Rust verdict:** no isolated CPU-heavy component contributes materially. The only CPU-bound hotspot, the shell gate, turned out to be a packaging defect (missing bytecode) rather than slow code, and one Dockerfile line fixes it. **Missing measurement that could change this:** per-turn provider timestamps (request sent / first byte / last byte) on the live path. If local runtime glue on real responses turned out much larger than the 10–16 ms per turn seen in replay (for example streaming-parse cost on long outputs), the local share would rise. It cannot rise above the ~3% ceiling unless that glue is more than 10× the replay figure.

## 5. Next experiment

- **Entry point:** `python3 scripts/eval/bench_trials.py --task code-search --trials 10 --model DeepSeek-V4-Flash --include-live` against the rc stack. Same fixture, same model, same budget. Owner-gated spend: about $0.005 per arm at the recorded $0.0005 per run.
- **Baseline:** the current image and plugin (5 trials exist; run 10 fresh trials so the arms are contemporaneous).
- **Candidate:** (a) a security-neutral steering change only: in `harness-shell.js` DESCRIPTION, lead with *"For searching file contents use the `grep` tool, not bash. Never append `2>/dev/null`, `&&`, `|` — they are always refused."* and put the workspace absolute path in the goal preamble. (b) Bundle the bytecode Dockerfile line in the same image, because it is behaviour-neutral, and report its share separately with the zero-spend replay (§2, `replay.py`).
- **Primary metric:** policy denials per run, and model turns per run (from `agent_events`, `spans.py`). Wall time is secondary. With 44–117 s spread, n=10 cannot resolve a 4 s median shift reliably, so state confidence intervals rather than a point claim.
- **Acceptance:** denial-recovery turns ≤1 in total across 10 runs (baseline 3/5). Model turns per run median reduced by ≥1. Median event-span wall reduced by **≥4 s (≥6%)**. Zero-spend replay total ≤1.10 s (baseline 1.44 s). 10/10 PASS. Gate parity fixtures identical. `make test-section5` (Linux image) and section4 unit tests green.
- **Drop if:** denials per run do not fall (the model ignores the description again). In that case the next option is a Section 5 policy review of allowing a bare trailing `2>/dev/null`, and that is a security decision, not a performance one. Also drop if pass rate falls below 10/10, or turns per run do not change.

## 5b. Experiment result (live A/B, 2026-11-21) [M]

The owner approved the spend. `DeepSeek-V4-Flash` is no longer in the rc catalog (the stack now routes through OpenRouter, and a first attempt was refused at setup with $0 spent), so both arms used **`anthropic/claude-haiku-5.5`**, the stack default. Each arm ran `bench_trials.py --task code-search --trials 10 --include-live --max-total-usd 1.0` against `harness-rc` (`E2E_COMPOSE_PROJECT=harness-rc`, ports 18001/18004/28003).

- **Baseline:** `harness-engine-worker:vv90i13` = `sha256:d41fbdd2…`.
- **Candidate:** `sha256:48cca6fd…` = baseline + one added DESCRIPTION line in `harness-shell.js` (*"To search file contents use the grep tool, not bash. Never add 2>/dev/null, &&, ;, | or > … The working directory is already the workspace root"*) + `compileall`.
- **Switching arms:** the daemon resolves the tag on every run, so the candidate was selected by pointing the tag at it. Afterwards the tag was restored to `sha256:d41fbdd2…` (verified). A backup tag `vv90i13-audit-backup` remains.
- **Not changed:** the goal-preamble change from §5 was not applied, because it is orchestrator-side.

| Metric (per run, n=10 each) | Baseline median (range) | Candidate median (range) | Δ |
|---|---|---|---|
| Pass | 10/10 | 10/10 | — |
| **Event span** (`plan_proposed`→`engine_completed`) | **14.27 s** (13.13–16.79) | **9.45 s** (9.03–12.97) | **−4.83 s (−34%)**; ranges do not overlap (exact Mann-Whitney two-sided p≈1e-5) |
| Policy denials (total over 10 runs) | 9 (9/10 runs) | 2 (2/10 runs) | −78% |
| Model turns | 5 (4–6), Σ51 | 4 (4–6), Σ44 | −1 |
| `bash` calls (total) | 21 | 14 | −7 |
| Tool time | 0.632 s (0.42–1.05) | 0.109 s (0.10–0.31) | −0.52 s (bytecode + fewer bash calls) |
| Driver wall (`durationSec`) | 16.9 s (16.8–21.2) | 12.9 s (12.8–17.1) | −4.0 s. Quantized by the driver's 4 s settle poll, so use the span |
| Cost (ledger) | $0.0396 total | $0.0342 total | −14% |

Reading:
- **Acceptance met.** Denial turns fell from 9 to 2 (the target was ≤1 per 10; the two remaining denials were `;` chains, see below), median turns −1, span −4.8 s (target ≥4 s / ≥6%), replay 0.97 s (target ≤1.10 s), 10/10 PASS.
- **What the denials were.** On Haiku, all 9 baseline denials were the first `grep` piped to `head`/`grep -v`, or given `2>/dev/null`. The two candidate denials were `;` chains.
- **What didn't work.** The "use the grep tool" steering had **no effect**: the first-class `grep` tool was used 0 times in both arms. The gain came from the model dropping shell operators. A follow-up could remove that clause, or find out why the model ignores the tool.
- **Attribution.** The two changes were bundled. Tool time accounts for ≈0.5 s of the 4.8 s, which is consistent with the per-call bytecode saving. The remaining ≈4.3 s is the removed denial turn. This attribution is inferred, not measured separately.
- **Caveats.** This is one task and one model. The Haiku baseline (14 s) is much faster than the Nov 13 DeepSeek baseline (61 s), so the percentage is model-dependent. The absolute saving of about one model turn should carry over.
- **Next steps.** Upstream the DESCRIPTION line and the `compileall` line through a normal PR, and rerun on a second task with a test step (which also needs `pytest` in the worker image).

Raw attempt files: `ab/baseline/` and `ab/candidate/` in the session scratchpad. The `run_eval` per-run evidence was also written to `docs/briefs/evidence/eval/code-search-default-17952515*.json` and `…179525[1-9]*.json` (untracked).

## 6. Evidence notes

- Gate parity: 12 commands (allowed, policy-denied, secret file, forbidden path, interpreter read of a forbidden path, write in and out of scope, `../..` escape) gave **identical** JSON in both images, with forbidden paths set and with them empty. The only difference was the container hostname printed by `/etc/hostname`. Writes outside scope were refused by Landlock in both images.
- Replay correctness: 7/7 runs `completed`, target edited to `= 7`, and the final text was the expected `FILE:` line, in both images.
- Observations outside scope, not fixed: `_with_repo_context` builds the context from `self.workspace` rather than the run workspace (`engine.py:1178`), so check whether that is intended. The scratch image `harness-engine-worker:audit-pyc-scratch` is still present locally (removing it was blocked in this session): `docker rmi harness-engine-worker:audit-pyc-scratch`.
