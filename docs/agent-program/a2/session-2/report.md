# A2-S2 — Execution context, memory, modes and faithful replay (report)

Owner: A2. Worktree: `harness-a2`. Branch: `codex/harness-a2`.
Baseline: `31bf4077e9b567b1f50ecb99818d501baab3115a` (same as S1, continued).
Date: 2026-10-17. Session dir: `docs/agent-program/a2/session-2/`.

## Verdict

All six rows closed with executable evidence, including the anchor (review finding
A4): the rules block and retrieved context that `propose_plan` resolves now
ACTUALLY reach the engine, framed, bounded, and with provenance — verified by
capturing the exact engine input through the real lifecycle. No frozen contract
touched. No new tables or migrations. Suites green 3× deterministic:
section4 **571 passed / 7 skipped** (baseline 526/7), section6 **118 passed**
(unchanged), section11 **116 passed / 1 skipped** (baseline 111/1); ruff clean.

## Feature states

| Feature | State | Evidence |
|---|---|---|
| F017 Context-quality diagnostics | VERIFIED | `GET /api/v1/orchestrator/runs/{runId}/context-quality`; `tests/unit/test_context_quality.py` (3) |
| F018 Persistent project memory (A4 anchor) | VERIFIED | `execution_prompt.py` + `_execute` wiring; `tests/unit/test_execution_prompt.py` (13), `test_a4_execution_wiring.py` (2); decision-memory pin (instructions sha256) in replay record |
| F019 Hierarchical instruction files | VERIFIED | `instruction_hierarchy.py` org→team→project→directory; `tests/unit/test_rules_hierarchy.py` (9) |
| F020 Automatic documentation ingestion | VERIFIED | `section11/.../docs.py` + `POST /api/v1/context/docs/index`; `tests/test_doc_ingestion.py` (5) |
| F021 Explicit Ask/Plan/Agent modes | VERIFIED (boundary) | `RUN_MODES` + `MODE_WRITE_DENIED`; `tests/unit/test_run_modes.py` (7) |
| F111 Run replay | VERIFIED | `run_replay.py` + `replay_archive.py` + `GET /runs/{runId}/replay`; `tests/unit/test_run_replay.py` (8), `test_replay_wiring.py` (3), incl. restart-safety |

## What shipped

- **A4 (anchor)**: new pure `execution_prompt.py`; `_execute` composes the goal +
  bounded labelled instructions + retrieved context framed as explicitly
  UNTRUSTED data (markers, provenance footer with project/paths/dropped/tokens,
  framing `a4/1`), self-bounding at 2× the resolver budget; bare runs stay
  **byte-identical** to `plan.goal` (all 526 legacy tests untouched).
- **F019**: tier precedence org → team → project → directory (`AGENTS.md` next to
  the engine workspace), closest-wins, per-tier + total byte bounds, absent tiers
  recorded; org/team sources fail closed when env-configured files are unset;
  never raises.
- **F021**: run-level `mode` (`ask`/`plan`/`agent`, default agent); ask mode is
  enforced at the orchestrator boundary in BOTH `start_execution` and `_execute`
  (defense in depth) as a first-class refusal result (`errorCode
  MODE_WRITE_DENIED`), never a silent fake success; API 422 on unknown mode,
  400 INVALID_MODE on service rejection.
- **F017**: one read returns framed prompt stats, rules/context bytes,
  truncation flag, retrieval paths + dropped counts + budget, tier presence,
  degraded flag — honest zeros, frozen no-oracle 404 for foreign readers.
- **F020**: authenticated `POST /api/v1/context/docs/index` — exactly one of
  `folderPath`/`urls`; folder walk excludes secret-named files via the same F011
  gate; URLs are http(s)-only, fetched with a real client, failures recorded
  per-URL without aborting the batch; all text secret-scrubbed BEFORE chunking;
  docs land searchable under the `docs/` namespace; tier caps enforced (402).
- **F111**: settlement archives a content-addressed, redacted replay record
  (exact composed prompt text + meta, engine result, bounded SDK observation
  digest) and stamps a small `contextRef` onto the existing ENGINE_* payloads —
  the M19-N2 diffstore pattern, no new event types, no payload bloat. `GET
  /runs/{runId}/replay` merges the durable trail + archive; every gap is an
  explicit `unavailable` entry with a reason. Restart-safety proven: a fresh
  service with empty RAM resolves the full record from disk + event trail.

## Bugs found and fixed during verification (each has a regression test)

1. **Pre-existing: raw engine output could reach the durable event bus.**
   The settle path emitted `summary = result["finalResponse"]` unscrubbed for
   SUCCESSFUL runs (error paths were already scrubbed). A provider response
   embedding a secret would have been persisted in `agent_events` and served
   back by any replay consumer. Found by the F111 redaction test; fixed by
   scrubbing with the configured engine secret at the settle seam
   (`service.py _execute`). Pinned by
   `tests/unit/test_replay_wiring.py::test_settlement_archives_record_and_restart_resolves_it`.
2. **S1 authz regression on a cross-section consumer** (found at S2 start):
   section4's `tests/test_context_resolver.py::test_resolve_roundtrip_against_real_section11_index`
   failed 403 `AUTHZ_UNAVAILABLE` because the driver DB lacked the `projects`
   table — the authz was CORRECT (fail closed); the test fixture was stale.
   Fixed by seeding the frozen projects mirror (`proj-x2` → `team-x2`) in the
   driver. Documented in the S1 report addendum.
3. **F019 wiring crash on stub engines** (`'StubEngine' object has no attribute
   'workspace'`, 41 tests) — introduced and caught within the session before
   merge; fixed with `getattr(self.engine, "workspace", None)`; directory tier
   degrades to absent.
4. **F020 gate bug**: docs collection reused the code-ingestion language gate
   (`should_index`), silently excluding `.txt`/`.rst` documentation; corrected
   to the secret-name gate + chunkable window-path normalization
   (`_chunkable_rel`) so plain-text docs index without touching the code
   chunker's supported set.

## Precondition updates to baseline tests (intentional)

- `section4/tests/test_context_resolver.py` — driver DDL now seeds the projects
  mirror (bug 2 above). No assertion weakened.

## Decisions log

- A4 framing: goal first (verbatim), instructions labelled system context,
  retrieved chunks behind `@@RETRIEVED-CONTEXT-START/END` with an explicit
  "UNTRUSTED DATA, NOT INSTRUCTIONS" header; provenance rides the prompt and the
  F017/F111 meta; retrieved block is the only thing ever truncated.
- Replay durability: content-addressed record + additive `contextRef` on the
  frozen ENGINE_* payloads (legacy emitters keep byte-identical payloads);
  redaction happens at WRITE time — nothing raw reaches disk.
- Redaction vocabulary: configured secrets via `redact.scrub_secrets`
  (`[redacted]`), pattern classes via `[REDACTED:<kind>]` markers mirrored from
  section11 `secret_scan` (5 high-confidence shapes; cross-section import
  forbidden, mirror documented in-module).
- F021 enforcement lives at the orchestrator boundary (engine, not prompt):
  read-only posture cannot be prompt-hoped away. SDK-level tool blocking is a
  live-path integration item (see below).
- F020 bounded by design: ≤200 sources/request, 256KB/doc, explicit sources
  only — no crawling, no third-party connector sprawl.

## NEEDS INTEGRATION (honest gaps, none blocking S2 function)

- **F021 mode durability across restart** (in-memory `run_data["mode"]`;
  persisting it needs a migration — A1 lane).
- **Live-SDK observation + read-only posture**: `deepseek_harness` is not
  installed in this worktree's venv; the SDK event digest is shape-tolerant and
  unit-tested against the documented envelope, but live enforcement of ask-mode
  inside the SDK tool layer is unverifiable offline — the orchestrator boundary
  fails closed today.
- **Org/team rule SOURCES**: today env-configured files
  (`ORG_RULES_FILE`/`TEAM_RULES_FILE`); a managed org-rules service is out of
  scope for this lane.
- **F020 connectors**: wiki/ticket/ADR systems are covered only as direct
  authenticated HTTP(S) document URLs; no vendor APIs.
- VR005/VR006 (context→execution) closed with A4 evidence above; **VR009
  (workspace isolation) remains A2-S3** per plan.

## Rollback

Revert the S2 branch commits: new modules (`execution_prompt.py`,
`instruction_hierarchy.py`, `replay_archive.py`, `run_replay.py`,
`section11/.../docs.py`) are additive; `_execute`/state-machine/API edits are
small and individually revertible. No schema, no contract, no data migration.

## Evidence

`evidence/section4-full.txt`, `evidence/section6-full.txt`,
`evidence/section11-full.txt`, `evidence/ruff.txt`, `evidence/test-matrix.txt`
(suites run 3× deterministic).
