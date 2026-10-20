# A5-S2 report — commands, hooks, policy, SDK, wiki integration, distribution

**Agent:** A5 (MCP, Integrations, Observability, Operations, Pilot Evidence)
**Date:** 2026-10-17
**Baseline commit:** `31bf4077e9b567b1f50ecb99818d501baab3115a` (working tree was already dirty; nothing committed)
**Artifacts:** `packages/extensions/` (service+client+SDK), `integrations/connectors/wiki/` (new), `docs/agent-program/a5/session-2/`

## What shipped

### F067 — Namespaced slash/task commands
`commands.py` + `/api/v1/extensions/commands` endpoints. Commands are team
templates with declared `{{placeholder}}` arguments; the stored `name` is the
full namespaced key (`team/<slug>` for user commands; the model reserves
`plugin:<pluginId>/<slug>`), and uniqueness is enforced on the full key.
Invocation validates strictly (missing AND unknown arguments both rejected —
no silent blanks), renders, and returns the text plus a sha256 digest for
audit. Removal is terminal (`removed` status; invoke -> 404).
**Limit:** the chat-surface `/`parser lives in the client UI, outside A5; the
registry provides the namespaced, collision-free command store.

### F068 — Event hooks
`hooks.py` + `/hooks` endpoints. A hook subscribes to `tool_call.succeeded`,
`tool_call.failed`, or `*`, with target `webhook` (URL) or `mcp_tool`
(serverId+toolName). The call path emits events inline: a hook fires
**immediately after the call**, and every attempt is recorded as a delivery
(`delivered` / `failed` / `refused`) with bounded timeout (5 s) and **no
retries** — honest over clever. Verified against a real local webhook sink: a
200 records `delivered`, a 500 records `failed` with the HTTP status in the
detail, a deny-listed URL is `refused` at creation (and re-checked at
dispatch). Cross-tenant hooks are invisible/404.

### F069 — Tool allowlists/denylists
`policy.py` + `/policy` endpoints. Team tool policy: patterns are exact names
or trailing wildcards (`ec*`); **deny always wins**; if any allow rule exists
the team is in **allowlist mode** (everything not allowed is blocked). Enforced
at every entry point: direct call (403 `TOOL_DENIED_BY_POLICY`), custom-tool
wrappers by BOTH wrapper name and source tool (no bypass), and grant creation
(409 — you cannot even grant a denied tool). Patterns like `a*b` are rejected
as `POLICY_PATTERN_INVALID`; duplicate rules conflict (409).

### F070 — Extension SDK
Two halves:
- `sdk.py` — authoring: `ExtensionApp` with `@app.command(...)` / `@app.hook(...)`
  decorators. Declarations are validated locally at import time (name rules,
  placeholder/argument consistency, known event types), with in-process
  `render()` / `dispatch()` dry runs for development.
- `client.py` — `ExtensionsClient`: full typed REST client (httpx) over every
  extensions endpoint, context-manager lifecycle, bearer-only identity, and
  every non-2xx raised as `ExtensionsApiError(status_code, code, message)`
  parsed from the frozen error envelope. Tested end-to-end against the real
  ASGI service (tool call with a live MCP fixture server, commands, skills,
  catalog install/uninstall, typed errors).
**Security boundary:** the service never imports extension code; local hooks
run only in processes hosting the extension.

### F125 — Notion/internal-wiki integration
New package `integrations/connectors/wiki/` (`harness-wiki-connector`):
- `LocalMarkdownWiki` — a directory of `*.md` files
  (`# ` heading = title, path = page id);
- `HttpWikiConnector` — any wiki with a two-endpoint JSON API
  (`GET /pages`, `GET /pages/{id}`), optional bearer token, pluggable URL
  guard (the extensions service injects its private-host policy);
- `sync_wiki_to_skills` — idempotent sync into the versioned skills registry:
  creates `wiki/<slug>` skills, adds a **new version** on content change,
  leaves unchanged pages alone.
End-to-end tested against the real service + a real local wiki HTTP server:
create -> unchanged -> version bump, with v1 history preserved. README
documents the API contract and how to add a Notion adapter.
**Limit:** Notion itself is NOT implemented (needs OAuth + a live account —
unverifiable here; documented rather than faked).

### F150 — API docs + SDK distribution
- Interop artifacts re-captured for every session: `interop/openapi.json`
  (27 paths, up from 19 in S1) and **18 normalized golden fixtures** covering the S2
  journeys incl. error paths (400/403/409) and delivery transitions.
- **Distribution verification:** `scripts/verify_distribution.py` builds wheels
  for `harness-contracts`, `harness-extensions`, `harness-wiki-connector`,
  installs them into a **fresh venv** (no repo on `sys.path`, no editable
  installs), and runs a smoke journey there: app boots, commands route present
  in OpenAPI, command render OK via the client, catalog data present.
  Evidence log: `distribution-verify.log` (exit 0, `DISTRIBUTION_VERIFIED`).
**Limit:** no publication to a package index (no index authority in this
environment); wheel-install verification is the strongest available proof.

## Verification (all commands run, output inspected)

| Check | Result |
|---|---|
| `./.venv/bin/python -m pytest tests/ -q` (packages/extensions) | **90 passed** |
| `./.venv/bin/python -m pytest integrations/connectors/wiki/tests -q` | **8 passed** |
| `./.venv/bin/ruff check src tests scripts integrations/.../wiki` | clean |
| `scripts/capture_golden_s2.py` | 18 goldens + openapi, real servers, assertions inside |
| `scripts/verify_distribution.py` | exit 0, `DISTRIBUTION_VERIFIED` |

## Decisions log

1. **Hooks fire inline from the call path** (bounded 5 s, no retries) instead
   of a background queue: no broker exists yet; inline + recorded attempts is
   honest and testable. A queue is an S3+ option, not a silent feature.
2. **Policy checked at grant creation too** (409) — preventing a grant that
   can never succeed beats letting it fail later.
3. **`ExtensionApp` local hooks** exist only in host processes; the shared
   service stays code-free. This keeps the supply-chain story simple (VR014).
4. **Wiki sync adds versions, never edits** — reuses F065's immutable-version
   model; history loss would be silent data destruction.
5. **Notion adapter not written** — unverifiable credentials path; the HTTP
   connector contract + documented extension point is the honest deliverable.
6. **PEP 610 note:** `pip freeze` reports directly-installed wheels as
   `name @ file://`; distribution evidence therefore reads versions via
   `importlib.metadata`.

## Known limits carried forward

- No hook retry/backoff queue (documented above).
- Command `/` parsing UI is out of A5 scope (client surface).
- Notion connector remains a documented adapter point.
- No package-index publication; wheels verified locally only.
- Golden fixtures intentionally omit volatile fields (`<uuid>`, `<ms>`, ...);
  they are conformance examples, not byte-exact replays.
