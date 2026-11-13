# Week 02 evidence ledger — 90-day plan (Nov 18–24, 2026; opened early 2026-11-11)

Plan: `docs/plans/90-day-execution-plan-2026-11-11.md`. Baseline: `w28-s1-delegate` @ `7c1acf4`.
States per FUTURE PLAN §2. Testing executed by the test agent only (§3). Demo prototype work
(task #7, owner request 2026-11-11: working prototype for accelerator demo video) tracked in §4.

## 1. Task states

| Task | Lane | State | Evidence |
|---|---|---|---|
| P-04 sandbox review + drill prep | Fleet | **Implemented—unverified** | `p04-sandbox-review-2026-11-11.md` (48KB, 5-backend boundary table + engine-worker row, 14 gaps, drill map, §8 handoff). Spot-check by R-lane fleet: G1 confirmed at `confinement.py:446` (`allow_network` no-op). October Landlock fixes present: `confinement.py:480-498` (/bin/sh), `348-352` (HOME=/). |
| P-03 SOW template | Fleet draft | **Implemented—unverified** | `docs/legal/PILOT_SOW_TEMPLATE.md` (W1 ledger) |
| Demo prototype (accelerator video) | Fleet | **In progress** | §4 below; runbook reuse: `docs/demo/yc-demo-runbook.md` (verified Nov 1 on w24b1/w24b2) |
| P-09…P-12 pilot tasks | Owner-gated | Not started | Blocked on signed SOWs (P-05) |

## 2. Owner security decisions surfaced by P-04 (feed F-05 threat model)

| Gap | Severity | Pilot impact | Decision needed |
|---|---|---|---|
| G1 Landlock network no-op (`confinement.py:446`) | High | Pilot is macOS-only (Seatbelt denies network) → pilot unaffected; **Linux expansion blocked** until container boundary or ABI≥4 net rules | Fix path vs named risk acceptance for any Linux pilot |
| G2 process-mode read exposure (HOME, `/proc/<pid>/environ` → runner token on BYO) | High | BYO/bare-host pilots only; engine container path unaffected | BYO host policy: dedicated credential-free uid or container-only BYO; negative drill first |
| G3 worker-daemon root + host Docker socket | High (architectural) | Demo/pilot on single-purpose host | Owner risk-accept by name for pilot; isolated/rootless daemon before production |
| G4–G14 | Med–Low | See review §7 | Batch disposition with F-05 |

## 3. Test-agent handoff (W2) — run, record, do not fix

From review §8 (commands there are authoritative). Record results in §5 with date, host,
revision, raw output.

| # | Drill | Host | Purpose |
|---|---|---|---|
| D1 | `./scripts/native_linux_drill.sh` | Linux (ubuntu; builds harness-backend:drill if absent) | Re-verify October fixes at current revision; probe fail-closed |
| D2 | G1 negative: Landlock-confined allow-listed python attempts outbound socket with `HARNESS_SANDBOX_ALLOW_NETWORK=0` | Linux | Confirm gap live (expected: succeeds → gap confirmed; record, do not fix) |
| D3 | G2 negative: shell-gated child reads `/proc/<gate-pid>/environ` for `HARNESS_RUNNER_TOKEN` | Linux + macOS BYO path | Confirm token exposure; record |
| D4 | `section5` suite incl. untracked `test_shell_gate_real_boundary.py` | macOS | A-01 disposition input |
| D5 | Demo hero journey verification (§4) | macOS | Accelerator video evidence |

## 4. Demo prototype log (accelerator video)

Integrity rules inherited from `docs/demo/techstars-local-demo.md` and the runbook:
plan text is a **fixed template** (W24 finding 4) — never present as model-generated unless a
current rehearsal proves otherwise; seeded web demo is a **replay**, never "live"; label any
cut; record date/revision/image tags/model/run IDs/outcomes; no credentials on screen.

| Attempt | Date | Stack | Provider | Journey | Run ID | Outcome |
|---|---|---|---|---|---|---|
| boot-1 | 2026-11-11 | project `harness`, w24b1 + frontend w24b2 | OpenRouter (key auth OK, ~$4 credit) | **FAILED at migrate**: drift gate exit 37 — DB has 73 migrations, image declares 71 (0073/0075 unrecognized). Safety system worked. | — | Recorded in env-manifest §1b |
| boot-2 | 2026-11-11 | project `harness-demo` (fresh DB), w24b1 + frontend w24b2 | same | 11/11 healthy, AGREE; first run failed: worker-daemon needs `--profile worker` | run-c74e1d87b479 | Honest failure; fixed |
| run-2 | 2026-11-11 | harness-demo + worker-daemon w24b1 | OpenRouter claude-haiku-5.5 | **Full web journey VERIFIED**: propose→approve→live run→complete, 20 events, files + tests created | run-a230d8306753 | **$0.003521 ledger; temperature.py + tests verified** |
| run-3 | 2026-11-11 | same | same | **Pause→redirect→resume VERIFIED**: paused seq 41, direction sent, resumed seq 42, power.py created per direction, engine_completed seq 60 | run-248e9a4f4ea8 | **$0.010075 ledger; 40 events; calc package verified** |

**Full evidence + corrected boot commands + video integrity rules: `demo/verification-2026-11-11.md`.**
Demo prototype task: web journey verified; VS Code segment re-rehearsal pending (historical W24 evidence exists).

Voiceover check vs P-04: runbook line "no network beyond what's allowed, no access outside the
repo" is accurate for the **engine-worker container path** (per-run internal network, DNS kill,
workspace-only binds). If a run uses the Seatbelt process path instead, soften to "writes
confined to the workspace; every tool call recorded" (Seatbelt reads are broad, G2). Verify
which path the demo run uses from its events before recording.

## 5. Test-agent results (paste below)

_(empty — awaiting test agent)_