# Week 4 plan — reach: web, packages, MCP client

Follows [Week 3](week-3-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Gate

Exit evidence for W4 as written in the roadmap:

> **an agent answers a question that requires reading live docs; a run uses a
> third-party MCP tool and the audit records it.**

Read through the standing ground rule (*security changes ship with the
enforcement*), that expands into four claims that must each be shown, not
asserted:

1. an authorized outbound request succeeds;
2. a destination or tool outside the allow-list is denied;
3. credentials do not reach the model context;
4. installs run confined and are audited.

This document records what the audit found before any code was written.

## What exists today (audited 2026-10-26)

| Claim | State | Where | What is missing |
|---|---|---|---|
| Allow-list egress proxy | **exists; list now derived (2026-10-26)** | `egress_proxy.py` answers `403 Forbidden` unless `CONNECT` targets port 443 **and** `is_allowed_destination(host)`; `egress_allowlist.json` is generated from the provider registry by `scripts/gen_egress_allowlist.py`; a drift test fails when the artifact is stale | the derivation widened the proxy's reachable set from 3 hosts to 54 provider endpoints + 4 registries + 3 git hosts; docs (62 hosts) are a separate `web` purpose and stay closed to the sandbox. Live reachability from inside `sandbox_egress` is still undrilled |
| Sidecar deployed | **exists** | `deploy/docker-compose.yml` service `sandbox-egress`; healthcheck sends `CONNECT example.com:443` and asserts `403`, so a dead listener or loosened list reads unhealthy; `sandbox_egress` network is `internal: true` with no gateway | the container only joins that network when `SANDBOX_USE_DOCKER` is on; the local seatbelt path has no egress boundary at all |
| Sandbox network wiring | **exists, fails closed** | `docker_runner.py`: `allow_network=False` by default; when true it requires **both** `SANDBOX_EGRESS_NETWORK` and `SANDBOX_EGRESS_PROXY_URL`, injects `HTTP(S)_PROXY`, and refuses to start otherwise | no evidence yet that the proxy URL is reachable from inside the internal network (the compose comment says "not yet drilled") |
| `web_fetch` tool | **absent** | no such tool in `engine._execution_policy_patch`; `docs/capability-gap-2026-10-25.md` records it as ABSENT | the runtime binary ships `@deepseek-ai/dsh-tool-web` and `dsh-web-fetch-http` — the tool exists upstream, it is simply not mounted or gated |
| MCP **client** | **absent** | `mcp/` is a server exposing four harness tools; nothing in the repo consumes an MCP server | runtime binary ships `@deepseek-ai/dsh-mcp-client` and `dsh-mcp-resources`, and its session protocol carries `mcpServers`; the Python SDK has no typed `mcp_servers` parameter, only the raw `request()` escape hatch — **verify that path before designing around it** |
| Reviewed MCP server allow-list | **absent** | — | an MCP server is a process we spawn (or a URL we call); it needs the same allow-list + audit treatment as the shell gate |
| Package/lockfile awareness | **absent** | runtime ships `@deepseek-ai/dsh-tool-workspace-dependencies` and `dsh-package-manifest`; neither is mounted | read-only lockfile awareness is the cheap half; enabling it is a policy patch line plus tests |
| Installs inside the sandbox | **blocked on purpose, not audited** | `section5/src/harness_sandbox/policy.py` blocks `pip install`, `python -m pip install`, `npm install/i/ci`, `yarn/pnpm add`, `npx -y`, `yarn/pnpm dlx` — the comment says *"pinned_registries is not enforced at runtime, so block the verbs"* | that justification is now **half stale**: the proxy does enforce registries, but only the container runner reaches it. Unblocking the verbs without proving confinement would be a security change shipped before its enforcement |
| Workspace cache between runs | **absent** | checkpoints (`run_checkpoints.py`) snapshot a run's own workspace, which is discarded with the run | a warm cache needs an owner, an eviction policy and a poisoning story (a cached `node_modules` is executable content from a previous run) |
| Credential scrubbing (sandbox) | **exists** | `policy.sanitize_environment` drops `HARNESS_*`, `DEEPSEEK_*`, `GITHUB_*`, `AWS_*`, `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`; command validation rejects `$HARNESS_…`-style references | no path for a **trusted** credential to reach an MCP server; today there is no MCP client, so nothing leaks — the moment there is one, this becomes the hard part |
| Credential scrubbing (engine worker) | **exists, narrower** | `confine.scrubbed_env` allowlists `PATH`, `LANG`, `LC_ALL`, `TERM`, `PYTHONPATH`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` and the `DSH_` prefix | `DEEPSEEK_API_KEY` is deliberately present (the engine must call the model); the claim to prove is that it never appears in a **tool result** the model can read |
| Audit of tool calls | **exists for shell/fs** | W2's tool events on `agent_events`; the W3 drills read them | unverified for MCP-dispatched tools; if the runtime emits them under a different event shape, "the audit records it" is not yet true |

## Decisions owed before code

1. **Where the allow-list lives — decided 2026-10-26.** Derived into a
   checked-in artifact (`section5/src/harness_sandbox/egress_allowlist.json`) by
   `scripts/gen_egress_allowlist.py`, with a drift test in Section 5. Sections
   cannot import each other, so build-time derivation plus a failing test is the
   only version of "derived" that can also be enforced. Decisions are
   purpose-scoped: `sandbox` = endpoints + registries + git, `web` = docs +
   endpoints, `any` = union (administration only). Unknown purposes raise.
2. **Install policy.** Unblock the verbs and rely on the proxy (requires
   proving the proxy actually bounds npm/pip, including their subresource hosts),
   or keep the blanket block and record W4's install clause as deliberately
   out of scope. The gate text says installs must run, so the first is the
   intended path — but only with the container path proven, not the seatbelt one.
3. **MCP credential model.** Environment on the spawned server vs a broker that
   injects at call time. Either way the token must not be echoed into a tool
   result. Pick one before mounting `dsh-mcp-client`.
4. **First reviewed MCP server.** A local stub proves the mechanics and is CI-safe;
   a filesystem or git server proves "third-party" without an account. The gate
   needs the latter eventually; the stub is how we get the boundary right first.
5. **Cache ownership.** A per-project warm cache is the only version with a safe
   owner; a per-run cache is just a slower fresh install. If cache work is
   deferred, say so in the gate rather than pretending the clause is met.

## Slices, in order

1. **Egress decisions derived and testable.** Replace the hard-coded trio with a
   registry-derived allow-list plus an explicit reviewed exception list; add a
   per-destination decision function the tool can call *before* dialing, so the
   deny is visible in the tool result rather than a proxy error the model has to
   interpret. Tests: allow, deny, port, IDN/case folding, and the sidecar
   healthcheck still asserting 403.
2. **`web_fetch` mounted and gated.** Add the runtime plugin to
   `_execution_policy_patch` behind the same policy that governs the network,
   route it through the egress decision, and record the fetched URL in the tool
   event. Live drill: a question that needs a live doc page, with the transcript
   showing the fetch and the answer.
3. **MCP client with an allow-list.** Stand up a local stub server, mount
   `dsh-mcp-client` with a reviewed server list, and confirm tool events land in
   `agent_events` with the server and tool named. Then point it at one reviewed
   third-party server.
4. **Install path.** Only after slice 1–2 prove the boundary in container mode:
   relax the install verbs under an explicit policy flag, run `npm ci` and
   `pip install` behind the proxy, capture the outbound destinations, and show
   the audit trail. A denied install (registry not on the list) is the negative
   case.
5. **Credential non-leak drill.** Search the stored transcript and every tool
   result for the values of the credentials the worker holds; the drill fails
   if any appears. This is a grep with a pass condition, not a promise.

## Known constraints

- **Mode matters.** Every egress claim is only true in `container` mode. The
  macOS seatbelt profile confines reads and writes, not sockets; evidence must
  name the mode it was taken in, and the gate cannot be closed on a seatbelt run.
- The proxy is HTTPS `CONNECT`-only on port 443. A registry or doc site reached
  over plain HTTP, or on another port, is denied by design — say so in the tool
  result instead of letting it read as a network failure.
- CI must not depend on the internet: the allow case can hit a local TLS echo
  server listed in the test allow-list; the deny case needs no network at all.
- `deploy/docker-compose.yml` records that the proxy path "is not yet drilled
  against a live Docker daemon". Slice 1 closes that sentence or leaves it
  standing — it must not stay both deployed and undrilled.

## Slice 1–2 outcome (2026-10-26)

**The allow-list is data now.** `scripts/gen_egress_allowlist.py` derives
`section5/src/harness_sandbox/egress_allowlist.json` from
`section3/src/harness_router/provider_registry.py`; Section 5 loads it and a
drift test fails when it is stale. The derivation drops `replace-with-…`
placeholders and every loopback/private destination, so "derived" cannot mean
"reachable by accident".

**Purposes are listeners, not headers.** `egress_proxy` serves two ports:
`3128` (`sandbox`: package registries, git hosts, provider endpoints) and `3129`
(`web`: provider docs and endpoints). A caller cannot declare its own purpose,
so the boundary is which port it can reach. The sidecar healthcheck now probes
**both** ports with a denied CONNECT, so a dead 3129 cannot read as healthy.

**The proxy path is drilled.** `scripts/e2e/w4_egress_network_drill.py` sends
CONNECT lines inside the sidecar and from a throwaway container on the
gateway-less `harness_sandbox_egress` network. Result (`w4-egress-network-*.json`):
`api.anthropic.com` and `github.com` → 200; `example.com` → 403; docs on the
sandbox port → 403; non-443 → 403; non-CONNECT → 405 — identical from both
vantage points. The compose comment that said the proxy was "not yet drilled"
is now a record of the drill instead.

**`web_fetch` is mounted and gated.** `dsh_plugins/harness-web.js` calls
`web_gate.py`, which refuses anything outside the `web` purpose, plain HTTP,
non-443 ports, and **every redirect hop** outside the list; it returns readable
text, not raw HTML. A missing gate removes the tool rather than opening an
unfetched one. Unit coverage: `section5/tests/unit/test_web_gate.py`,
`test_egress_proxy.py`, `test_egress_allowlist.py`, and the overlay assertion in
`section4/tests/unit/test_engine_shell_policy.py`.

**What the first live engine run found.** The tool was mounted and the *denied*
fetch came back exactly as designed, but the allowed fetch failed inside the
confined worker with `Temporary failure in name resolution` — the per-run
network has no gateway (by design), so `web_fetch` had a policy decision and no
route. The fix is architectural, not a loosened check: `ContainerBoundary` now
connects the allow-list proxy to the run network under the alias
`harness-egress`, and the gate dials `http://harness-egress:3129`. The run
network gains exactly one peer, and that peer can only reach what its allow-list
admits.

**What the derivation caught in the registry.** Anthropic's `docs_url` still
pointed at `docs.anthropic.com`, which now 301s to `platform.claude.com`; the
redirect check refused the hop. The fix was to correct the registry entry (data
truth), not to widen the allow-list. The stale URL is recorded here because it
is exactly the class of drift this derivation exists to expose.

**Known limitations, stated rather than hidden.**

* The worker can reach the proxy directly, including the sandbox port. Purpose
  separation therefore bounds *what can leave the machine* (docs vs endpoints vs
  registries), not *which tool asked*. An agent that shells out to
  `python -c urllib…` with the proxy can fetch an allow-listed docs page without
  it appearing as a `web_fetch` call.
* `web_fetch` evidence is taken in `container` mode only. A seatbelt worker has
  no egress boundary, and this week does not claim one.
* The gate refuses `502`-class upstream failures as fetch errors, not denials;
  the distinction is visible in the result text.

### Slice 2 live evidence (2026-10-26)

`scripts/e2e/w4_web_fetch_engine_drill.py` drives a real run over HTTP on the
cheapest configured model. The goal names neither the docs title nor the refusal
text, so a correct answer can only come from the tool:

* audit row 1 — `web_fetch https://api-docs.deepseek.com/` → `ok: true`, output
  begins `fetched … (HTTP 200, text/html)` and contains `Your First API Call`;
* audit row 2 — `web_fetch https://example.com/` → `ok: false`, output
  `Error: Denied by the harness egress policy: example.com is not in the web allow-list`;
* the final answer is `TITLE: Your First API Call | DeepSeek API Docs` /
  `REFUSAL: …not in the web allow-list`, and the worker was observed in its
  confined container.

Evidence: `docs/briefs/evidence/pilot-prep/w4-web-fetch-engine-*.json`. Note
that `/engine`'s folded `toolCalls` is empty until the run leaves
`awaiting_done_signal`; the drill reads the durable `agent_events` rows instead,
which is the audit the gate asks about.

## Slice 3 finding — the SDK path for MCP is a dead end, the plugin path is not

Probed 2026-10-26 with `scripts/e2e/w4_mcp_probe.py` against the pinned SDK
(`deepseek-harness-sdk 0.2.1rc2`, profile `sdk-minimal`):

1. `initialize` **accepts** a `mcpServers` field without complaint — and ignores
   it. The stub server's log stayed empty (the process was never spawned) and the
   model's tool list was still `bash`, `read_image`, `working_directory`.
2. `session/new` — where the runtime's ACP layer puts `mcpServers` — is **not an
   SDK-runtime method**: `unknown DeepSeek Harness SDK runtime method:
   session/new`.

So the roadmap's "mount the SDK's MCP client" cannot be done by passing servers
to the SDK. The capability exists, but as a **cordis composition plugin**:
`@deepseek-ai/dsh-mcp-client` bundles with `sdk-minimal` and its config schema is

```json
{
  "transport": "stdio",
  "serverName": "stub",
  "command": "python",
  "args": ["-m", "…"],
  "env": {},
  "cwd": "/workspace",
  "toolCallTimeoutMs": 30000,
  "failOnStartupError": true,
  "maxInstructionBytes": 65536,
  "reconnect": { "enabled": true, "initialDelayMs": 500, "maxDelayMs": 30000, "maxAttempts": 10 }
}
```

(or `{"transport": "streamable-http", "url", "headers"}`). It registers the
server's tools on the same tool registry as `bash`, so the existing tool-event
pipeline should record them — that part still needs the live proof.

**Next for slice 3:** add reviewed `mcp-client` entries to
`_execution_policy_patch` from an operator-supplied, reviewed server list (not
from the plan or the model), keep `failOnStartupError: true`, and run the stub
server in a live worker to prove tool calls land in `agent_events`. The
**third-party** leg of the gate then depends on slice 4: the worker image has no
installer today, so an `npx`-fetched server cannot be the reviewed first server.

## Slice 3 outcome — reviewed MCP servers, live and audited (2026-10-27)

**The reviewed list is configuration, not input.** `harness_orchestrator/mcp.py`
reads `DSH_MCP_SERVERS`, a JSON file **baked into the worker image**
(`/opt/harness/mcp`) so that admitting a server is a rebuild — a review event —
rather than something a running service can swap. Validation is the boundary:
`stdio` or `https` only, names pattern-checked, unknown keys refused,
`failOnStartupError` forced to true, and **no `env` at all on a spawned server**
(credential decision 3: servers get nothing; a call-time broker is the later
design). A declared-but-malformed list fails the run — it does not degrade into
"no servers", because a half-applied boundary reads exactly like an intact one.

**Neither the plan nor the model has a channel to it.** `shell_policy` carries no
MCP keys and nothing here reads the plan; a test asserts that a plan asking for
`{"mcpServers": [{"command": "curl"}]}` mounts only the reviewed entry.

**Live proof** (`scripts/e2e/w4_mcp_client_drill.py`,
`w4-mcp-client-1793109830.json`): a real HTTP-driven run settled
`awaiting_done_signal` with `agent_events` tool rows named
`mcp__harness-stub__stub_echo` and `mcp__harness-stub__env_probe` — so the
runtime registers MCP tools in the same registry, and **the audit names the
server and the tool**, which is precisely what the gate asked. The marker
`HARNESS-MCP-STUB:<text>` appears nowhere in the goal, so it can only be in the
transcript if the call crossed the server process. The worker was observed on its
own run network while executing.

**What that drill cost: an eight-day leak.** The first attempt died in two
seconds on `docker network create` — by hand, "all predefined address pools have
been fully subnetted", with 20 orphaned run networks. W4's egress attach connects
the shared proxy to every run network, and `close()` removed the network while
the proxy was still attached — which Docker forbids — so every run that reached
the proxy leaked one network. Fixed (`disconnect` before `rm`), two tests pin the
order, and the orphans were reclaimed. It also exposed that the worker daemon
never received `DSH_PATCH_FILES`/`DSH_MCP_SERVERS`: `confine.wrap_command` passes
`--env KEY` by name and Docker resolves the value from *its own* client
environment, so an orchestrator-only setting reached every worker empty.

**Still open in the gate:** the **third-party** leg. The worker image has no
installer, so an `npx`-fetched server cannot be the reviewed first server —
that depends on slice 4, as planned.

## Slice 4 outcome — installs run confined, and the proxy is the record (2026-10-27)

**The verbs are no longer blanket-blocked; they are conditionally permitted.**
`policy.install_permission()` grants installs only when `DSH_ALLOW_INSTALLS` is
set **and** `HARNESS_ENGINE_CONFINEMENT == "container"` **and**
`HTTPS_PROXY` names the egress sidecar. Anything less and the refusal carries its
reason into the tool result (`… — installs: package installs are off by default;
set DSH_ALLOW_INSTALLS=1 to permit them`). The engine reaches the proxy through
`confine.install_env()`, which injects the proxy URL by value for exactly this
case; `HTTP(S)_PROXY` are the only routing variables passed that way, because a
proxy is a destination, not a secret.

**The proxy now keeps the audit the gate asks for.** `egress_proxy._log_tunnel`
prints one line per tunnel that actually opened
(`CONNECT pypi.org:443 purpose=sandbox allowed`). Denials are deliberately not
logged: the compose healthcheck probes with a denied CONNECT every 10s, so
logging refusals would bury the record in noise while telling us nothing the
caller did not already see. A unit test pins both halves.

**Deterministic drill** (`scripts/e2e/w4_install_drill.py`,
`w4-install-1793111223.json`) — `shell_gate.py` driven directly inside a
throwaway container on a real run network, no model in the loop:

| case | result |
|---|---|
| `pip install` with no flag | denied, reason names the flag |
| `pip install six==1.16.0` with flag + container + proxy | exit 0, `Successfully installed six-1.16.0` |
| same, `--index-url https://files.example.com/simple` | exit 1, `Tunnel connection failed: 403 Forbidden` |
| proxy audit | `pypi.org:443` and `files.pythonhosted.org:443`, purpose `sandbox` |

So the boundary bounds installs at the destination level, not just at the verb
level, and the two hosts pip needed are exactly what the audit shows.

**Known limitations, stated rather than hidden.**

* Only the **pip** lane is drilled. The npm/npx patterns are permitted under the
  same permission function, but the worker image has no node toolchain today, so
  claiming the npm lane would be an assertion. It stays untested rather than
  quietly assumed.
* Installed code is unvetted executable content from a previous network
  exchange. The flag trades that risk for the boundary; the boundary does not
  remove it. There is no signature or integrity story beyond TLS plus the
  derived registry list.
* Per decision 5 the workspace cache is still absent: each permitted install
  fetches fresh into a run-local `/tmp`, which is discarded with the run. The
  gate's install clause is met by the drill; the caching clause was never part
  of the gate text and is not claimed.
* The audit line is the proxy's, so it records allowed egress only. A refused
  install is visible in the tool result; it is not in the proxy log.

## Slice 5 outcome — the credentials stayed out, and the drill found a hole in the audit (2026-10-27)

**The claim, as a grep with a pass condition.** `scripts/e2e/w4_credential_leak_drill.py`
takes the deployment's real secret *values* (never printed, never written into
evidence), runs a confined worker whose goal is to dump its own environment, and
searches every stored surface: `engine.finalResponse`, `engine.toolCalls`,
the run's comments, the 600-char audit view, **and the full `agent_events`
payloads** (the truncated view is exactly where a secret could hide past the
cut). Verdict: **PASS** (`w4-credential-leak-1793114924.json`) — one tool result
that really dumped the environment, three secret names searched, zero hits.

**What the worker's environment actually contained** is the other half of the
proof. `policy.sanitize_environment` runs before the shell execs, so the stored
result is `HOME=… PATH=… PWD=…` and the engine's own key is simply absent —
it never reaches the transcript because it never reaches the tool
(`w4-credential-leak-1793112416.json` is kept as the record of the run that
caught the defect below).

**The defect the drill caught was not about credentials.** The first green-shape
attempt produced a `tool_call` row and **no `tool_result` row at all**: the
output was NUL-separated (`cat /proc/self/environ`), PostgreSQL cannot store
U+0000 in text/JSONB, asyncpg raised `UntranslatableCharacterError`, and
`_publish_safe` turned the whole event into a log line. So the transcript said
"the tool never answered", and a leak search over stored rows would have passed
over a hole. Fixed in `service.durable_event_text` (NUL → U+FFFD, lone
surrogates normalised, applied before scrub and truncation), four tests pin it,
and the rerun stores the dump. A missing row is worse than a lossy one.

**Two facts the first attempts were not evidence of:**

* `env` and `printenv` are not sandbox binaries — the policy refused the call
  (correctly). The goal now uses `cat /proc/self/environ`, which the allow-list
  admits; verifying that through the real gate before burning a 10-minute model
  run is cheaper than discovering it afterwards.
* The drill used to "pass" on a run that never exercised the exposure path (it
  only required *no hits*). It now requires a completed turn **whose tool result
  really is an environment dump**; a deadline kill or a refused command fails
  loudly. A slow local model also needs `ENGINE_RUN_TIMEOUT_SECONDS` raised —
  that knob is now an explicit orchestrator setting rather than an undocumented
  default.

## W4 gate status — closed

| Claim | Evidence |
|---|---|
| an authorized outbound request succeeds | `w4-egress-network-*.json`, `w4-web-fetch-engine-*.json` |
| a destination or tool outside the allow-list is denied | same, plus `w4-install-1793111223.json` (off-list index → 403) |
| credentials do not reach the model context | `w4-credential-leak-1793114924.json` |
| installs run confined and are audited | `w4-install-1793111223.json` (proxy logs the exact registries) |
| a run uses an MCP tool and the audit records it | `w4-mcp-client-1793109830.json` — **stub only**; the third-party leg still waits on a node toolchain in the worker image |

The last row is the honest remainder: the mechanics are proven against a server
whose behaviour we can read, but a *reviewed third-party* MCP server has not
been run, and `npm`/`npx` installs are permitted by the same policy function yet
undrilled because the worker image has no node toolchain.
