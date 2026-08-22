# API Conventions (frozen)

All backend sections MUST follow these. Frontend sections build against them.

## REST

- One prefix: `/api/v1/...`
- JSON over HTTPS only.
- Auth: `Authorization: Bearer <session-token>` issued by Section 1's `/auth/login`.
- Resource naming: plural nouns, kebab-case path segments (`/agent-runs/{id}/plan`).
- Actions that are not CRUD: `POST /resource/{id}/{action}` (e.g. `POST /runs/{id}/pause`).
- Success body: the resource object, camelCase keys.
- Error body (all non-2xx): `{"error": {"code": "<machine_code>", "message": "<human>"}}`.
- Pagination (when needed): `?limit=&cursor=` returning `{"items": [...], "nextCursor": ...}`.
- IDs are UUIDv4 strings; `seq` fields on events are monotonically increasing integers.

## Auth context

Shared middleware resolves every request to `{ userId, teamId, role }` before any handler runs.
Role is one of `admin | engineer | viewer | guest`. Guests can review but never approve plans or merge.

## WebSocket

- Endpoint: `GET /api/v1/ws/{projectId}?afterSeq=<int>` (token via query param `token`).
- Server pushes exactly the `EventEnvelope` shape from `harness_contracts.events`:
  `{"type", "payload", "ts", "seq", "projectId", "runId"}`.
- On connect with `afterSeq`, missed events are replayed from persistence first, then live.

## Versioning

- Breaking change to anything in this package => bump version + update consumers' mocks in the same commit.
- Additive changes allowed without bumping major.
