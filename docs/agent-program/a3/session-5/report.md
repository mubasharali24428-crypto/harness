# A3-S5 — Agent verification loops and evidence strength

Package `packages/verification` (own venv, stdlib-only runtime; `coverage` used when present). Modules: `discovery`, `mutation`, `repair`, `testgen`, `contract`, `scorecard`, `cli` (`harness-verify`). Each is importable and runnable without any other session.

## Evidence
| Command | Result |
|---|---|
| `cd packages/verification && .venv/bin/ruff check src tests` | clean |
| `.venv/bin/python -m pytest tests -q` | 23 passed, ~29 s |
| `harness-verify openapi-drift` on Section 5 router vs committed `section5/openapi.json` | exit 1: 5 undocumented routes, 4 status-code differences (`section5-openapi-drift.json`) |
| `harness-verify mutate` on `quota.py` with the S3 quota tests, 14 sampled mutants | score 0.50 before → 0.93 after adding exact-threshold tests (`mutation-quota*.json`) |
| `harness-verify scorecard --no-mutation` over the ten S3/S4 modules | `pass_weak`: 16 test files selected (static), tests executed and passed, lint executed, strength not measured (`scorecard-s3-s4-modules.json`) |

The quota mutation run is the clearest proof the tool earns its keep: it showed my own S3 guard tests did not pin `group_stats`, the MiB/KiB conversions or the `>`-vs-`>=` boundaries; those tests were written and the score rose. One survivor remains (`1024->1025` on one conversion).

## Scorecard rules (conservative by construction)
fail if any executed check fails • insufficient_evidence if tests did not execute or a changed source file has no test • pass_weak if tests pass but strength was not measured • pass only with executed tests + executed mutation ≥ threshold. Skipped/blocked evidence is printed, never counted as success.

## Limits
- Python-first: mutation and execution cover pytest only; vitest/jest are discovered but reported blocked.
- Characterization tests pin current behaviour, including current bugs; they are a regression net.
- Mutation samples (default 30) and is bounded by wall clock; equivalent mutants count as survivors.
- Discovery is static; reflection/dynamic imports are invisible.
- `repair` has no CLI by design (the fixer is code); it is an API: `run_repair(root, tests, proposer, ...)`.

## Rollback
Delete `packages/verification` and `docs/agent-program/a3/session-5`.

## Integration proposals
- A2: pass the SDK/model fixer as `Proposer` and the model test generator as `Generator`; record `Scorecard.evidence_digest` on the run event.
- A5: run `harness-verify openapi-drift` and `scorecard` in CI; decide whether to regenerate `section5/openapi.json` (A3 left it untouched).
