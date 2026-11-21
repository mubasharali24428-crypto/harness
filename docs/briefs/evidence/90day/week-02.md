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

## 5. Test-agent results (2026-11-21, w28-s1-delegate at efb5238 + 27405a4/e12890c)

All suites re-run on the macOS host, Docker Desktop 29.5.3. Recorded by the
verification lane; R marks nothing here Verified.

| # | What | Result |
|---|---|---|
| R1 | section1 full suite (SQLite) | **1326 passed, 0 failed**, 1 skipped — the 4 `test_runs_index_tuning.py` failures recorded in the P-17 packet are fixed at `e12890c` (scratch schema now mirrors migration 0001: `agent_events.payload`, `agent_plans`; statement budget pins the post-9d4fab2 shape) |
| R2 | `make pg-probe-s1` | **1326 passed, 0 failed** (was FAIL) — the P-08 pg-probe gate is green |
| R3 | `make pg-probe-migrate` | PASS: 74 migrations (0001→0076) clean on the live PG, re-run idempotent |
| R4 | section4 full suite | **2392 passed, 0 failed**, 60 skipped |
| R5 | section5 suite (excl. live-docker) | 542 passed, 8 skipped, 4 xfailed — drill item D4's base evidence |
| R6 | section7 `tsc --noEmit` + `vitest run` + `npm run build` | clean / **1201 passed** / exit 0 |
| R7 | section8 suite | 104 passed |
| R8 | P-17 portal verification | packet re-run 8/8; result + attack list: `p17-verification-result.md` (guest-gate anchors hardened to `\Z`; repeated-`projectId` consistency pinned; 18 portal tests green) |
| R9 | Nightly backup repair | first green run since 2026-11-09: `nightly-backup RESULT=ok offsite=NONE 2026-11-21T07:40:45Z` (store verify ok=174/0/0). Root causes were environmental (daemon down, live db stopped then network-detached by a failed 5499 bind — force-recreate fixed it); nightly.sh now fails with the root cause. OPEN owner items: demo db binds 127.0.0.1:5499 (live db's port — cannot run simultaneously), `OFFSITE_RSYNC_TARGET` still unset (every backup copy is on this machine) |
| R10 | Env manifest live capture (was Blocked 2026-11-11) | addendum written: 11/11 demo services AGREE on vw24b1, RC frontend vv90r4, live backend services still down (owner redeploy call) |

Drill status vs §3 — **D1–D4 executed 2026-11-21** (P-04 acceptance evidence):

| Drill | Host / kernel | Revision | Verdict | Evidence |
|---|---|---|---|---|
| D1 native-Linux container drill | ubuntu-latest, **kernel 7.0.0-1020-azure**, image `harness-backend:drill` built in CI from `deploy/backend.Dockerfile` (base python:3.13-slim@sha256:e48f59…) | **19f5cae** (pushed branch tip; CI workflow run 44882932634, dispatched by hand) | **PASS** — `"result": "pass"`, all four checks green (write through shell gate, workspace read, sibling read denied, outside write denied); CI run overall **completed / success** | `native-linux-container-drill` artifact (docs/briefs/evidence/pilot-prep/native-linux-container-drill.json on CI) |
| D1b Landlock pytest subset on Linux | Docker Desktop VM, **kernel 6.12.76-linuxkit** (real Linux kernel, `landlock_supported: True`) | 160dd30 | **42 passed** — the Landlock halves actually executed; the two October regressions `test_exec_read_roots_never_grant_the_filesystem_root` and `test_home_root_never_grants_the_filesystem_root` ran and **passed** | this run; command = review §7.1 block 2 |
| D2 G1 negative (Landlock allow-network) | Docker Desktop VM, kernel 6.12.76-linuxkit | 160dd30 | **GAP CONFIRMED live**: production `confine_argv(allow_network=False)` child connected to example.com:443. Documented limitation (`confinement.py:446`); pilot unaffected (macOS Seatbelt denies network); G1 owner decision standing | `pilot-prep/g1-landlock-network-noop-2026-11-21.json` + `probes/g1_probe.py` |
| D3 G2 negative, macOS halves | macOS Darwin 25.6.0, Seatbelt via production `confine_argv` | 160dd30 | **Split**: environ side-channel `ps eww` = PROTECTED-BY-ABSENCE (confined and unconfined control both empty — Linux `/proc` half still needs the Linux host); **$HOME canary READ-GAP confirmed** — a workspace-confined, network-denied child read `$HOME/.harness-g2-canary` in full (Seatbelt read list grants HOME, `confinement.py:335`). G2 owner decision standing (BYO host policy) | `pilot-prep/g2-process-mode-macos-2026-11-21.json` + `probes/g2_probe.py` |
| D3b w21 `--mode off` baseline | macOS | 160dd30 | baseline captured: every outside-write probe false, "outside workspace changed": false | `pilot-prep/w21-mode-off-baseline-2026-11-21.json` |
| D4 confinement + real boundary suite | macOS | 160dd30 (test committed at e24b830) | **34 passed, 1 skipped, 4 xfailed** (`test_confinement.py` + `test_shell_gate_real_boundary.py`); sibling workspace neither readable nor writable, shared system temp not writable | R5 + this run |
| D5 demo hero journey | — | 2026-11-11 runs (run-2, run-3) | **Already verified 2026-11-11** (web journey + pause/resume, ledger costs recorded in §4); the VS Code segment re-rehearsal remains open | §4 rows |

**Correction to the R5 note of 2026-11-21 (morning):** `test_shell_gate_real_boundary.py`
was never untracked-and-missing — it is tracked, committed at `e24b830` (2026-11-14);
the "not in the tree" wording came from the review's pre-commit state. D4 runs green.

**P-04 acceptance state:** the review's drills are executed and recorded; the named
holes (G1, G2) are now **confirmed live**, not just argued — both remain
**risk-acceptance decisions for R by name** (week-02 §2), which is what the
acceptance line ("holes fixed or risk-accepted by name") requires of the owner.
The Linux `/proc` half of D3 and the D5 VS Code re-rehearsal remain open; the
intended-host (cloud box) D1 rerun is unnecessary for the drill itself (CI =
native Linux at the exact revision) but the §7.2 live-stack drills still need the
owner-approved compose `worker` stack.

## 6. UI overhaul F1–F10 audit (2026-11-21, code-level; no browser run)

Against the 2026-11-14 findings (`ui-overhaul-plan-2026-11-13.md` §8.2):

| Finding | State | Evidence |
|---|---|---|
| F5 model picker (127 dup options) | **Fixed by design** | `ModelPrefs.tsx`: provider-grouped, type-to-filter picker ("a bare <select> ... is unusable past the first screen"); same model name from different providers is a real choice, not a duplicate |
| F6 runs footer 0–0 of 0 / UUID column | **Resolved by redesign** | the runs table page is gone — the list is the shell sidebar (`3557e00` carries goal/plan id into it); no footer exists to mis-render |
| F9 cost headline / BY DAY twice | **Fixed** | `66e5bbd` "the cost page leads with one number; unused prReview removed" |
| F10 settings bullet list | **Fixed** | `d3d62e8` settings section nav |
| F8 shell chrome | **Partial** | new shell shipped (`bb12334`); nine-items → grouped nav needs a browser walk to claim done |
| F3 run page admin-console tab | **Partial** | three-pane target in progress (`RunColumn.tsx`, composer spec `01fa370`); the 3,607-px tab needs a re-measure on the new shell |
| F1/F2 demo review data + diffs | **Open — deployment** | demo stack runs `vw24b1` backends + review data path; needs the owner's demo redeploy and a real review/PR, not code |
| F4 demo stack shows old UI | **Open — deployment** | image tag flip is the owner deploy call (A-06 §5.3); RC frontend is `vv90r4`, demo frontend still old |
| F7 internal ids as headings | **Open — needs browser walk** | code search shows no remaining "Run {id}" page heading, but "walked" evidence requires the browser pass |

No browser run happened this week; nothing here claims F1–F10 are closed on screen.