# Decision: external agent adapter protocol v1 — observer leases only

**Date:** 2026-11-24 · **Status:** PROPOSED by engineering; ships as an observer-only seam under
the D-06 default ("no wider access than explicit grants"). Owner signature pending (D-06 is a
DEFAULT row in `2026-11-24-owner-register-defaults.md`).
**Code:** `section4/src/harness_orchestrator/adapters.py`, migration `0079_run_adapter_leases.sql`,
tests `section4/tests/unit/test_external_agent_adapter.py`.

## Context
Shared runs (collab pilot) and W13 BYO runners exist. External agents (another vendor's coding
agent, a team's own bot) want to follow a run and contribute proposals. Without a defined seam,
each integration would invent its own access path.

## Decision
- `POST /api/v1/orchestrator/runs/{runId}/adapters/register` — a human **writer** of the run
  registers an external agent: `agentId`, `protocolVersion` ("1"), `capabilities` from the closed
  set `read_events | read_plan | propose_patch | comment`, `maxScope` (relative paths, same
  traversal rules as approvals), `pubkey` (only its sha256 is stored).
- The response is a lease: `mode: "observer"`, `authority: []`, `digest` = sha256 of the canonical
  registration. `maxScope` is returned as `requestedScope`: a request for a human to read, never
  applied.
- `GET .../adapters` lists leases to run readers. One lease per (run, agent): re-registering with
  a different body is 409, so scope cannot be widened by re-registration.
- Protocol types stay route-local (section4), not in frozen contracts: v1 is a pilot seam and a
  contract bump would force TS mirror + mocks for an unproven surface.

## Threat model (no permission expansion via adapter)
| Threat | Control |
|---|---|
| Adapter claims approval or execution power | closed capability enum; `approve`, `grant_*`, `merge`, `execute` -> 422 |
| Adapter widens its own scope | scope is recorded, never applied; widening only via leader approval routes, which require a human Principal; re-registration -> 409 |
| Cross-tenant probing via register | run guard: foreign == unknown run, identical 404 envelope (tested) |
| Low-privilege member registers an agent | write role required; viewer -> 403 |
| Key material leak | only the pubkey's sha256 is stored or returned |
| Path traversal in requested scope | `collab.validate_paths` (422) |

## Not in v1
The agent authenticating as itself (W13 runner-token style), event streaming to the adapter,
lease revocation/expiry, and the TS client stub. Each needs this record amended first.
