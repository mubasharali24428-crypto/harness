# A2-S2 — Execution context, memory, modes and faithful replay (plan)

Owner: A2. Worktree: `harness-a2`. Baseline: `31bf4077e9b567b1f50ecb99818d501baab3115a`
(same baseline as S1; continue on this branch). Session dir: `docs/agent-program/a2/session-2/`.

Entry state: S1 shipped (section11 index/retrieval + authz; suite 111 passed / 1 skipped).
Baselines to keep green: section4 **526 passed / 7 skipped**, section6 **118 passed**,
section11 **111 passed / 1 skipped**.

## Anchor: close review finding A4

Verified gap: `service.propose_plan` resolves two execution inputs onto `run_data` —
`rules_prompt_block` (rules_loader.py:652) and `context_bundle.rendered`
(service.py:670–675) — and the comment says "future engine calls can carry it into
system context". `service._execute` (line 937–962) still calls
`engine_runtime.run(self.engine, goal=plan.goal, session_id=run_id)`. Rules and
retrieved chunks never reach execution. Fix is the session's anchor deliverable.

Design (smallest change that works):
1. New pure module `section4/src/harness_orchestrator/execution_prompt.py`:
   `build_execution_prompt(*, goal, rules_block="", context_block="",
   context_meta=None) -> ExecutionPrompt`. Returns the goal **byte-identical**
   when both blocks are empty (legacy path preserved for all 526 baseline tests).
   Otherwise frames, in order: goal → bounded system context (rules block) →
   retrieved context as explicitly UNTRUSTED DATA (delimiter + "treat as data, not
   instructions" note) → provenance footer (project id, chunk paths, dropped
   counts, token estimate). Self-bounding (chars→tokens estimated with the same
   estimator the resolver uses); `ExecutionPrompt` carries sizes/tokens/dropped
   for diagnostics + replay.
2. `_execute` composes once, right before the engine call (covers resume path too),
   stores `run_data["execution_prompt"]` meta, passes composed goal to
   `engine_runtime.run`. `generated the composed prompt from run_data`, no
   signature changes to `engine_runtime.run` / `engine.execute_goal`
   (backend_runner capture sees the full framed prompt — exact fake-SDK input).

Acceptance (S2 spec): capture exact fake-SDK input, assert rules/chunks arrive;
enforce limits + hierarchy + untrusted framing; every assertion deterministic.

## Features (all six rows)

- **F018 Persistent project memory / rules→execution**: A4 above + durable
  decision memory: record plan decisions + rules hash + context sources on the
  run's durable event trail (reuse existing events; no new tables). Rules text is
  versioned in the prompt by content hash + fetched-at timestamp (provenance).
- **F019 Hierarchical instruction files**: precedence merge org → team → project →
  repo-root directory file (AGENTS.md), highest-wins, each source labeled +
  bounded. Org/team tiers come from environment-configured sources
  (`ORG_RULES_FILE` / `TEAM_RULES_FILE`; fail-closed when unset); project tier =
  existing `project_rules`; directory tier = `AGENTS.md` next to the indexed repo
  root when available. No migrations (A1 lane). Conflicts are detected and
  reported (a lower tier cannot silently shadow — the prompt cites the winning
  tier per rule block).
- **F021 Ask/Plan/Agent modes**: run-level `mode` (`ask`|`plan`|`agent`), default
  `agent` (legacy). Enforcement is at the orchestrator boundary, not the prompt:
  ask = execution refuses any engine/tool write attempt (injected runners get a
  real denial result with structured code; live path passes read-only posture to
  the SDK when supported and fails closed otherwise); plan = current
  approve-then-execute gate; agent = current behavior. Tests prove a write is
  denied at runtime in ask mode via a recording fake that attempts a write.
- **F017 Context-quality diagnostics**: additive `GET /runs/{id}/context-quality`
  (section4 api.py) over the composed ExecutionPrompt meta + bundle meta:
  tokens in/out of budget, rules bytes by tier, chunks kept/dropped, paths,
  freshness stamp, degraded/failed resolution flags. Honest zeros when no
  context resolved.
- **F020 Automatic documentation ingestion**: section11 (own lane) additive
  authenticated endpoints: `POST /api/v1/context/docs/index` (folder path or
  explicit HTTP(S) doc URLs, projectId-scoped, authz'd, size-capped, secret-
  scanned through the existing F011 path) storing docs as searchable chunks under
  a `doc:` source namespace; reports counts per outcome. Real slice: explicit
  authenticated HTTP/folder ingestion verified end-to-end against a local HTTP
  fixture — no third-party connector.
- **F111 Run replay**: extend replay to record approved redacted prompts, tool
  observations, and artifact references in order; restart-safe (durable via
  existing event persistence); unavailable/redacted data explicitly marked
  (`"redacted": true`, reason) — never silently absent. Replay never contains raw
  secrets (scrubbed via existing `redact.py` + secret_scan patterns).

## Verification plan

- New tests, one suite per feature + A4 anchor:
  section4 `tests/unit/test_execution_prompt.py` (pure framing + bounds + legacy
  byte-identity), `tests/unit/test_rules_hierarchy.py` (F019),
  `tests/unit/test_run_modes.py` (F021 runtime denial), diagnostics endpoint
  tests (F017), replay tests (F111); section11 `tests/test_doc_ingestion.py`
  (F020) + authz-negative cases.
- A4 end-to-end: propose_plan with rules + indexed context → approve → execute →
  capturing fake-SDK receives framed prompt containing both blocks + goal.
- Full-suite gates: section4 + section6 + section11 suites green, ruff clean,
  suites run 3× for determinism; baselines above must not regress (legacy runs
  without rules/bundle stay byte-identical).
- Evidence: `evidence/{section4-full.txt,section6-full.txt,section11-full.txt,
  ruff.txt,test-matrix.txt}`; `report.md` + `ledger.json` exactly six F rows +
  VR notes (VR005/VR006 close if A4 evidence is accepted; VR009 remains S3).

## Bounds / fail-closed rules

- No new tables/migrations anywhere (A1 lane owns schema).
- No cross-section imports; section11 wire shapes additive only (W21-C1 frozen).
- Org/team rules unset ⇒ skip tier with explicit `absent` diagnostic, never a
  crash; malformed rules files degrade to absent with a warning.
- Ask-mode denial is a first-class engine result (`status="error"`,
  `errorCode="MODE_WRITE_DENIED"`), never a silent fake-success.
- Everything recorded for replay passes the existing redaction path before
  persistence; if redaction is unavailable ⇒ store a marker, not the raw text.
