# Marathon 2.0 W4 — SDK inventory, boundary re-proof, AST/LSP spike (M2-W4)

Follows the 2026-10-28 Marathon 2.0 roadmap, W4 row, and
[the W3 outcome](m2-w3-plan.md). This is **Marathon 2.0 W4**, distinct from the
historical W4 (reach: web, packages, MCP), whose plan and evidence are in
[week-4-plan.md](week-4-plan.md).

Governing rule: *an unchecked gate stays open when a dependent week begins.*
W3's deterministic gate is checked; the stop/torn-write clauses remain open and
are carried here as limitations, not silently counted.

## Purpose

The W4 row asks for three things:

1. an inventory of the **exact pinned SDK**, its profile, skills, hooks and tool
   registration, separating *available* from *configured* from *tested*;
2. **authorized requests work and forbidden destinations/tools fail** at the
   current revision;
3. a **read-only AST search and LSP spike** that cannot escape worker policy.

## Inventory (2026-10-28)

| Item | Observed | Where |
|---|---|---|
| SDK | `deepseek-harness-sdk==0.2.1rc2`, `deepseek-harness-runtime-bin==0.2.1rc2` (pinned) | `section4/pyproject.toml:17-18` |
| Runtime carrier | single-file Node executable `dsh-jsonrpc-agent-pkg-linux-arm64`; embedded package metadata says `version 0.0.0-dev` (the wheel is the versioned unit) | orchestrator site-packages |
| Profile | `sdk-minimal` (`DEFAULT_DSH_PROFILE`), not the default `sdk` (danger-full-access); a per-run overlay is appended after operator patches so none can loosen it | `engine.py:54-55, 151-310` |
| Transport | stdio JSON-RPC; `HarnessConfig(profile, patches, cwd, env)`; `initialize(cwd, provider, model)`. No typed `skills`/`hooks`/`mcp` parameter — MCP and policy are patched into the composition | `deepseek_harness/client.py` |
| Plugin surface | **342** `@deepseek-ai/dsh-*` ids embedded in the runtime binary | extracted from the bundled executable |

### Available vs configured vs tested

`available` = shipped in the pinned runtime. `configured` = present in the
profile + per-run overlay for a real run. `tested` = evidence from this row or a
prior gate at the current revision.

| Capability | Plugin id(s) | Available | Configured | Tested |
|---|---|---|---|---|
| Shell | `dsh-tool-bash` / `dsh-bash-local` | yes | **replaced** by `harness-shell.js` (Section 5 gate) | yes — every drill; shell gate fails closed |
| File read/image | `dsh-tool-fs` | yes | yes (`read`, `readImage`) | yes (W2/W3/W11) |
| File write/edit | `dsh-tool-fs` | yes | conditional: only when the OS boundary freezes forbidden paths / narrower scope | yes (W3 code change; `_fs_write_capability`) |
| glob/grep | `dsh-tool-fs-search` | yes | yes | yes (W2 code-search) |
| Web | `dsh-tool-web`, `dsh-web-fetch-http` | yes | **not used**; our `harness-web.js` gates every dial | yes — live fetch 200, off-list 403 |
| Browser | `dsh-browser-use`, Playwright MCP variants | yes | our `harness-browser.js` (Chromium in the worker image) | yes (W10) |
| Images | — (ours) | n/a | `harness-images.js` | yes (W11, 22/22) |
| MCP client | `dsh-mcp-client`, `dsh-mcp-resources` | yes | yes, reviewed list only (`DSH_MCP_SERVERS`); spawned servers get **no credentials** | yes — live stub run, tool events audited |
| Package awareness | `dsh-tool-workspace-dependencies`, `dsh-package-manifest` | yes | **no** | no |
| Installs | shell gate + egress proxy | n/a | off by default; `DSH_ALLOW_INSTALLS` under container boundary | yes — allowed + denied registry |
| Skills | `dsh-skill`, `dsh-skill-filesystem`, `dsh-skill-office`, `dsh-tool-skill` | yes | **no** | no (W6/W12) |
| Hooks | `dsh-hooks-claude-code`, `dsh-hooks-codex` | yes | **no** | no (W12) |
| LSP | `dsh-lsp`, `dsh-lsp-stdio`, `dsh-tool-lsp` | yes | **no** | spike only (this row) |
| Subagents / teams | `dsh-subagent*`, `dsh-experimental-agent-team*` | yes | **no** | no (W8) |
| Persistent bash | `dsh-tool-bash-persistent` | yes | **disabled** | n/a |
| Sandbox policy | `dsh-sandbox-policy` | yes | `mode: workspace-write`, `workspaceRoot` per run | yes |

The honest summary: the runtime is much larger than what we expose, and every
row that says "no" is a capability we have not reviewed, not a capability we
have. Nothing in this row widened exposure; the AST/LSP tools are **not mounted**
in the engine.

## Defect found at the current revision (and fixed)

The deployed orchestrator had `DSH_MCP_SERVERS=/opt/harness/mcp/servers.stub.json`
but **no `/opt/harness/mcp` in its container** — the reviewed list ships only in
the worker image (`section4/worker.Dockerfile:37`), while the process that reads
it is the orchestrator (`engine._execution_policy_patch` →
`harness_orchestrator.mcp.reviewed_servers`). Verified before the fix:

```
McpConfigError: DSH_MCP_SERVERS='/opt/harness/mcp/servers.stub.json' is set
but the file does not exist
```

That is fail-closed by design (a declared-but-missing list must fail the run),
so it did not open anything — it broke every engine run after the last container
rebuild. Fixed by mounting the same reviewed directory read-only into the
orchestrator (`deploy/docker-compose.yml`, alongside the existing
`./patches:/opt/harness/patches:ro`). After recreating the orchestrator:

```
[{'transport': 'stdio', 'serverName': 'harness-stub', ...}]
```

Lesson recorded: a reviewed list has an **owner process**, and shipping it in
the image of a different process is not configuration.

## Boundary re-proof (current revision)

| Task | Result | Evidence |
|---|---|---|
| `w4-egress-deny` | PASS 1.2 s | `docs/briefs/evidence/eval/baseline-20261028T134204Z.json`, `pilot-prep/w4-egress-network-1793194928.json` |
| `w4-mcp-client` (live) | PASS 16.7 s | `baseline-20261028T134316Z.json`, `pilot-prep/w4-mcp-client-1793194998.json` |
| `w4-web-fetch` (live) | PASS 14.6 s | `baseline-20261028T134402Z.json`, `pilot-prep/w4-web-fetch-engine-1793195045.json` |
| `w4-install` | PASS 20.3 s | same baseline, `pilot-prep/w4-install-1793195080.json` |
| `w4-credential-leak` (live) | PASS 16.7 s | same baseline, `pilot-prep/w4-credential-leak-1793195080.json` |

Observed, not inferred:

- the live fetch returns the real page title from `api-docs.deepseek.com`, and
  `example.com` is refused with "not in the web allow-list";
- the MCP run records `mcp__harness-stub__stub_echo` and
  `mcp__harness-stub__env_probe` in `agent_events`; the marker appears only via
  the server; no credential *value* from the deployment appears in any tool
  result or the final answer;
- a permitted `pip install` succeeds through the proxy and an off-list index is
  403; installs stay off unless the flag is set;
- the `/engine` view of a settled run says `"engine": "replayed"` — that is the
  view reconstructed from the durable event log after the run left memory, not a
  canned demo. The tool calls and fetched bytes above are live.

## AST/LSP spike (read-only, confined)

Two small tools, neither mounted in the engine:

- `section5/src/harness_sandbox/ast_search.py` — parses Python with `ast`
  (no import, no execution), returns definitions and module constants, caps
  files/bytes/results/seconds, never follows a symlink out of the root.
- `section5/src/harness_sandbox/lsp_client.py` — Content-Length LSP client that
  spawns the server in its own session, removes every credential-shaped env key,
  caps each frame before buffering it, enforces a per-request deadline and kills
  the process group on timeout or oversize.
- `section5/tests/fixtures/lsp_fixture_server.py` — stdlib server with
  definition/references plus probe methods (env, cwd, write, network, slow,
  flood).

**Unit tests: 17 passed** (`test_ast_search.py` 9, `test_lsp_client.py` 8):
symlink escape skipped, syntax errors do not hide other matches, result caps,
tree byte-identical after a search, credential overrides refused, timeout kills,
oversize frame refused before buffering, cwd confinement.

**Container drill: 10/10 PASS in 3.95 s** through the runner
(`baseline-20261028T134734Z.json`, `w4-ast-lsp-1793195233.json`), in the worker
image on `--network none`, uid 10001, workspace mounted read-only:

| Check | Observation |
|---|---|
| AST finds planted definitions | `RETRY_BACKOFF_SECONDS`, `RetryPolicy.compute_backoff`, `flush_backoff_queue` |
| symlink escape never read | `pkg/escape.py` skipped; the outside symbol is absent |
| source tree unchanged | identical SHA-256 map before/after every call |
| LSP definition | `file:///work/pkg/retry.py` |
| env scrubbed | 4 keys (PATH/LANG/HOME/TMPDIR), none matching `DEEPSEEK`/`HARNESS` |
| cwd confined | `/work` |
| cannot write | `OSError: [Errno 30] Read-only file system: 'escape.txt'` |
| cannot reach network | `OSError: [Errno 101] Network is unreachable` |
| deadline kills the server | `LspTimeout` in 1.34 s with a 1.0 s deadline |
| no route | empty `ip route` |

## Gate position

| W4 exit claim | State |
|---|---|
| Authorized requests work; forbidden destinations/tools fail | **closed at this revision** (web, install, egress, MCP) |
| SDK inventory separates available / configured / tested | **closed** (table above; 342 ids vs 8 configured groups) |
| LSP/AST servers cannot escape worker policy | **spike closed** (read-only, scrubbed, no network, deadlines kill) |
| A third-party MCP server is used | **not claimed** — the reviewed server is our first-party stub; a real third-party server needs credentials/ownership and is a W6/W12 review |
| Package/lockfile awareness | **not claimed** — available upstream, not configured |

## Limitations

- The LSP spike proves the *client's* bounds against a fixture server, not
  compatibility with pyright/clangd/rust-analyzer. W7 integrates real servers
  with their own startup/timeout/output profiles.
- AST search is Python-only and definition-only; it is a boundary/ranking spike,
  not a code-intelligence product.
- Neither tool is mounted in the engine. Mounting one is a capability change and
  waits for M01 (confinement) to close.
- The `/engine` final response for settled runs is reconstructed from the event
  log; the live evidence is the audited tool calls, not that view.
- MCP evidence uses the local stub; "third-party" remains an open review item.
- W3's stop/torn-write clauses are still historical-only; W4 does not change that.

## Carry-over

- **W5:** CLI parity and evaluation capture; no boundary change.
- **W6/W12:** skills/hooks lifecycle and a real (credentialed) MCP server, with
  the credential broker decision still owed.
- **W7:** real language servers behind the client proven here; AST/repo-map
  packaging after M01.
