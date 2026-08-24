# MISSION 16 — WAVE 6 (roadmap program, mission 2) — TASK GRAPH

> ALEX-FORCE, 20 agents. Baseline: df53c2d. Consumes part4 §W5+ severity
> order: perf/scale program (raw-16) + UI element systemization (raw-17)
> + edge-case corpus (raw-18). Migration slots: next free 0025 is TAKEN
> (ws_fingerprint) → next free 0026; verify ls before naming.

## LANES

| Lane | Mission | Owned paths | Squad |
|---|---|---|---|
| X1 | Perf: runs-index query at 100k events — covering index + cursor EXPLAIN evidence | section1/migrations/0026_runs_index_tuning.sql · section4 runs_index.py · tests | ALPHA |
| X2 | Perf: meeting message pagination (unbounded render fix server-side) | section6/src/harness_meeting/api.py · tests | ALPHA |
| X3 | Perf: WS replay cap + seq-window resume proof | section1 eventbus replay path · tests | ALPHA |
| X4 | UI systemization: migrate remaining pages onto components/ui primitives (login, meeting, review, dashboard root) | section7 app/** pages · tests | BETA |
| X5 | Edge-case corpus wave A: lifecycle double-invocation corpus from raw-18 seeds into conformance tests | section*/tests/conformance/ edge cases · shared fixtures | BETA |
