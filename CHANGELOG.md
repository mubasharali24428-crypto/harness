# Changelog

All notable changes to the AI Engineering Harness are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
entries below are backfilled from `STATUS.md` + git history (Mission 12 G4
docs-truth lane, 2026-08-23) and extended through Missions 15–23 by the
Mission 23 / Z3 polish lane (2026-08-26). Migration numbers refer to files in
`section1/migrations/`.

## [Unreleased]

### W10 (in progress) — a browser inside the run boundary; the "agent sees it" half is blocked on the model (2026-10-27)

* **Browser tool, confined.** `browser` (screenshot/dump) runs headless Chromium
  inside the run's own container, through a gate that confines every write to the
  workspace, refuses `file://` outside it, sends remote pages through the same
  web-purpose proxy as `web_fetch`, scrubs the environment before spawning
  Chromium, and caps renders by wall clock and bytes. Mounted per run beside
  shell/web; absent (not degraded) when either file is missing. Chromium is in the
  worker image stage only. 15 gate tests + 2 mount tests; proven with real
  Chromium on the host and inside the worker image with its real confinement
  flags. (`browser_gate.py`, `harness-browser.js`, `worker.Dockerfile`)
* **What the live probes showed.** The agent itself took a screenshot through the
  product; a page on a fresh internal network gets `FETCH FAILED` with no proxy,
  `FETCH OK` for an allow-listed host through the proxy and `FETCH FAILED` for an
  unlisted one; an 11 MiB upload is `413 ARTIFACT_TOO_LARGE`; the agent's CSS fix
  was recorded as one reviewable change; sibling-run reads answer `not found`.
* **What is NOT shown — the W10 gate stays open.** The agent never saw its own
  screenshot: the runtime refuses images for a text-only model
  (`does not declare image input`), the OpenRouter account is out of credit
  (`total_credits 5`, `total_usage 5.198` — a run at 18:41 worked, the same call
  at 18:49 answered 402), free image models either rate-limit (`429`) or emit no
  tool calls, and the host's Ollama has no vision model. The before/after
  re-verification is therefore unproven; `docs/week-10-plan.md` records the exact
  messages and how to resume.

### W9 slices 1–3 — share links that are capabilities, deployments bound to the reviewed revision (2026-10-27)

* **Shares are capabilities, not signatures.** `POST /runs/{id}/artifacts/{artifactId}/shares`
  mints a random token, stores only `sha256(token)`, and `GET /shares/{token}` — the
  one anonymous route in the product — serves the bytes with
  `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: sandbox; default-src 'none'`, always as
  `application/octet-stream`. There is no signature to verify and no key to leak:
  revocation is a row write, so a revoked token is indistinguishable from an
  unknown one (404), an expired one is 410, and a deleted artifact is 410.
  Uploads the classifier quarantines never get a link at all. Opening a link is
  audited as the artifact's team, not the anonymous reader.
  (`0063_artifact_shares.sql`, `shares.py`, 12 tests)
* **A deployment approval names the revision a human reviewed, and stops being
  valid when it moves.** `POST /runs/{id}/deployments` binds `changes_digest` +
  `run_head_sha` + `plan_version` (+ `base_sha`) as they stand at request time;
  approve re-reads the current revision and answers `409 DEPLOYMENT_STALE` with
  `staleFields` when any part moved, or when the client echoes a different
  digest than the request was bound to. No changes → `NOTHING_TO_DEPLOY`, no
  recorded head → `REVISION_UNKNOWN`, a non-canonical environment name is
  refused rather than silently rewritten (`ENVIRONMENT_INVALID`). Revocation is
  final and idempotent: an approve after revoke is `DEPLOYMENT_REVOKED`, a second
  approve is `DEPLOYMENT_DECIDED`. Every step writes the binding into `audit_log`.
  Nothing here deploys anything; the record is the guarantee.
  (`0064_deployments.sql`, `deployments.py`, 13 tests)
* **Both gates pass on the live stack.** `scripts/e2e/w9_share_deploy_drill.py`
  — PASS, 21/21 (`docs/briefs/evidence/pilot-prep/w9-share-deploy-1793124898.json`).
  A token opened with no `Authorization` header serves the exact bytes as an
  attachment with `nosniff` and a sandboxing CSP; a foreign team gets 404 from
  mint/list/revoke while the token alone still opens it; revoke kills that same
  token (404) and is idempotent; a 60s link answers `410 SHARE_EXPIRED` after a
  real minute. A deployment request binds digest + head + plan version; a wrong
  client digest and, after the head moves, a correctly-bound request are both
  refused `409 DEPLOYMENT_STALE` (the second stays `requested`); revoke is final
  (`DEPLOYMENT_REVOKED` on any later approve). All six actions are read back
  from `audit_log`.

### W8 slice 2 — a container-mode run can finally write, and abandoned boundaries get a reaper (2026-10-27)

Three defects, all found on the live stack, none visible in review:

* **`EACCES` on every write.** The daemon launches the worker as uid 10001 while
  the run's own workspace is root:root 0755, so `write` failed, the run settled
  `blocked` with zero changes, and *no* container-mode run had ever produced a
  reviewable diff. Handing the directories over
  (`confine.claim_run_dirs_for_worker`) was necessary but not sufficient: the
  engine re-creates a `volume-subpath` mount as root:root 0755 at mount time
  (measured `pre 10001 → during 0`, `pre 0o2777 → during 0o755`).
  `confine._mount()` now binds the named volume's own engine-host directory
  under `HARNESS_RUNS_ROOT` — verified with `WRITE OK` and the directory left
  10001:10001 — and keeps `volume-subpath` as the fallback when the mountpoint
  cannot be resolved (`HARNESS_RUNS_BIND=0` forces it).
* **The boundary sweeper could not see a running container.**
  `_running_containers` matched `" running "` against a line that ends with the
  state, so everything looked stopped — on a path that deletes other runs'
  networks. The listing is piped and parsed positionally now, pinned by a test.
* **`agent_runs.state` is not the durable lifecycle.** It only ever holds
  `idle`/`pr_opened`; the states the API reports are replayed from
  `agent_events`. Boundary settlement is therefore decided by the event log
  (`service._settled_run_ids`), not that column.

New: per-run networks are labelled `harness-run=<run id>` (an unlabelled orphan
is untraceable, so nothing may be deleted on a guess);
`container_runtime.sweep_orphan_boundaries` with its two proofs — durable
settlement, or nothing attached still running — never touching a held run, an
unlabelled network, an unproven one, or the shared egress proxy (disconnected,
not stopped: it serves the runs beside this one); `service.recover_boundaries`
on the worker's own cadence, plus a DB-free pass in the socket-holding daemon.
**Both live gates pass**, taken against `deepseek/deepseek-v4-flash` through
OpenRouter ($0.05/$0.09 per M tokens) because the host's Ollama no longer has
`qwen-local:latest` — the model is configuration, so this changes nothing but
the provider named in the evidence:

* `w8-concurrency-1793123190.json` — PASS, 10/10. Three runs `executing` at
  once in one project, three distinct boundary networks, one run directory per
  worker and none shared, each run recording exactly one change with its own
  digest and checkpoint manifests containing only its own file.
* `w8-crash-cleanup-1793123310.json` — PASS, 11/11. The victim's launch client
  is SIGKILLed mid-run: the run settles `blocked`, its claim row is gone and the
  audit says why. A launcher-shaped orphan (network + relay + still-running
  container, no owner) is then built with the product's own boundary classes and
  the process that made it is killed; the sweep — given durable proof for that
  run only — stops the container and removes the network, while the live
  neighbour stays in `kept` with none of its containers stopped.

Two further defects surfaced only because the writes started working: re-owning
*every* run directory broke the orchestrator's own staging pass (it must read
what the agent wrote, as the uid that provisioned the tree), so the handover now
claims root-owned directories only; and the drill measured itself wrong twice
(mount destination `/workspace` instead of the real per-run path, and a
one-line-JSON assumption that turned two checkpoint manifests into "0").

### W7 (IDE gate) — an in-editor edit round-trips through the gate, and the bytes reviewed are the bytes written (2026-10-27)

Live evidence `w7-ide-1793116802.json`, verdict PASS on ten checks, driven by
the extension's own core against the running stack: the stored plan goal
carries the editor's selection (`@src/note.py:1-2` plus the line itself); the
run executed in `harness-engine-worker:vdev-local` with a read-only root,
`CapDrop: ALL` and only the run's **internal** network attached; both diff
stages reached the editor; a review submitted against bytes other than the
displayed patch came back **409 CHANGE_STALE from the server**; the approved
change carries the digest the client verified before showing anything; and the
file on disk is exactly the reviewed bytes.

Two claims this does **not** make. The server's event list for the run is
`plan_proposed, plan_approved, done_signal_received, review_passed` — no
`execution_started`, no per-tool rows — because execution is off-host and no
endpoint lets a client mint events it did not observe; confinement is evidenced
by the container, not by an invented audit row. And the `vscode` adapter is
typechecked and built, but no editor window was opened here; the evidence file
says `drive: … vscode adapter typechecked, not opened`.

Fixes found by building the drill: the launcher adapter discarded every complete
NDJSON line it parsed, turning a refusal into "exit 2" with the reason thrown
away; and the worker's network isolation was being read from the container's
endpoint block, which never carries `Internal` — it reported an internal network
as not internal. Isolation is now asserted against `docker network inspect`.

### W4 (credentials) — the worker's environment was dumped on purpose, and no secret is in the record (2026-10-27)

`w4_credential_leak_drill.py` takes the deployment's real secret values (never
printed, never written into evidence), runs a confined worker whose goal is to
dump its own environment, and greps every stored surface: the folded engine
result, the run's comments, the 600-char audit view and **the full
`agent_events` payloads** — the truncation is exactly where a secret would sit.
PASS (`w4-credential-leak-1793114924.json`): one real environment dump, three
secret names searched, zero hits, because `sanitize_environment` strips them
before the shell ever execs.

Getting it to mean something cost two corrections. The drill used to pass on a
run that never exercised the exposure path — "no hits" is not evidence if the
tool never answered — so it now requires a completed turn whose tool result
actually *is* the dump. And the first such run produced a `tool_call` with **no
`tool_result` row**: NUL-separated output made the JSONB insert fail
(`asyncpg … \u0000 cannot be converted to text`), `_publish_safe` logged it and
dropped the event. That hole is fixed above (commit 4b2f300); the kept evidence
file for the failing run records it.

W4's four gate claims are now each backed by evidence. The honest remainder is
named in the plan: the MCP leg is proven against a first-party stub, and a
reviewed **third-party** server still waits on a node toolchain in the worker
image, with the npm install lane permitted-but-undrilled for the same reason.

### W7 (IDE) — an in-editor edit goes through the plan gate, the done-signal and the change review, in that order (2026-10-27)

`clients/vscode` is a client of the control path W6 built, not a second one:
context builder, diff applier, run controller, typed-SDK transport, VS Code
adapter, activation. Everything that decides anything sits in `src/core` with no
`vscode` import, so the policy is tested against fakes instead of described.

The flow the gate demanded turned out to have **two** approvals, because the
product has two: the operator approves the *plan*, and the operator approves the
*result* (`POST /runs/{id}/done-signal`) — and that second one is the only thing
that makes a diff exist server-side, which is what `/changes` and
`review_change(diffSha)` bind to. So the editor shows the engine's proposed diff
labelled *proposed*, asks for confirmation, records it, then shows each file's
recorded patch labelled *reviewed* and takes approve/reject per file. Declining
either step leaves nothing recorded and writes nothing.

Bytes are the contract, and the refusals are the feature: a patch that does not
hash to the digest is never displayed; drifted context is applied only on a
unique match, never a guess; binary, symlink, rename-only and
patch-omitted changes are refused; a file that changed after its diff was shown
is not overwritten; a `@`-context path that climbs out of the workspace is
rejected before it reaches the wire; and a context block over the cap says in
the goal itself which items were dropped or truncated, so "what the model saw"
stays answerable from the audit.

`awaiting_done_signal` is a settled state — the engine finishing is not the run
finishing — and this was learned twice: once by reading `service.py`, once by
watching a test poll a deadline that would never arrive. Building it also
required rebuilding `clients/ts/dist`, which was 12 files stale against its own
`src` (no `run_diff`, `list_changes`, `review_change`).

Not claimed: the live drill is written but unrun, and the `vscode` adapter is
typechecked and built without a window ever being opened in this environment.
`docs/week-7-plan.md` states both plainly rather than implying a desktop test.

### W4 (installs) — permitted only under the boundary, and the proxy keeps the record (2026-10-27)

`pip install` is no longer blocked by name alone. `policy.install_permission()`
grants the install verbs only when the operator flag is set **and** the run is in
`container` mode **and** `HTTPS_PROXY` names the egress sidecar; anything less
returns a refusal that says why. The engine injects `HTTP(S)_PROXY` through
`confine.install_env()`, passed by value because a proxy is a destination, not a
secret.

The gate asks for an audit, and the proxy did not have one: it now prints one
line per tunnel that actually opened (`CONNECT pypi.org:443 purpose=sandbox
allowed`). Denials are not logged — the compose healthcheck probes with a denied
CONNECT every 10s — and a test pins both halves.

Deterministic drill (`w4_install_drill.py`, `w4-install-1793111223.json`), the
real gate inside a container on a gateway-less run network, no model: no flag →
denied with the reason; flag + container + proxy → `Successfully installed
six-1.16.0`; same with an off-list `--index-url` → `Tunnel connection failed:
403 Forbidden`; proxy audit shows exactly `pypi.org` and
`files.pythonhosted.org`. The npm/npx lane is permitted by the same function but
undrilled (the worker image has no node toolchain), and a warm package cache is
still absent — both stated rather than assumed.

### W4 (MCP) — a run used an MCP tool, and the audit names the server and the tool (2026-10-27)

The reviewed list is configuration, not input: `harness_orchestrator/mcp.py`
reads `DSH_MCP_SERVERS`, a JSON file **baked into the worker image**, so admitting
a server is a rebuild rather than something a running service can swap. Validation
is the boundary — stdio or https only, pattern-checked names, unknown keys
refused, `failOnStartupError` forced true, and **no `env` at all** on a spawned
server (decision 3: servers get no credentials; a call-time broker is the later
design). A declared-but-malformed list fails the run instead of degrading to
"no servers", because a half-applied boundary looks exactly like an intact one.
Neither the plan nor the model has a channel to it; a test asserts that a plan
asking for `{"mcpServers": [{"command": "curl"}]}` still mounts only the
reviewed entry.

Live (`w4_mcp_client_drill.py`, `w4-mcp-client-1793109830.json`): an HTTP-driven
run settled `awaiting_done_signal` with `agent_events` rows named
`mcp__harness-stub__stub_echo` and `mcp__harness-stub__env_probe`. The returned
marker appears nowhere in the goal, so it can only be in the transcript if the
call actually crossed the server process. The first-party stub is deliberate:
the mechanics are proven against a server whose behaviour we can read before any
real third-party server is reviewed — and the third-party leg still waits on the
install path, since the worker image has no installer.

Building it also surfaced two shipped defects, each fixed with tests in the same
change: the run-network leak below, and the fact that operator `DSH_*`
configuration never reached a worker (`--env KEY` resolves its value from the
Docker client's own environment — the daemon's, not the orchestrator's).

### W4 (leak): every confined run leaked its network until Docker could not run anything (2026-10-27)

Found while drilling MCP wiring: a run failed in two seconds with
`docker network create --internal … returned non-zero exit status 1`, and running
that command by hand answered **"all predefined address pools have been fully
subnetted"**. The stack held 20 orphaned `harness-<id>-net` networks, the oldest
eight days.

The cause was W4's own egress work: `ContainerBoundary` connects the shared
`harness-sandbox-egress` proxy to each run network so the worker has exactly one
route out, but `close()` removed only the relay and the network — and a network
with an attached container cannot be removed. So *every run that reached the
proxy* leaked a network permanently, and nothing noticed until the pool was
full. Two tests now pin the order (`disconnect` before `network rm`, and no
disconnect when no proxy was attached); the 20 orphans were reclaimed by hand
with the same operation, after confirming each held nothing but the proxy.

The larger honesty note: no earlier drill caught this because nothing in the
stack asks "did the boundary clean up?" — the failure surfaced weeks later as an
unrelated-looking start error. A cleanup claim needs a reclaim check of its own,
not just a `close()` that exists.

### W6 (local) — the same engine, confined by the container boundary, against a local directory (2026-10-27)

Slice 4, designed from the audit before any code: seatbelt is not an option
(the engine refuses it — sandbox-exec breaks the dsh PTY — and fails closed
unless the mode is `container` or explicit `off`), and the worker daemon's
security law forbids caller-shaped paths, so local mode reuses neither. The
decision is recorded in `docs/week-6-plan.md`: **local engine, server gate**.

`harness run --local DIR` proposes and approves on the server exactly as a web
run does, deliberately does NOT call `start_run` (the queue only claims
EXECUTING runs, so no phantom server execution and no double-run), then runs
the turn through `deploy/runners/local_engine.py`: the orchestrator's own
`DeepSeekHarnessWrapper` with `isolated=True`, launched by
`confine.wrap_command(mode="container")` — same worker image, same internal
network + model relay + egress proxy. The agent works on a seeded copy; the
diff is produced INSIDE the boundary (read-only bind of baseline and run
tree) and applied back file-by-file. The launcher refuses, with an actionable
message and no spawn, when docker, the image, live credentials or the
container mode are missing — it never degrades to `off` or seatbelt.

Audit, stated honestly: the plan gate is the server's (identical events), the
patch is uploaded to the raw-body artifact endpoint and a run comment
references the plan that authorised the run — carrying `executed=local`,
confinement, image, the diff digest and the artifact id in its body.
Tool-call-level events from an off-host engine cannot be minted into
`agent_events` by any existing endpoint; that is a contracts decision, not a CLI
workaround — so the worker's own NDJSON progress lines are now shown to the
operator instead of being parsed away, which is what let the next defect be seen
at all.

The live drill corrected three more claims from the first pass, each now pinned
by a test: the generated `upload_artifact` stub sends **no body**, and the
endpoint ("the request body IS the file") answers `EMPTY_ARTIFACT` — the trace
had been failing silently; the transport grew a raw `content=` path and the
patch bytes now actually land. A comment reference must resolve to something the
discussion knows: `refKind: diff` means a high-impact *approval* row, not an
artifact id, so pointing at the artifact was rejected as unavailable — the
comment references the plan instead, verified `201` against the live API. And
the drill itself read a comments list without checking its status code and under
the wrong key, so a 404 looked like an empty but valid list.

Earlier in the same drill, the applier had treated "absent from the change
list" as "deleted", which would have removed every untouched file in the
workspace; only the boundary diff's own `+++ /dev/null` headers may delete now,
and paths had been corrupted by a fixed-offset slice in the header normaliser.

Status after four live attempts, the last one green (`w6-local-1793107805.json`):
the server showed `plan_proposed, plan_approved` and no `execution_started` (a
local run never becomes a server execution), `present.txt` appeared in the user's
directory while the untouched file stayed byte-identical, the diff artifact was
read back and held the patch, and the provenance comment landed with
`executed=local` + digest. What the evidence deliberately does not claim: the
worker was killed by the engine deadline at 900 s after writing, so this is the
boundary, apply and trace proven end-to-end, not a turn that ended on its own
terms — and the created file is empty, so the apply holds regardless of how well
this local model writes content. An over-specified one-command goal made it reply
`DONE` without calling any tool, and the CLI reported `artifact: skipped (the run
produced no diff)` instead of inventing a change.
`harness chat --local DIR` joins `run --local`: the directory is stored with
the session, so `--resume` picks the same workspace back up.

### W6 (chat) — an interactive loop that says exactly what it remembers (2026-10-27)

`harness chat` is slice 3: an interactive loop over the SAME run path — every
turn is a normal plan-gated run (`propose → approve → start`), `--yes` covers
all turns only as an explicit operator decision, and a refusal rejects that
turn's plan and ends the loop rather than letting the next message assume an
approval that never happened.

The session identity the plan demanded, answered without inventing a server
object: the engine remembers exactly the history block `compose_goal` renders
into the next goal — it travels on the wire inside `propose_plan`, so it is
auditable, not implied — and the transcript lives in a local session file
under the credentials permission contract (0700 dir, 0600 file, atomic
replace). The transcript stores the plain message per turn; the rendered goal
is stored alongside it because that string is what the server actually saw.
History is capped newest-first at 4 KB, so the oldest exchanges drop first —
a bound, stated in the code, not a silent truncation.

Live smoke (`scripts/e2e/w6_chat_smoke.py`, `w6-chat-1793092724.json`): two
scripted turns against the stack; turn 1 shows the same event sequence as a
`harness run` (plan_proposed, plan_approved, execution_started,
engine_completed); turn 2's wire goal provably carried turn 1; session file
permissions checked on the real store. `--message`/`--resume`/`--continue`
make chat scriptable, so the drill needs no pty.

There is no server-side conversation object, and chat does not pretend there
is one; server-remembered sessions would be a contract decision, not a CLI
workaround.

### W6 (CLI) — the same task completes from a terminal, with the same audit trail (2026-10-27)

Week-6 slices 1–2: `harness_sdk.cli` ships a `harness` console script with
`login`/`logout`, `run`, `status`, `diff` — commands only, every call still goes
through the SDK transport so the CLI cannot drift from the contracts.

The plan's four claims, each with evidence:

* **Same gate, same events.** `scripts/e2e/cli_parity.py` runs one goal through
  `harness run` and the same goal over HTTP, then compares the persisted
  `agent_events` types: identical sequences, identical settled states
  (`w6-cli-parity-1793085128.json`, runs `run-0e4b25c7f895` /
  `run-0f515e97495a`). Like the W4 drills, every run writes its own evidence
  file, so a rerun cannot overwrite the record a claim was made from.
* **`harness diff` is the server's diff**, read through `run_diff` — the CLI
  formats it; it never recomputes one.
* **Offline token storage**: `$XDG_CONFIG_HOME/harness/credentials.json`, dir
  `0700`, file `0600`, atomic replace, `HARNESS_TOKEN` overriding; the OTP login
  path reads the dev code from identity logs.
* **`--json` is exactly one object** on stdout, progress to stderr — subprocess
  tests, not a string dump.

Building the drill found and fixed three real problems: `argparse` crashed every
command but `login` because the service-url flags were only defined there (unit
tests injecting a client could not see it); `POST /plans` returns only a
`planId` — the run binding comes back from APPROVE, so assuming a runId failed
against a live server while stubs passed; and Docker Desktop's compose log API
served a ~17-hour-old buffer while `docker logs <cid>` was current, which made
working OTP delivery look broken (`compose_journey.fetch_otp` now reads the
container first). The bracketed-`NO_PROXY` sanitiser from contracts 1.0.0rc5 is
mirrored in the SDK transport, since the CLI runs on the developer's machine
with that variable set.

Chat and local-workspace mode are not started: the gate stays open.

### W3 closed — an interrupted write, a rewind, and a second attempt (2026-10-26)

The last open clause was "resume reconciles partial writes". A new drill
(`scripts/e2e/w3_partial_write_drill.py`, `w3-partial-write.json`) pauses a run
while `slow_writer.py` is provably writing, reads a genuinely torn file back
(28 of 120 lines), rewinds to the before-attempt checkpoint — which removes the
file, because it did not exist then — and resumes, producing a clean second
attempt.

Building it exposed a real defect: the attempt number lived in RAM, so a resumed
run restarted the count and the checkpoint history read "before attempt 1" twice.
It now comes from the checkpoint store, and a test pins `1 → 2` across a resume.
Also fixed: the drill's `docker top -eo cmd` form fails without a PID column and
still exits 0, which had the process check silently seeing nothing.

### Contracts 1.0.0rc5 — one outbound client factory, not four copies (2026-10-26)

httpx 0.28 turns a bracketed IPv6 `NO_PROXY` entry (`[::1]`, which Docker Desktop
exports) into a mount pattern whose port parses as `:1]`, so *constructing* the
client raises `InvalidURL`. Section 1 fixed it locally for SSO; the whole-project
test run then showed onboarding (4 tests) and the MCP client (3 + 15 errors)
failing the same way.

`harness_contracts.http_client` now owns the factory (`env_client`, plus a
`sync_env_client` twin) as an optional extra, since sections may import contracts
but not each other — three copies of a security-relevant HTTP client is exactly
the drift the isolation guard exists to prevent. The retry strips brackets only
when the environment actually carries them, restores the environment afterwards,
and re-raises any other `InvalidURL` untouched. Contracts bumped to `1.0.0rc5`
with the TS mirror header regenerated in the same change; Section 1's module is a
re-export shim. Onboarding 91 passed and MCP 18 passed **with the bracketed
variable still set**.

Also corrected in the test report: Section 11's failure is not this defect. Five
repetitions each way gave 1/5 with `NO_PROXY` and 0/5 without, on a
`chunksIndexed` 8-vs-7-rows race unrelated to httpx — recorded as its own item.

### W3 (rewind) — a run workspace can go back to a checkpoint (2026-10-26)

"Revert to the last checkpoint" was a policy with nothing behind it: a run
executes in its own directory on the `harness_runs` volume and nothing
snapshotted it. Now every execution attempt leaves a `before attempt N` and an
`after attempt N` checkpoint, and `POST /runs/{runId}/rewind` restores the tree
to one (default: the newest). Rewind is exact — files are written back and files
created after the snapshot are removed — and it is refused with 409 while the run
has a live task, because resetting files under a worker that is still writing
them would race the agent.

Checkpoints are manifests plus content-addressed blobs under
`DSH_WORKSPACE/.checkpoints/<runId>/`, outside the run's own tree so the agent
cannot edit its own undo history. Not git: the orchestrator image has no `git`
binary, and adding one for this is a build dependency the feature does not need.

Also: the resume-status caveat no longer tells operators to "review the workspace
checkpoints" — it says side effects are not rolled back and resume continues from
whatever is on disk, which is the behaviour the engine actually has.

### W1 (M07) — both remaining acceptance legs, driven over HTTP (2026-10-26)

M07's gate asked for a run started through the API and a screenshot visible
through the container boundary. Both now exist as drills against the deployed
stack, with no host-side engine call:

- `scripts/e2e/m07_api_run_drill.py` — login, `POST /projects`, `plan`,
  `plan/approve`, `start`, then status/engine polling; the worker is snapshotted
  live (`999:999`, read-only root, `cap-drop ALL`, private network, two
  volume-subpath mounts) and the fixture repo is verified on disk after the run
  (`m07-api-run.json`).
- `scripts/e2e/m07_vision_api_drill.py` — uploads a PNG as a run artifact, sets
  `maxToolCalls` *before* the first call, and reads the model's answer. Two runs
  with two different solid fills both came back correct (`orange`, `purple`) in
  12.53 s / 22.59 s with zero tools used (`m07-vision-api.json`,
  `m07-vision-api-purple.json`). The deployment was returned to the local model
  afterwards; the provider key was read from `~/.harness-secrets` and never
  printed.

Found on the way: `GET /runs/{runId}/engine` replays a settled run without
model or usage (recorded in `docs/backlog.md`), and the local `.env` named a
model the configured Ollama does not have.

### W2 (N04) — retry with changes is a visible control, not a model ladder (2026-10-26)

The backend already recorded guidance and resumed runs; what was missing was the
affordance, so an operator facing a stuck run could only press Resume and hope.

#### Added

- **`resumeWithChanges(runId, content, token)`** — guidance is recorded first,
  then the run resumes. The order *is* the feature: the executor drains guidance
  through `guidance_since` when it starts, so resuming first would read nothing
  and silently discard the correction — a working-looking button with no effect.
  Empty content is refused before any request rather than degrading into a plain
  resume (Resume already exists; a second button for it is decoration), a denied
  redirect never resumes, and the content is trimmed so whitespace cannot become a
  meaningless guidance row.
- **"Resume with changes" in Controls** — visible only while a run can resume
  (`paused` / `blocked`), accessible name on the input, disabled until something
  is typed, and honest copy: guidance the agent reads before it continues, not a
  chat message and not a restart.

#### Limits stated

- This is not a silent model retry: no model is switched. It continues the same
  session with new guidance. A checkpoint rewind is Week 3's work.

#### Tests

`section7`: `resumeWithChanges.test.ts` (4), `controlsResumeWithChanges.test.tsx`
(6), both written failing first. tsc clean, **1003 tests across 91 files**,
`next build` green.

### W2 (N02, N03) — the context was assembled and then dropped (2026-10-26)

The runtime's own context injector is off in the shipped profile
(`includeRuntimeContext:false`) and that key is the SDK's, so what this product
controls is the goal it hands the engine. Two blocks it was already assembling
never got there.

#### Added

- **`repo_context.py`**: a bounded baseline — pruned file tree, README excerpt,
  `git status --short` when the workspace is a repo, and the dependency manifests
  present. Prepended by `execute_goal`, which means the direct SDK call and the
  isolated worker cannot drift on what the model was told about the repo.
  `HARNESS_REPO_CONTEXT=0` disables it.
- `HARNESS_REPO_CONTEXT` is injected **once per session**: an SDK session
  continues across a resume, so a second copy is a duplicate the model pays to
  read again, not extra context. A different session still gets its own.

#### Fixed

- `run_data["rules_prompt_block"]` never reached the executor. Its loader comment
  already said it was stored "so … future engine calls can carry it into system
  context"; they did not. Project rules are now in the engine call, before the
  curated context and the goal.
- `run_data["context_bundle"]["rendered"]` — the bounded @ref bundle, documented as
  riding "on the run for engine prompt assembly" — also never reached the engine.
  Same fix, same ordering.

Both stay advisory: a blank block contributes nothing rather than an empty
heading, so a run with neither gets the prompt it always got (pinned by test).

#### Limits stated

- The empty case is deliberate: a workspace with nothing recognisable returns
  `""`, because an empty scaffold is worse than no scaffold.
- **Not yet demonstrated:** the gate's other half — a real task in a 500-file repo
  finding the right files unprompted. That needs a live run, which is what M07 is
  blocked on.

#### Tests

`section4`: `test_repo_context.py` (8), `test_repo_context_wiring.py` (6),
`test_n03_context_to_executor.py` (5). Ruff clean, `make static-gates` green.

### W2 (N01) — a stuck run is now caught from the event stream (2026-10-26)

Two live acceptance attempts made 14 and 37 tool calls against a one-sentence
goal and were stopped by nothing but the wall clock. The budget was not missing —
it was late. `remaining_tool_calls` computed `maxToolCalls` from
`COUNT(*) FROM agent_tool_calls`, and `record_engine_tool_calls` only fills that
table *after* the engine returns, so every run started with its full allowance and
never lost any. Native `tool-fs` calls are not bash calls, so the shell plugin's
own counter never saw them either.

#### Added

- **`loop_guard.py`**: a pure, DB-free guard observing the W1 transcript stream —
  the one place every call passes on both execution paths. Two independent
  stops: `loop_detected` (same tool, whitespace-normalised same arguments, four
  times) and `tool_budget_exhausted` (distinct calls over the cap, because 100
  different commands is a burn even if nothing repeats). Results are not counted;
  once stopped it stays stopped, so it cannot report a stop and then let the run
  continue.
- **Service wiring**, on both the live forwarder *and* the `finally` drain that
  previously emitted records without consulting the guard. On a stop the run
  fails durably with `ENGINE_FAILED`, the shell policy's `maxToolCalls` is
  written to zero so further bash calls refuse, and the worker's late
  `completed` is discarded instead of overwriting the failure.
- `HARNESS_LOOP_REPEAT_LIMIT` tunes detection; unset means the default (4), and
  an explicit empty/`off` means disabled. "No config" falling through to "no
  detection" is how the budget became decorative.

#### Limits stated, not smoothed over

- **The worker is not killed.** There is no cancel channel into the SDK session;
  that is Week 3's spike. A stopped run keeps consuming until it ends on its own.
- `tool_budget_exhausted` is covered by unit tests but **not** at the service
  seam — the fixture has no `run_limits` row, so `budget` arrives as `None`.
- The wiring tests were written after the wiring, so they were mutation-checked:
  disabling `guard.observe` fails 3 of 4 of them.

#### Tests

`section4`: `tests/unit/test_loop_guard.py` (12) and
`tests/unit/test_loop_guard_wiring.py` (4). Ruff clean, `make static-gates` green.

#### Verified live (2026-10-26) — M00 and M04

**M00 (stack health on the current revision) closed.** Both images already on
the host (`vdev-local`, `vbuild-1025`) were checked first and contain none of
this week's code, so they could not have produced current-revision evidence.
`harness-backend:vm00-1026` and `harness-frontend:vm00-1026` were built from
`df4096e`: 61/61/61 migrations, `migrate` exit 0, 18 containers healthy, journey
13/13, `CONNECT github.com:443 → 200` and `CONNECT example.com:443 → 403` on that
image, and a forced sidecar stop producing `[FAIL] container health` before
recovering.

**M04 (live transcript from the confined worker) closed.** A real worker in a
real `container` boundary on a worker image built from this tree streamed two
call/result pairs — `read` at 34.85 s, `write` at 48.89 s — against a 68.42 s
completion, with `notes.txt` on disk containing exactly what the goal asked. No
spend (local Ollama), and `HARNESS_ENGINE_CONFINEMENT=off` was never set.

#### Added

- **`deploy/patches/openrouter-vision.patch.yml`** — the same transport patch
  with `input: [text, image]`. It is committed and baked into the worker image
  because a confined worker can only read `DSH_PATCH_FILES` from inside its own
  image: the previous approach wrote the widened declaration to a host temp path
  and the worker died before reaching any model. A modality claim that has to be
  reviewed and versioned is the right shape anyway.
- **`scripts/e2e/m04_streaming_smoke.py`** — timestamps every `on_progress`
  record against the run clock and asserts arrival-before-completion, which is
  the one M04 claim unit tests cannot make. Same money guard as the sibling
  smoke.

#### Fixed

- The M04 smoke's own classification bug: it filtered records on `kind`, while
  the worker's transcript records use `phase`. It reported 0 calls on a run that
  had made two — a probe that silently under-counts is worse than no probe.

#### Known

- **M07 was attempted and failed.** The confined screenshot run attached the image
  (`goalImagesAttached: 1`) and streamed correctly — 74 records, 37 correlated
  pairs, first at 13.45 s — but made 37 `bash` calls in 516.75 s, never returned
  the colour, and ended in an empty model response at the deadline. No retry: the
  discipline is one attempt, and re-running a runaway would have spent again
  without becoming measurable.
- **That run cannot be priced.** `usage` is `null` on the openai-completions
  transport, so 37 paid calls yield no cost figure from evidence. Metering is now
  the thing that would let a runaway be stopped, not merely reported.
- **Tool choice varies by model on identical policy**: `qwen-local` used native
  `read`/`write`; `qwen/qwen3.7-flash` used `bash` 37 times and never the file
  tools.
- Journey check 07 still reports the engine result as `replayed`, not live; the
  journey proves the stack, never a live run.
- The first trip-test attempt PASSED with the sidecar stopped — `ps -a` was read
  before the daemon reflected the exit. Detection is fine; the test needs a
  settle.
- `compose_journey` and the confined drills run on Docker Desktop here;
  Landlock-backed writes remain a native-Linux matter (`d04752e`).

#### Verified live (2026-10-26)

`scripts/e2e/openrouter_smoke.py` drives the shipped engine against a real key
(one attempt, `max_tokens` pinned, `HARNESS_ALLOW_PAID_SMOKE=1` gate — it
refuses to spend otherwise). It completed a tool-using run with two live tool
calls and a real file side effect, and got `qwen/qwen3.7-flash` to read the
colour of an attached PNG back as `(220, 40, 40)`. Three facts it settled: the
profile registers one LLM route and OpenRouter needs
`deploy/patches/openrouter.patch.yml`, not a provider env var; a model declared
`input: [text]` makes the runtime silently drop the image and say so; and
providers refuse tiny images (`8x8 must be larger than 10`), so fixtures must
be real screenshots.

#### Not proven, and why that is the honest result (2026-10-26)

The same smoke re-run with `--isolated` against local Ollama — free, no provider
spend — failed in **0.0 s with zero tool calls**: the confined worker refuses
because Seatbelt breaks the runtime's PTY and no container boundary was up. That
is the guard doing its job. Setting `HARNESS_ENGINE_CONFINEMENT=off` would have
produced a green run proving only that an unconfined worker runs, so it was not
set and the refusal is recorded as evidence
(`m07-ollama-isolated-refused.json`). **Superseded the same day**: the container
boundary turned out to be reachable and M04 has since streamed live through it —
see *M00 and M04* above. What the refusal established still stands: bypassing
confinement to obtain evidence was correctly refused, and the direct path was the
only proven one until a real boundary existed.

Reading that output also exposed a displayed number that is not the model's:
the status panel shows `DSH_CONTEXT_WINDOW` (1,000,000 by default) while the
transport patch declares `contextWindow: 65536` for the model in use. **Closed in
`a5148e0`** — a window is now reported only when an operator set one, and the run
page's never-before-reachable "window unknown" branch is what an unset variable
produces. The profile default was indeed not proof of the window, exactly as Day 4
predicted and the roadmap's no-fake-affordances rule forbids shipping.

### W1 — a screenshot can travel with the goal (2026-10-26, contracts 1.0.0rc4)

`readImage` was already on and the runtime already admits image content blocks,
but nothing in this product ever attached one: "fix this UI, here's the
screenshot" was impossible.

#### Added

- **`goal_images.py`**: a run's verified image artifacts become SDK content
  blocks (`{"type":"image","data":<canonical base64>,"mimeType":…}`) at
  execution time. Selection is bounded twice — our own
  `HARNESS_GOAL_IMAGE_MAX_BYTES` / `_MAX_COUNT` / `_TOTAL_BYTES` and the
  runtime's own image limits — and every image that does not travel is
  reported with a reason. `HARNESS_VISION_MODELS`, when configured, refuses
  before the call rather than paying for a rejection the runtime would file
  anyway; `HARNESS_GOAL_IMAGES=0` turns the path off.
- **Upload→model path** in section 7's composer: the picked file is stored via
  the existing artifact route once approve mints the run id (no new endpoint,
  no invented contract), then the engine hands it to the model at start.
- **Contracts `1.0.0rc4`** (additive): `goal_images`, so "what did the agent
  actually see" is durable and replayable rather than process memory. The
  section 7 status panel renders attached/skipped/refused from that event.

#### Fixed

- A resumed run re-attached the same image into a continuing SDK session
  (duplicate attachments). The first attempt of a session now owns the image.
- **Review-found, test-confirmed:** the team filter was appended *after*
  `ORDER BY`, which made the SQL a syntax error on the first real run — the
  module-level tests could not see it because they never went through the
  service. Service-level tests now pin the keyword actually reaching the
  engine, the refusal-before-the-call, and the resume case.
- `teamId` is a process stamp, not a durable column, so a replica that never
  saw the bind would have selected nothing and said nothing. It now resolves
  through the same durable witness the budget gate uses, and reports
  `teamScoped: false` when only run scope is available.
- Artifact bytes were read synchronously on the event loop; they now go
  through a worker thread, and an expired-but-unpurged image is no longer sent
  after the artifacts API has already 410'd it.

### W1 — the transcript is live, and compaction is visible (2026-10-26)

The SDK emits turn events, but this wrapper dropped them: a run showed a
spinner, then a final answer, with no evidence of what the agent actually did.
Contracts had no tool-event vocabulary at all.

#### Added

- **Contracts `1.0.0rc3`** (additive): `tool_call`, `tool_result` and
  `context_compacted`, with golden payloads and a regenerated TS mirror. The
  call/result pair is correlated by `(runId, callId)`; results that arrive
  before their call are buffered by the UI rather than dropped.
- **A live transcript path**: `execute_goal(..., on_progress=)` forwards SDK
  turn events on the direct path; the isolated worker emits NDJSON `progress`
  lines so the parent process sees them; the service drains a thread-safe queue
  onto the event bus (scrubbed before persistence). `ToolActivity` renders the
  stream in section 7 and pairs each result with its call.
- **Context visibility**: the state machine records the context window at
  execution start; compaction events are summed into `GET /runs/{id}` status,
  and the status panel shows compacted tokens.

#### Fixed

- **Optional engine keywords are probed, not assumed.** `on_progress` is now
  passed only when `execute_goal` accepts it (and `**kwargs` counts), and no
  forwarder is started for engines that cannot emit. Narrow test/custom engines
  previously failed the whole run with a `TypeError`; 32 section-4 tests caught
  this before it shipped.

### W1 — the agent can use files, and output is no longer capped (2026-10-25)

The shipped `sdk-minimal` profile turns the filesystem tool off (`read`,
`write`, `edit` all false) and the wrapper additionally pinned
`max_tokens=4096`. An agent could only touch files through bash, and every
answer was truncated at 4k tokens regardless of the model.

#### Changed

- The per-run policy patch enables `tool-fs` (`read`, `write`, `edit`,
  `readImage`) and `tool-fs-search` (glob/grep), so file work no longer costs a
  shell round-trip. This is safe only because the runtime confines the tool to
  the same `workspaceRoot` the shell gate enforces, and because
  `_fs_write_capability` keeps `write`/`edit` **off** when a plan has
  `forbiddenPaths` and the confinement mode cannot freeze them (Landlock and
  `none` cannot; container read-only mounts and Seatbelt can). The reason is
  written into the patch so an operator can see why writes were disabled.
- The hardcoded `max_tokens=4096` is gone: unset lets the provider decide, and
  `HARNESS_SDK_MAX_TOKENS` pins a cap for cost control (invalid values fail
  fast).
- Seeding a workspace no longer strips `.git`, so checkpoints and diffs work
  against real history instead of an undescribed tree.

### W1 Day 0 — the sidecar that was never running (2026-10-25)

`sandbox-egress` was crash-looping and every check passed anyway.

#### Fixed

- **The entrypoint ignored `command:`.** `deploy/entrypoint.sh` never read
  `"$@"`, so the egress sidecar (whose only purpose is
  `python -m harness_sandbox.egress_proxy`) booted the all-in-one branch —
  seven uvicorns plus a migrate job inside a 128MB, 0.25-CPU container — and
  died on the master-key gate. It now execs a passed command before the
  `HARNESS_SERVICE` branch, so a sidecar needs no service identity at all.
- **`sandbox-egress` had no healthcheck.** It has one now, and it tests
  policy rather than liveness: a `CONNECT` to a non-allow-listed host must
  answer `403` (no outbound network needed), so a dead listener, a broken
  allow-list or a wedged loop all read as unhealthy.
- **Readiness no longer implies reach, and never depended on it.** The sandbox
  `/ready` body reports `egress: up|down` when the proxy is configured and
  still fails only on db + schema drift — a run that needs no network must
  execute while the proxy is down, but `ready` must not be read as
  "network available".

#### Added

- `scripts/e2e/compose_journey.py` check 0: every service this stack actually
  runs (profiles applied) must have the container it should have — running,
  healthy where a healthcheck exists, and `migrate` exited 0. It reads
  `ps -a`, because the first version of this check read `ps` and therefore did
  not see stopped containers at all: stopping `sandbox-egress` by hand still
  passed. Verified both ways against the live stack (stopped → reported,
  restarted → clean). This is the check that would have caught 708 consecutive
  failed healthchecks behind a demo replay.

#### Found while verifying

- The database had migration `0062_team_model_prefs` applied while the running
  image declared 60, so identity / github / review / meeting / webhook-delivery
  were crash-looping on the schema-drift gate — correctly: the image predated
  the migration. A rebuild from the current tree clears it. The full image
  build is currently blocked on a 60 MB `deepseek_harness_runtime_bin` wheel
  downloading at ~28 kB/s from inside the builder (~100 kB/s on the host), so
  the live verification used a source-overlay image over the previous build
  (`harness-backend:vrollback-1024` is the untouched original tag).

### Tool-output protocol — structured diagnostics instead of scraping (2026-10-25)

`docs/tool-diagnostics-protocol.md`. Hermes' `hermesc --json` contract adopted
verbatim (roadmap H1): `{path,line,col,kind,message}` on stderr, the program's
output on stdout, never mixed.

#### Changed

- `StructuredExecResult.diagnostics` is a list of records, not strings — with
  `diagnostics_source` (`protocol` / `located` / `scan` / `none`) recording how
  the parser got them, and `diagnostics_truncated` when the 20-record display
  cap drops the rest. A record with no position says so instead of carrying a
  guess; `kind` defaults to `error` so an unknown severity is never softened.
- Diagnostics are read from stderr for the two structured tiers. Only the
  keyword `scan` fallback reads stdout, because test runners print their
  summary there — and the API now labels that case.
- `parse_output` (an unused alias flagged as an orphan in
  `docs/briefs/m17-findings-pbeta.md`) removed.
- Python and TypeScript SDK clients regenerated — this also picks up drift that
  predated this change: the router clients were missing `runId`,
  `ModelPrefsBody` and `unattributed` spend, and the sandbox client was missing
  the checkpoint hunk/restore operations already served by the API.

#### Added

- `render_diagnostics_json()` — the emitter half, so a tool that speaks the
  contract round-trips through the parser (`emit → parse` is an identity).
- A seeded corpus of nine tool outputs across all three tiers, with a test that
  asserts the corpus exercises every tier — the "validate the validator" step
  applied to the parser before it becomes a review gate.
- Terminal panel renders `path:line:col`, the kind and the message per
  diagnostic, plus a plain note when positions came from scanning.

### Provider registry — 65 providers, three wire protocols (2026-10-25)

`docs/providers.md`. Until now the router had one outbound target —
`DEEPSEEK_BASE_URL` — no matter which model was named, so the six "providers"
in the catalog were six labels on one URL.

#### Added

- `section3/src/harness_router/provider_registry.py`: 65 providers / 117 named
  models across 9 regions (15 frontier labs, 16 open-weight hosts, 12 Chinese
  labs, 14 gateways and clouds, 8 local runtimes), each with its documented
  base URL, wire protocol, auth style, key env and docs link.
- Endpoint resolution with documented precedence: a deployment gateway
  (`HARNESS_PROVIDER_BASE_URL`) → the provider's own `*_BASE_URL` → the
  historical `DEEPSEEK_BASE_URL` → the registry default → the deployment
  fallback for an unregistered provider. Existing installs and suites are
  unaffected; a gateway override forces the OpenAI shape.
- Anthropic (`/v1/messages`, `x-api-key`, system lifted out, `max_tokens`
  defaulted) and Gemini (`:generateContent`, model in the path,
  `x-goog-api-key`, role mapping) adapters next to the OpenAI one.
- `GET /api/v1/models/catalog` now carries label, protocol, region,
  open-weights, `requiresOwnEndpoint` and a `priced` list; the keys page turns
  the provider `<select>` into a searchable datalist over the registry, and
  Settings → Models gained a provider filter.

#### Fixed

- **Billing no longer guesses.** `PricingEngine` returns $0.50/$1.50 per
  million for any model it does not know, which at this catalog size would
  invent spend for free local runs and under-bill frontier models. The router
  bills an unpriced model at zero and logs the gap; the UI says "cost not
  tracked" instead of implying a rate.
- The provider-key endpoint accepts any valid key: `KeyProvider` was a
  four-value union in the UI while the backend validates only the provider
  *shape*, so 61 providers were unreachable from the page that stores their
  keys.

#### Known limitations

- The sandbox egress allow-list predates the registry: a new provider is
  routable from S3 but not necessarily from inside a confined run. Deriving
  that list is a network-policy change and needs sign-off.
- Teams cannot point a provider at their own base URL (Azure resource, Bedrock
  region) — still operator env only, deliberately, since a team-supplied URL is
  an SSRF surface.
- Only 15 of the 117 model ids have a real rate card.

### UI/UX audit Batch A (2026-10-25)

First batch from `docs/ui-ux-audit-2026-10-25.md` — the eight items that were
broken or misleading rather than missing.

#### Fixed

- **A1** — the run surface stopped quoting internal engineering notes to users:
  the diff panel now names the files the agent has actually touched (derived
  from the `diff_progress` events the sandbox already emits), the plan panel
  dropped its placeholder "Actual cost: — (no live source yet)" line, and the
  "Comments & nudges" panel became **Run notes**, labelled for what it is (a
  browser-local scratchpad) with a pointer to the real direction control.
- **A2** — `TerminalPanel` no longer claims "no live terminal feed" while
  transcript lines render directly underneath it.
- **A3** — light-theme contrast: pills, phase badges, run-state badges,
  settings links and breadcrumb separators moved onto theme tokens, and
  `pillStyle()` mixes its fill with `color-mix()` so token colours are legal at
  all (the old `${color}22` concatenation is what forced every pill to hardcode
  a dark-theme hex). A headless pass over 12 pages × 2 themes went from 15 AA
  failures to zero; `src/lib/themeContrast.test.ts` guards it.
- **A4** — the demo-replay banner (a nowrap `.pill` carrying a whole sentence)
  forced the run page to 489px at a 390px viewport; it wraps now, measured 0px
  overflow at 320/390/768.
- **A6** — ticking "Edit a file myself" used to POST a human edit against a
  placeholder path immediately, pausing the run and recording an empty edit;
  the checkbox now only reveals the editor and "Save edit" does the write.
- **A7** — the sidebar TEAM line is a read-only value, not a `<select>` with
  one hardcoded option and no handler.
- **A8** — a rejected model-preference save rolls the control back instead of
  displaying a model the server refused.
- **A9** — the review page distinguishes a missing PR (404 → empty state) from
  a failed load (message + retry) instead of hanging on "loading diff…", and
  "Request changes" requires a written note and sends it as the review comment
  rather than POSTing an empty array and reporting a recorded decision.

### Team model preferences and reasoning effort (2026-10-25)

#### Added

- **Per-team model choice, persisted** — migration `0062_team_model_prefs.sql`
  plus `GET/PUT /api/v1/models/prefs` (Reader may view, Writer+admin may save,
  unknown models rejected with `UNKNOWN_MODEL`, storage failure returns
  `503 PREFS_UNAVAILABLE` rather than silently accepting). Router `/call` and
  orchestrator runs both honour the team's primary model; deployment defaults
  still apply until a team saves a preference.
- **Composer model chip and Settings → Models** — `ModelPrefsForm` (compact in
  the plan-gate composer, full on `/settings/models`) lists the provider
  catalog, auto-saves for admins, and states plainly what the choice affects.
- **Honest reasoning limits** — reasoning effort is stored for every
  deployment but only wired into the request for OpenRouter-style endpoints;
  the server reports `reasoningWireSupported` and the UI says so instead of
  implying the setting always does something.

#### Known limitations

- Reasoning effort reaches only OpenRouter-compatible endpoints; other
  providers accept no reasoning object, so the setting is stored and ignored
  there (stated in the UI, not hidden).
- The reviewer model preference is persisted but not yet consumed by the
  reviewer lane.

### Mission 23 · Program finale — polish sweep (2026-08-26)

Final mission of the 10x program (graph:
`docs/briefs/mission23-finale-graph.md`): four lanes — program AAR +
scoreboard (Z1), bet execution (Z2), repo-wide polish sweep (Z3), release
readiness for the v1.0.0-candidate tag (Z4).

#### Added

- **Z3** — `docs/backlog.md`: consolidated future-work ledger (counsel-review
  placeholders from the M22 legal skeletons, frontend API-client
  consolidation) plus the kept-after-inspection inventory from the dead-code
  scan, so nothing flagged was silently dropped.

#### Removed

- **Z3** — provably-dead symbols (zero references repo-wide, tests included):
  `_error_text` (mcp tools), `_num` (S3 budget guard), `claim_owner` +
  its probe SQL (S4 run queue — superseded by the durable spawner election),
  `get_plan_gate_service` (S4), `get_sandbox_service` (S5), `focusRing`
  (S7 tokens), and the orphaned module `section7/src/lib/onboarding.ts`
  (superseded by `app/onboarding/onboardingApi.ts` since M10-B3).

#### Fixed

- **Z3** — broken internal links in `docs/runbooks/alerting.md`
  (`queue-depth.md` → `docs/engine-concurrency.md`; `eventbus.md` →
  `docs/runbooks/ops.md`). Link-checker now reports zero broken internal
  links across all 95 root+docs markdown files.

### Mission 22 · Commercial skeleton (2026-08-26)

License, pricing tiers, billing, SSO, and an MCP server — the platform can be
commercially operated without inventing product behavior (per-tier limits are
enforced in code, not just advertised).

#### Added

- **M1** — Legal foundation: MIT `LICENSE` (decision flagged for counsel),
  `TERMS.md`/`PRIVACY.md` skeletons with explicit `[PLACEHOLDER: counsel
  review]` markers (tracked in `docs/backlog.md`), `PRICING.md` mapped to
  real code caps.
- **M2** — Tier enforcement: `PLAN_TIERS` became behavior-bearing — Free =
  capped runs/day + index size, Pro = full, Team = extra seats + audit
  retention; enforcement chains BEFORE the budget reserve at run-start and on
  the context-index API; cap hits return `{tier, cap, upgradeTo}` for
  upgrade CTAs.
- **M3** — Stripe adapter behind `HARNESS_BILLING=stripe`: checkout session
  creation, webhook route with HMAC-sha256 signature verification (>5min
  timestamp drift rejected) and idempotency by event id, subscription status
  → tier sync. Test-mode only in CI (mocked Stripe, no keys).
- **M4** — OIDC SSO: discovery-based flow with PKCE and JWKS validation,
  per-team IdP configuration, email-match provisioning on first login
  (documented rule: no verified-email match ⇒ deny + audit), OTP fallback
  when unconfigured. Raw OIDC over httpx — no vendor SDKs.
- **M5** — MCP server (`mcp/`): stdio transport exposing `start_run`,
  `get_run_status`, `search_context`, `list_projects` under a service-token
  principal, with tool schemas written for LLM consumers.

### Mission 21 · Context pillar (2026-08-26)

The engine's plans can cite your actual codebase: index once, resolve @refs
at plan time, generate steps with path:line citations backed by indexed
chunks.

#### Added

- **X1** — Codebase indexing core: pgvector-backed store, pluggable
  embeddings, incremental re-index, frozen search contract (W21-C1).
  Migration **0030** is pgvector-gated and degrades honestly without the
  extension.
- **X2** — `@refs` resolution: hybrid keyword+vector ranking,
  token-budgeted context bundles; grammar pinned by parity tests in S7.
- **X4** — Retrieval-augmented planner: generated steps carry path:line
  citations resolved from the index (exit criterion met and demonstrated).
- **X3** — UI surface: `@` autocomplete refs, chunk preview panel,
  searchable file tree in section7.

#### Fixed

- Replay-cap flake eliminated (30/30 stability sweep clean); formal close at
  CI SUCCESS.

### Mission 20 · Horizon NEXT part 2 — the first 10x lever (2026-08-26)

Claim batching tore down the BASELINE wall: **1.69 → 37.58 journeys/s at 32
VU (~22×)** with zero failures across 4,521 journeys.

#### Added

- **T1** — Claim batching in the orchestrator worker (the 10x lever above);
  theoretical ceiling now ~3.2M runs/day, with executor width and provider
  latency as the honest next limits.
- **T2** — Observability minimum: request-id middleware on all 9 services,
  anonymized user hashing in logs, Prometheus scrape endpoint + 5
  promtool-validated alerts (`docs/runbooks/alerting.md`).
- **T3** — Resource limits on all 22 compose services, log rotation,
  `/ready` separated from `/health`.
- **T4** — `docs/extensions.md` (three real integration seams), glossary
  day-one nouns completed, README/AGENTS truth pass.
- **T5** — TypeScript SDK v0 generated from the same pipeline as the M19
  Python SDK — both SDKs stay in lockstep with the shipped OpenAPI specs.

### Mission 19 · BASELINE + Horizon NEXT phase 1 (2026-08-25)

First honest performance baseline plus the stateless-orchestrator groundwork
that made the M20 lever possible.

#### Added

- **B1** — BASELINE published: ~1.9 journeys/s ceiling, wall identified as
  the worker's single-claim poll cycle, $0.000051/run metered cost, 8→64 VU
  ladder methodology in `docs/benchmarks/`.
- **N1** — Stateless orchestrator phase 1: CAS-based approvals, durable-
  first execution, two-replica exactly-once spawner election.
- **N2** — Slim cross-instance bus signals (migration **0029**: ~70-byte
  wake-up payloads, 8KB publish cap).
- **N3** — Partitioned retention: monthly `agent_events` partitions
  (migration **0028**) + outbox/audit prune runners.
- **N4** — Canned demo: zero-key 8-minute value demo driven by recorded-truth
  replay (no fabricated model output).
- **N5** — Python client SDK v0 generated from the seven OpenAPI specs
  (91 paths / 104 operations).

### Mission 18 · Horizon NOW (2026-08-25)

Six correctness/capacity fixes from the 10x plan's Now horizon, all
fail-closed by construction.

#### Added

- **C1** — Authorization fail-closed everywhere (the triple fail-open path is
  dead; denials are audited).
- **C2** — One budget truth: `contracts/budget.py#resolve_cap` is canonical,
  fails CLOSED with an explicit degraded flag.
- **C3** — Cost gate O(1): covering indexes (migration **0027**) + daily
  spend-rollup runner.
- **C4** — Load-test harness (carried forward as the M19/M20 BASELINE tool).
- **C6** — Rollback story v0 rehearsed end-to-end: the image-drift gate
  refused an old image against the 0027-schema database, as designed.

#### Fixed

- **C5** — Invite/ticket hygiene: email binding on invites, `?token=` login
  deprecated, WS-fingerprint enforcement aligned with what browsers actually
  send, expired-bucket GC.

### Mission 17 · ELITE audit → the 10x plan (2026-08-25)

Seven-lens adversarial audit, its fix wave, and the plan that governed
everything after it.

#### Added

- **Phase A** — ELITE dual-team audit banked: 1,901 lines of findings across
  seven lenses (`docs/briefs/m17-findings-*.md`).
- **B6** — Contributor bootstrap: working quick-start, first tutorial
  (`docs/TUTORIAL.md`).
- **Phase C** — `docs/briefs/mission17-10x-plan.md`: four axes, 23 missions
  across Now/Next/Later/Bet horizons (executed as M18–M23).

#### Changed

- **B1/B3** — Budget-call serialization + contracts promotion + isolation
  enforcement (section-boundary import ban wired into CI guards).
- **B2** — Live PITR wiring exercised + CI migration coverage.
- **B4/B5** — Plan-gate team scoping fixed; rate limiter keys IPv6-safe (/64);
  engine runtime hardened with the hung-provider circuit breaker
  (`docs/engine-concurrency.md`).

### Mission 16 · Wave 6 — perf/scale + corpus (2026-08-24)

Independent ALEX-FORCE re-verification before close (S1 430 · S4 318 ·
S7 vitest 452, migration chain 0001→0026 unique).

#### Added

- **X1** — Runs-index tuning at 100k events: covering index (migration
  **0026**) + N+1 elimination via one batched extremes query per page.
- **X2** — Meeting message pagination: keyset cursor, bounded windows.
- **X3** — WS replay cap + seq-window resume proof, truncation markers so
  clients know when history was clipped.
- **X4** — UI systemization completed: every page on the design-system
  primitives.
- **X5** — Edge-case corpus wave A: 19 seed-ID-traceable lifecycle
  regression tests.

### Mission 15 · Wave 5 — hardening sweep (2026-08-24)

Security posture, data-integrity, and docs-truth wave closed by verifier
teams.

#### Added

- **S1** — CSP enforce-mode rollout (report-only first; policy built from
  audited real sources) + `/api/v1/csp-report`.
- **S2** — WS ticket client binding: fingerprint bound at mint, validated at
  upgrade (migration **0025**, renumbered from 0024 after a slot collision).
- **D1** — FK deletion story: explicit ON DELETE behavior everywhere +
  cascade suite (migration **0023**); `billing_plans.team_id` CASCADE.
- **D2** — Vacuum/maintenance schedule + runbook.
- **G6a** — Architectural-law CI guards: route modules must be
  default-export-only; no cross-section imports (`make guard-routes`,
  `make guard-isolation`).
- **G6b** — Reviewer-heuristic team tuning + calibration corpus.
- **G7** — Docs truth batch: README accuracy pass, `docs/api-versioning.md`,
  `GLOSSARY.md`, env-var sync.
- **S4** — Salted KDF upgrade for stored secrets with dual-read
  re-encryption (+ `reencrypt` management command).

#### Fixed

- **S3** — Accessible names on all placeholder-only inputs; login layout
  fixed at 320px viewport.

### Mission 14 · Wave 4 (2026-08-24)

Product features: metering, a11y/responsive frontend fleet, invites,
aggregate run API + detail page, optimistic UI, design system, Docker DNS
fix. Baseline `9141e39`; closed at `baf2299` with all five verifier teams
READY and CI run 32701744809 SUCCESS. Suites at close: S1 315 · S3 91 ·
S4 291 · S6 76 · S7 vitest 415/415 + tsc clean.

#### Added

- **F1a** — Cost metering live end-to-end (W4-C1): engine completion → S3
  `record_usage` via the internal API; `GET /api/v1/models/spend?sinceDays=N`
  returns real NUMERIC-exact numbers `{total, byDay[], unwired:false}`; the
  cost page drops the "unwired" explainer and renders actual run costs.
- **F1b** — Keyboard support + focus management across section7: full
  tab-order audit page-by-page, Enter/Space on interactive cells, visible
  focus rings, skip-link (`lib/focus.ts`).
- **F2a** — Invite lifecycle admin UI: mint/resend/revoke with status badges
  (draft/sent/redeemed/expired/revoked), audited actions; server half landed
  as resend/revoke routes + status derivation (`4c09072`).
- **F2b** — Responsive layout: sidebar collapses <900px, cards stack, tables
  scroll-x; zero fixed-px layout breaks verified at 375/768/1280.
- **F3a/F3b** — Aggregate run read endpoint (W4-C2):
  `GET /api/v1/orchestrator/runs/{id}` enriched
  `{runId,state,costUsd?,prId?,url?,steps?}` with explicit nullability plus
  runs-index cursor pagination; Runs detail page consumes it with honest
  degradation per missing field (state machine view, cost line, PR link,
  step timeline from events) (`84608eb`).
- **F4** — Optimistic UI for plan approve/reject and roster remove/
  role-change: instant update, reconcile on response, rollback with toast on
  error (`hooks/useOptimistic.ts`).
- **DS** — Design-system lane: shared primitives (Pill, Button, Card, Table,
  Modal) extracted to `section7/src/components/ui/**` with tokens; cost +
  team-settings pages migrated as proof; DS owns the directory exclusively.

#### Fixed

- **P2** — Docker DNS crash-loop on the legacy all-in-one backend
  root-caused and fixed (W2/W3 carryover): orphaned detached container →
  EAI_AGAIN resolution failure; compose now declares the `backend` service
  under the opt-in `legacy-backend` profile so the state cannot re-enter;
  ops runbook documents diagnosis (`26f8c49`, `docs/runbooks/ops.md`).

### Mission 13 · Wave 3 (2026-08-23)

Hardening & performance. Baseline Wave-2 close (CI 32685579474 SUCCESS);
10 lanes committed (`d22e18c`), closed with all five verifier teams READY,
CI run 32694440516 SUCCESS, resume-DTO MINOR fixed in-lane (`d539b87`).
Suites at close: S1 309 · S3 91 · S4 291 · S6 76 · S7 303 vitest.

#### Added

- **H2** — WAL-G PITR end-to-end (H-018): archive_command wired through an
  archiving `walg-db` sidecar, credentialed restore drill executed with
  timed RTO/RPO evidence; opt-in `walg` compose profile + `.env.example`
  knobs (`c3aead1`, `docs/runbooks/backup-and-pitr.md`).
- **H3a** — Playwright E2E hero-journey suite against the compose stack:
  login→project→meeting→brief→plan→approve→run→detail→review, plus OTP-flow
  spec (`eae5a41`, `section7/e2e/`); consumed via `make e2e`.
- **H3b** — CI/local gate convergence (H-024/H-025): Makefile is the single
  source of truth — ci.yml jobs call its targets verbatim so drift between
  "passed locally" and "what CI ran" is structurally impossible;
  version-pin conformance test revived as the 4-way contracts-version rot
  detector (`4cd7074`, `test_version_pin_conformance.py`, `6254fe5`).
- **H4a** — NUMERIC money migration (H-023): cost columns → NUMERIC(12,6)
  (migration **0020**), Decimal sweep across S1/S3 money paths, budget
  guard/pricing tests Decimalized. Operator note: caps budgets at
  $999,999.999999.
- **H4b** — TIMESTAMPTZ normalization: shared timeutil helpers in
  harness_identity, S6 conversion + tz-stamp tests; naive-datetime write
  sites zeroed (`e0c61ad` group, `9575287`).
- **G5a** — Oracle-consistent 403/404s across sections + OpenAPI response-
  model typing islands grown 8 → 39 routes (`d2922ff`, hardening suites in
  S1/S3/S4/S6).

#### Changed

- **H1a** — Pool math fix (H-017): shared engine factory, documented 84 ≤
  120 connection budget (7 services × (pool_size=8 + max_overflow=4), PG
  max_connections=120 with 36 slots headroom), CI-enforced
  (`6dfa3b2`, `docs/pool-math.md`).
- **G5b** — invokeAi dead-export prune + frontend DX batch (VT-2 MINOR +
  raw-14 top items) (`d116460`, `ba05ada`).

#### Fixed

- **H1b** — Run-queue correctness (H-017 final): heartbeat comparison now
  judged by the database's own clock; `run_claims` candidate selection uses
  SKIP LOCKED inside the subquery; zombie reaper releases timed-out claims
  and moves runs to BLOCKED with honest `heartbeat_lost` events; live-PG
  race proofs (`06b8055`, `4db60d5`).

### Mission 12 · Wave 2.1 (2026-08-23)

Baseline `ebdfeb7`; campaign graph `docs/briefs/mission12-wave2-graph.md`.

#### Added

- **E1** — `@ai` meeting invocations call real LLMs through Section 3
  (`call_model`) using team BYO keys; KeyVault decrypt happens server-side;
  the regex stub engine remains only as an honest fallback that says so when
  no key is configured.
- **E2** — Frontend 401 interceptor clears the session and routes to
  `/login?expired=1`; sign-out wipes every `harness.*` storage key; backend
  error codes map to human-readable messages instead of internal jargon.
- **E3a/E3b** — Roster management API (`DELETE`/`PATCH` members; admin-only,
  last-admin guard → 409 `LAST_ADMIN`, cross-team → 404 without an existence
  oracle; audited `member.removed`/`member.role_changed`) plus Runs rows
  click-through and roster UI consuming it.
- **E4a/E4b** — PR-open persists `pr_id` on the run (migration **0014**) and
  emits `pr_opened {runId, prId, url}`; minimal `GET /runs/{id}` aggregate;
  Review page consumes `?pr=` and subscribes to `pr_opened` with an honest
  empty state.
- **F1** — Global `RequestValidationError` handler returning the unified
  `{"error":{code,message}}` envelope (detail mirrored for back-compat);
  `Idempotency-Key` middleware on mutating POSTs (migration **0015**; pilots:
  keys, run start, meetings).
- **F2** — Optimistic concurrency for plan decisions: `agent_plans.version`
  column (migration **0013**, slot re-assigned after the M11 collision),
  conditional `UPDATE … WHERE version=:seen`, mismatch → 409
  `PLAN_ALREADY_DECIDED`.

#### Fixed

- Duplicate `invokeAi` client calls removed (single invocation per `@ai`).

## Mission 11 · Wave 1 (2026-08-23)

CLIENTELA remediation: six CRITICALs + flow-blocking HIGHs. Baseline
`d5d9619`, seven lanes, closed at `b255ce2` with all verifier verdicts READY
and CI green.

### Security

- **C-001/H-004** — Ownership proof enforced by default everywhere
  (config, compose, deploy services): unverified email can no longer mint an
  admin team; plain login joined the DB-backed protected set; legacy
  direct-add invite endpoint removed (`f04650c`, `61cb1a9`); verify-OTP
  throttled, OTP codes single-use.
- **C-002** — Audit emission wired at every sensitive call site (API keys,
  invites, billing): never-throw, secret-scrubbing typed helpers
  (`audit_emit.py`) (`7a6b2b3`).
- **C-003** — Atomic budget enforcement: fail-closed reserve/release guard +
  `cost_ledger` writer, proven under an 11-test concurrency suite
  (`7cc21f8`).
- **C-005** — Plan-gate orchestrator API per the frozen pair contract —
  propose/approve/reject with role gates (admin/engineer decide;
  viewer/guest → 403), idempotent approve, audited reject (`281d769`) — and
  the PlanGate UI consuming contract DTOs field-by-field (`c2b487c`).

### Changed

- **C-004/H-005/H-006** — Engine boot integrity: compose engine env block
  stripped of placeholder/dev defaults; unset `ENGINE_LIVE_MODE` is now a
  loud boot failure (explicit `live|dry` enum); silent provider/model
  downgrade ladder removed (`db9e333`, `65525b2`).
- **H-007/H-008** — Done-signal & terminal-state hygiene: role-gated
  done-signal requires a non-empty diff and refuses resurrection;
  `reviewer_passed → DONE` is terminal (`608f0ce`).

### Fixed

- Rate limiter was wired to a route that never existed (`/auth/otp/request`);
  moved onto the real login/OTP routes (`dc9b75f`).
- CI lint: `LiveMode` → `StrEnum` (ruff UP042) and import sort (I001)
  (`70532c0`).

## Mission 10 (2026-08-23)

ALEX_TEAM Phase 2 completion + Phase 3 edge-hardening begins. Baseline
`abcab51`; mission closed with all CI jobs green (`602c505`).

### Added

- **BYO keys** — `/api/v1/keys` CRUD over the frozen `api_keys` table:
  KeyVault-encrypted at rest, hint-only exposure, idempotent soft revoke,
  foreign-team requests 404 with no existence oracle; Settings → Keys page.
- **Cost dashboard** — `/dashboard/cost` over live `/spend`: range selector,
  totals cards, by-model bars, SVG sparkline, honest empty state.
- **Run history** — team-scoped `GET /api/v1/orchestrator/runs` with
  server-driven paging (limit ≤ 100) + filters; Runs page with status pills
  and relative times.
- **Billing** — `/api/v1/billing/state|plan` behind `HARNESS_BILLING`;
  local quota from `cost_ledger` vs `teams.budget_usd` (migration **0010**);
  Stripe adapter is an injectable protocol (no package); Billing settings
  page with meter.
- **Onboarding revival** — checklist API derives completion from real state
  only; interactive onboarding page with progress.
- **WAL-G PITR tooling** — `deploy/walg` scripts + opt-in compose profile +
  Dockerfile; shipped as reviewed-but-unexercised scaffolding (no S3 creds
  in build env — honesty note in `docs/runbooks/backup-and-pitr.md`).
- **Rate limiting** — fixed-window Postgres limiter on login/OTP routes
  (migration **0011**): atomic upsert, 429 + `Retry-After` envelope; DB
  failure fails OPEN.

### Fixed

- CI red on a missing checklist golden fixture (`582c044`); gitleaks
  allowlist for the fake test key.
- Compose `CORS` env shape: origins parser now accepts both the JSON-array
  form and comma-separated lists (`96c1ffb`).

## Wave 9 (2026-08-23)

Phase 2 SaaS core: metering, budgets, orgs, notifications, security headers.
Baseline `6d5e52c`; three first attempts died to provider outages with zero
tree writes and were re-dispatched; every node independently re-verified.

### Added

- **Metering** — `cost_ledger` gains real writers: usage recorded on every
  successful `call_model`; `GET /api/v1/models/spend` aggregates
  totals/byModel/byDay; models service boots an asyncpg ledger pool and
  drains pending writes on shutdown.
- **Budgets** — spend cap checked pre-spawn and on reviewer model calls;
  precedence `teams.budget_usd` (migration **0007**) >
  `HARNESS_TEAM_BUDGET_USD` > unlimited; over-cap produces an honest blocked
  event.
- **Orgs/invites** — `team_invites` (migration **0008**) with hashed tokens,
  admin-only mint/revoke, atomic redeem (404/409/410), members roster; team
  settings page with roles, invite form, revoke, join-by-token.
- **Notifications** — `notification_outbox` (migration **0009**) with
  templates for `run_finished`/`review_failed`/`pr_opened`; worker drains
  with max 3 attempts; dev mode is offline-green; EventBus listener tap is
  disclosed and exception-guarded so notifications can never break event
  publishing.
- **Security headers** — pure-ASGI middleware on S1/S4/S5/S6/S8
  (nosniff/DENY/referrer everywhere; HSTS only behind `HARNESS_TLS_ENABLED=1`;
  report-only CSP on HTML); CI gained a security-scan job (pip-audit WARN +
  gitleaks).

### Fixed

- Merge wiring caught live: double rate-limiter install turned 6 tests red —
  limiter moved to assembly level, suite green.
