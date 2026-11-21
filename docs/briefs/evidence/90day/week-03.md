# Week 03 — engineering delivery and independent acceptance

Updated 2026-11-12. Base checkout: `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5`,
branch `w28-s1-delegate`, with uncommitted changes. This is not a clean CI-certified release.
The user assigned test execution to another agent; no pytest, full gate, paid model run,
security drill or rollback/restore test was executed by the build agent in this completion pass.

## 1. Scope and states

| Work | Engineering delivery | Acceptance still required |
|---|---|---|
| P-06a / A-01 | Corrected shell-scope inventory and exact test handoff in `../../../pilot-remediation/A01-DISPOSITIONS-2026-11-11.md` | Independent negative-test results; signed scope/host-policy decisions; no security risk accepted here |
| P-06b / A-06 | RC-specific Compose topology, frontend build-address inputs, project-filtered tag report, image build/deployment work | Final image snapshot below; independent smoke, security, restore and release review |
| Regression coverage | Six new test functions across the existing deployment-isolation and stack-tag test files | Separate test agent must execute them; not represented as passing |
| P-05 preparation | SOW template, deployment-priority draft, rename candidates and outreach kit | Actual signed partner SOWs, D-01 decision and qualified trademark review are not supplied by engineering |

**Engineering delivery is complete; independent test/security acceptance is pending.**
The capture at `2026-11-12T12:26:11Z` is recorded in §3 and
`image-manifest-2026-11-12.json`. Older W24 demo runs do not prove the RC.

## 2. Implementation

- `deploy/docker-compose.rc.yml` runs the RC alongside the existing demo, not over it.
  RC run volume: `harness_rc_runs`; networks: `harness_rc_default` and
  `harness_rc_sandbox_egress`; store and database volumes are project-prefixed.
  Worker-daemon and orchestrator agree on the run-volume name and RC egress-proxy container.
- RC host endpoints bind only to loopback: UI `3011`, APIs `18001/18002/18004/18005/18006/18008`,
  model service `28003`. Port `18003` was refused by Docker as in use; no other process was stopped.
- The RC database has no published port, external archive target or writable backup bind.
  Archive mode is off for this scratch deployment. This is not backup/restore evidence.
- `deploy/frontend.Dockerfile` accepts ten public URL build arguments. Defaults preserve
  the original deployment; the RC overlay supplies its own API and WebSocket endpoints.
  These are non-secret URLs. Next.js requires them at build time, not merely at container startup.
  Optional context/onboarding APIs on `18011/18010` are not deployed by this slice; do not demo
  those surfaces as working.
- `scripts/ops/stack_tags.py --project harness-rc` filters Docker by Compose project, avoiding
  collisions with same-named services in the old demo. Its existing zero exit-code behavior
  is unchanged: inspect the report and image IDs, not exit status alone.
  **Open defect:** `scripts/ops/test_stack_tags_exit.py::test_main_should_exit_nonzero_when_docker_ps_fails`
  is marked `xfail(strict=True)` — it asserts `main()` should exit non-zero when `docker ps`
  fails, but the script deliberately exits 0 (docstring: "this script always exits 0"). This
  tension is recorded as an open defect, not resolved; the zero exit is by design, so the code
  and the xfail marker were left unchanged.
- Tests were added for project filtering/default behavior, RC storage/network separation,
  loopback endpoints, backup-target separation, and frontend build-argument wiring.

## 3. Observed deployment results

- The prior background attempt lost Docker connectivity and did not establish RC success.
- Docker Desktop was restarted; the existing demo containers resumed and were not stopped
  or migrated by this completion pass.
- RC migrations completed with exit code 0. Read-only database query returned `73|0075`.
- RC database `SHOW archive_mode` returned `off`.
- First isolated boot failed at the model-service host port `18003`; overlay and frontend
  were updated together to use `28003`.
- Frontend build compiled successfully with Next.js 15.5.23; build-integrated type checks ran.
  The pytest suite and independent browser/agent acceptance suite did not run.
- After moving the model-service port, Compose `up --wait` exited 0. The project-filtered
  report found 11/11 backend services with tag `vv90dw3`; frontend and daemon share that tag.
  Healthcheck-equipped containers were healthy; the daemon has no configured healthcheck.
- Both RC `http://localhost:3011` and the existing demo `http://localhost:3010` returned HTTP 200.
- Limited browser checks: invalid email was rejected, synthetic local sign-in reached Command
  Center, observed requests used identity `18001` and models `28003`, and console warnings/errors
  were absent. SMTP is unconfigured. Existing `HARNESS_REQUIRE_OWNERSHIP=false` was inherited:
  this is development authentication, not OTP/security acceptance. The UI's `live` badge must
  not be interpreted as proof of live-model mode; the backend is explicitly `dry`.
- The engine-worker build initially failed on Debian HTTP DNS/timeouts while installing
  Chromium. Its build-only package transport was changed to official HTTPS with three retries
  and 30-second HTTPS timeouts; package signature checks and runtime confinement are unchanged.
  The HTTPS rebuild succeeded. All four application images are now present at `vv90dw3`.
- The JSON manifest records complete image IDs, Docker-reported RepoDigests, container mounts,
  network/port bindings, non-secret mode settings, migration state and source-file SHA-256s.
  No registry push occurred; reported RepoDigests do not establish remote availability.
- Five sampled deployed engine/sandbox source files match their working-tree bytes. This is
  source correspondence evidence, not a claim that every dependency or file has been audited.
- Authenticated daemon `/version` was reachable from the RC orchestrator and returned code
  digest `fcd71949c00b21de9353e37d5589626720ceb95f31cd7e8c9df986f5e731db14`.
- Independent static review found no blocker in the RC merge/build-argument wiring. The overlay
  is for the default services plus `worker` only: enabling other profiles needs another review,
  especially WAL-G services which retain their base-file backup destinations.

## 4. Reproduce without touching the working demo

From the repository root, Docker running, Compose >=2.24.4 (for `!override`), using the
existing private `.env` without printing it. Do not enable WAL-G, load-test or other profiles:

```sh
export HARNESS_IMAGE_TAG=v90dw3
export ENGINE_LIVE_MODE=dry
export COMPOSE_PROFILES=worker
rc() {
  docker compose -p harness-rc --env-file .env \
    -f deploy/docker-compose.yml -f deploy/docker-compose.rc.yml \
    --profile worker "$@"
}
rc config --quiet
rc build
rc up -d --no-build --pull never --wait --wait-timeout 150
python3 scripts/ops/stack_tags.py --project harness-rc
```

Compose adds its own `v` prefix, so this existing build label resolves to `vv90dw3`.
Record full image IDs; matching tags alone do not establish source identity. Do not use
`latest`, silently substitute W24 images or claim registry digests when only local IDs exist.

RC starts in dry mode deliberately: no paid call and no fake model success. A tester may
switch this RC to live only after approving provider, budget, data and run scope, and must
record that configuration and each real run ID. Do not disable confinement or approval gates.
The original live W24 demo remains separate at `http://localhost:3010`.

To stop only the RC after testing: `rc stop`. Leave volumes intact. The old demo needs no
rollback because it was not replaced. Schema rollback is not achieved by starting an older
image on this RC database; a restore drill requires a separate scratch database and approval.

## 5. Test-agent handoff — execute and attach results

### Local regression checks

```sh
./section1/.venv/bin/python -m pytest section1/tests/test_deploy_volume_isolation.py -q
PYTHONPATH=scripts/ops ./section1/.venv/bin/python -m pytest \
  scripts/ops/test_stack_tags.py scripts/ops/test_stack_tags_exit.py -q -rxXs
./section5/.venv/bin/python -m pytest \
  section5/tests/unit/test_shell_gate_real_boundary.py \
  section5/tests/unit/test_shell_gate.py \
  section5/tests/unit/test_landlock_read_allowlist.py -q -rxXs
./section4/.venv/bin/python -m pytest \
  section4/tests/unit/test_agent_tree.py section4/tests/unit/test_delegation.py -q
```

The shell-boundary fixture uses `/private/tmp` and Darwin-specific xfail marks; see the
A-01 document before Linux execution. Preserve failures and report their causes. Do not
turn a failing security assertion into a passing result by weakening its expectation.

### Exact-deployment checks

1. Compare container `.Image` IDs against the JSON snapshot; inspect full health/missing-service
   state. `stack_tags.py` remains an informational report, not a security gate.
2. Resolve Compose configuration locally without exporting secrets. Check no RC service mounts
   `harness_runs` or joins `harness_default`/`harness_sandbox_egress`; no non-loopback host ports.
   The worker-daemon still has the documented root-equivalent Docker socket: this is NOT a
   separately isolated host daemon, and G3 remains open.
3. In a separate browser origin/profile, open `http://localhost:3011`; verify API requests use
   RC ports, not `8001–8008`. Check login, invalid/expired authentication, reconnect, console
   errors, and unchanged original demo availability. Context/onboarding are outside this slice.
4. After explicit live-run/budget approval, use synthetic code: plan → human approval → worker
   run → actual file diff → test output → review/acceptance. Check denial, cancellation and
   resume paths. No fake completion or replay passed off as live work.
5. Record SQL `schema_migrations` state and cost-ledger/event evidence. Bytecode-cache files
   alone do not prove tests ran or passed; require command, exit code and test output.
6. Execute reviewed Linux boundary probes and a scratch restore/rollback drill; preserve all
   failures. The current source tests and W24 history do not certify this RC image set.

### Results to be supplied by test agent

**Supplied 2026-11-21** (test lane; host macOS Darwin 25.6.0 arm64, Docker Desktop
29.5.3, VM kernel 6.12.76-linuxkit; worktree at `160dd30`/`ff112dd`). Every command
below ran with its own exit code; nothing was weakened to pass.

#### Local regression checks (§5 block 1) — all green

| Group | Command | Result |
|---|---|---|
| deploy volume isolation | `pytest section1/tests/test_deploy_volume_isolation.py -q` | **9 passed** |
| stack tags | `pytest scripts/ops/test_stack_tags.py scripts/ops/test_stack_tags_exit.py -q -rxXs` | **13 passed** (0 xfail) |
| shell gate boundary trio | `pytest section5/tests/unit/test_shell_gate_real_boundary.py test_shell_gate.py test_landlock_read_allowlist.py -q -rxXs` | **57 passed, 4 xfailed** — xfail causes recorded, not weakened: two printed rows are rule-7 expansion-form gaps (`grep -rn *` and `python3 -c` reads outside scope), pinned as known limitation |
| agent tree + delegation | `pytest section4/tests/unit/test_agent_tree.py test_delegation.py -q` | **189 passed** |

Cross-ref: full section suites at the same revision in week-02 §5 R1–R7
(section1 1326/0, section4 2392/0, section5 542, section8 104, section7 1201 + build).

#### Exact-deployment checks (§5 block 2)

1. **Container image IDs vs `image-manifest-2026-11-12.json`: MISMATCH — the RC has drifted.**
   `python3 scripts/ops/stack_tags.py --project harness-rc` now reports **`Agreement: MIXED`**:
   `vv90i13` on 10 services, `vv90r4` on frontend + orchestrator, and **identity + review run an
   untagged image** (`abdc28b02505` — same bytes as the manifest's `vv90dw3` identity/review,
   but the tag no longer points at it). Only those two match the 2026-11-12 snapshot. The
   manifest is superseded; a fresh one-tag-set deploy + new manifest is required for any
   release claim (A-06, owner). All 14 RC containers healthy/up.
   **→ RESOLVED 2026-11-21 (same day, vv90r6):** PR #24 merged to master (`11d2e7a`), the
   stack was rebuilt and redeployed from the post-merge tip, and `stack_tags.py --project
   harness-rc` now reports **`Agreement: AGREE (vv90r6)`, Missing: none**, schema `74|0076`,
   identity on `harness-backend:vv90r6` (untagged image gone), guest gate 401 live, frontend
   200. Manifest: `image-manifest-vv90r6-deployed.json`. This finding is closed.
2. **Isolation: verified.** Merged `rc config` resolves clean; **no RC service mounts the demo
   run volume** (the overlay renames the logical volume — `name: harness_rc_runs`), **no
   service attaches** `harness_default`/`harness_sandbox_egress`, and **every published port
   carries `host_ip: 127.0.0.1`** (YAML-level check, not line grep).
3. **Frontend wiring: verified (static half).** `harness-rc-frontend-1` serves HTTP 200; its
   baked `NEXT_PUBLIC_*` endpoints are exactly the RC ports (18001/18002/18004/18006/18008/
   28003, WS 18001) — no 8001–8008. A full browser origin/profile walk remains open.
4. **Live-run checks: NOT RUN** — owner-gated (provider/budget/data approval per §4).
5. **Schema state:** RC db `73 migrations, max 0075` — one behind HEAD (0076, P-17), consistent
   with the drifted images. Cost ledger: no live RC runs (dry mode), so no spend to record.
6. **Linux boundary probes: executed 2026-11-21** — see week-02 §5 drill rows (D1 native-Linux
   PASS in CI at 19f5cae; G1/G2 gaps confirmed live with evidence JSONs). **Scratch
   restore/rollback drill: not run** — owner-gated (separate scratch DB + approval per §4).

#### A-01 independent negative-test results (P-06a)

Executed against BOTH gates; matrix + dispositions:
`docs/briefs/evidence/pilot-prep/a01-deployed-gate-matrix-2026-11-21.json` and
`a01-dispositions-2026-11-21.md`. Headline: HEAD denies all 8 probe shapes
(`OUT_OF_SCOPE`/policy) with the argument checks pinned by 25 tests; the deployed tag
vw24b1 predates them — four shapes (traversal read, tilde, parent-dir, outside-workspace
read) pass its decision layer, one demonstrably **executed** a read outside the workspace.
Closing the deployed side = the A-06 redeploy from a post-`e24b830` revision.

#### P-06 acceptance verdict (fleet)

- **A-01**: negative tests executed on both gates; dispositions written per item. Code side
  closed at HEAD. Deployed side open until the A-06 redeploy.
- **A-06 one reviewed image set, digests recorded**: digests recorded (backend
  `0cc42686…`, worker-daemon `e59ed0c6…`, frontend `380c2b98…`, db-walg `59c74c6f…` for the
  demo set; RC set IDs captured in this section) — but the **one-set/no-drift requirement is
  NOT satisfied today**: demo = AGREE `vw24b1` (an old set), RC = `MIXED`. The owner's
  one-tag-set redeploy + fresh manifest is the remaining move.
  **→ MET 2026-11-21 (vv90r6):** the RC runs one reviewed set (`harness-{backend,frontend,
  engine-worker,worker-daemon}:vv90r6`, IDs in `image-manifest-vv90r6-deployed.json`), AGREE,
  schema 0076, gate byte-identical to HEAD, pack route live. The 5-point verification is in
  that manifest; week-03 finding #1 above is closed.

#### P-05 (owner, unchanged)

SOW template exists as a counsel-review draft; signed SOWs, D-01 decision, qualified
trademark clearance remain owner actions (§6) — no fleet lane can supply them. Record host/kernel/backend, timestamp, exact image IDs, source patch hashes,
commands, exit codes, raw outputs, run IDs, spend, failures, skips and XFAIL/XPASS diagnostics.

## 6. Owner gates that engineering cannot complete

- Two signed design-partner SOWs, consent and named support contacts (P-05).
- D-01 deployment-priority decision; the draft is not an approval.
- P-D3 qualified name clearance; previous generic searches did not establish availability.
- Scope-read semantics and pilot-host policy reconciliation with the legal checklist.
- Independent security/test acceptance and release go/no-go.

No commits, pushes, partner messages, customer-data uploads or signatures were made in this pass.
