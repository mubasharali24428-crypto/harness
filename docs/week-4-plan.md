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
