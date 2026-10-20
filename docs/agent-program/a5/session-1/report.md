# A5-S1 — External MCP and trusted extensibility

Agent A5, program session 1 of 5. Date 2026-10-17 (Europe/Berlin).
Baseline commit `31bf4077e9b567b1f50ecb99818d501baab3115a` (matches reference; pre-existing
untracked/modified files preserved: `AGENTS.md`, `docs/master-prompts/`, `docs/reviews/`).

Machine-readable ledger: `ledger.json` (same directory). Interop artifacts:
`interop/openapi.json`, `interop/golden/01..15_*.json`.

## What was built

New package `packages/extensions` (`harness-extensions`, Python 3.13, own venv
`packages/extensions/.venv`). One FastAPI service under `/api/v1/extensions` following
`contracts/api-conventions.md`: camelCase, UUID ids, frozen error envelope
`{"error": {"code", "message"}}`, bearer identity that never trusts body `teamId`.

| Module | Responsibility |
| --- | --- |
| `config.py` | `HARNESS_EXTENSIONS_` env settings; deny-by-default defaults |
| `store.py` | durable SQLite store (`ext_*` tables) — registry, grants, skills, plugins, wrappers |
| `schema_guard.py` | tool-schema hardening (bytes/depth/nodes/cycles, object root) + exact-budget result-block truncation with `[truncated]` marker |
| `url_policy.py` | URL validation; loopback/private/link-local denied unless `allow_private_hosts` |
| `mcp_client.py` | REAL external MCP client (official SDK, `streamable_http_client`): initialize → tools/list → tools/call; per-tool rejection instead of trust; stdio entry point for local workers only |
| `scopes.py` | `authorize_tool_call`: deny-by-default, expiry fail-closed, run/project binding, PROJECT_CHECK_UNAVAILABLE fail-closed |
| `skills.py` | skills/playbooks with immutable versions + sha256 digests |
| `marketplace.py` | curated bundled catalog: digest-verified load, install/update/uninstall, terminal admin-only revoke; tampered payloads dropped |
| `calls.py` | linear call path (authorize → resolve → call → bound → record) + wrapper argument narrowing (ARG_NOT_ALLOWED / ARG_PINNED / ARG_UNKNOWN) |
| `api.py` | all routes + local error-envelope handlers; principal via `app.state.get_principal` (503 `AUTH_NOT_CONFIGURED` fail-closed) |
| `__main__.py` | standalone uvicorn, postgres-backed bearer dep (`make_bearer_dep`) |

19 OpenAPI paths at S1 capture (regenerated snapshots show the current surface): servers (CRUD + health + tools), grants (create/list/delete), custom-tools
(create/list/delete/call), calls, skills (create/list/get/version), catalog (list/get/install),
plugins (list/uninstall/revoke).

## Decisions (alternatives rejected)

- **Durable SQLite store** vs reusing Section 1 Postgres tables: the extensions plane must run
  standalone (local-first, no compose dependency); PG bearer auth is still the deployed identity path.
- **Curated bundled catalog** vs live remote registry: trust boundary stays inside the reviewed
  artifact; publishing/submissions/payments are explicitly out of scope, not faked.
- **Wrappers** vs arbitrary user-authored tool code: wrapping keeps the executor's allow-list
  surface unchanged (no new code-execution path).
- **Returns 404 (not 403) for cross-tenant resources**: no-oracle; team B cannot probe team A ids.

## Verification (commands actually run)

```
cd packages/extensions
./.venv/bin/python -m pytest tests/ -q          # 65 passed (incl. real MCP fixture traffic)
./.venv/bin/ruff check src tests scripts        # All checks passed
# from repo root:
./packages/extensions/.venv/bin/python packages/extensions/scripts/capture_golden.py
# → openapi.json + 15 golden fixtures; all captured live (statuses 201/200/200/200/…/403)
```

Hostile-input suite results (real wire traffic, SDK fixture server on ephemeral port):
`deep_bomb` depth-rejected, `huge_desc` (1 MiB description) size-rejected,
`non_object_root` type-rejected, `ref_bomb` neutralized by construction ($refs never
dereferenced), server reported `degraded` with `rejectedToolCount: 3` — never silently healthy.
Timeout ≥ call budget returns 504 `MCP_TIMEOUT`; dead server after registration → 502
`MCP_UNREACHABLE`; cancelled/oversized/truncated results carry explicit markers.

## Feature dispositions

| F | Capability | State |
| --- | --- | --- |
| F061 | First-class MCP server registry | VERIFIED IMPLEMENTED |
| F062 | MCP permission scopes | VERIFIED IMPLEMENTED |
| F063 | MCP server health checks | VERIFIED IMPLEMENTED |
| F064 | Custom tool builder | VERIFIED IMPLEMENTED (wrappers; limits documented) |
| F065 | Reusable skills or playbooks | VERIFIED IMPLEMENTED |
| F066 | Plugin marketplace | PARTIAL (curated-only; no publishing/payments) |

## Risk dispositions (owned list; S1-relevant shown first)

- VR014 supply chain → PARTIAL CONTROL (digest verification, schema/description/URL guards,
  deny-by-default grants, terminal revoke; no publisher signing yet).
- VR020 procurement evidence → PARTIAL CONTROL (OpenAPI + goldens + tests captured from real
  runs; full package in S5).
- VR040 docs/support, VR031 ROI, VR036/VR037/VR038/VR042/VR044/VR050 market risks → OPEN or
  EXTERNAL VALIDATION REQUIRED; remediation scheduled in A5-S2…S5 or outside code.

## Known limits (honest, not hidden)

- No third-party plugin publishing, payments, or remote registry protocol.
- No background health prober (on-demand probes only).
- DNS-rebinding window between URL validation and connect is documented in `url_policy.py`;
  mitigation is S5 network policy / `allow_private_hosts` discipline.
- Call observations are returned to the caller but not persisted in the extensions store yet
  (audit trail is the caller's responsibility in S1; revisit if S3/S4 need refetch).
- Pact-style contract tests against `harness_contracts` for this service are planned in S2
  alongside the extension SDK.

## Next session

A5-S2: commands/hooks/tool config (F067–F070, F125, F150) — extension SDK, hook interfaces,
allow/deny config, wiki connector groundwork, per-subagent tool permissions.
