# Section 10 — Onboarding, Demo Repo & Self-Hosted Deployment Packaging

## Purpose

Get a new user from zero to a working instance and through their first real task.

## Contract boundary

**Exposes:** `/api/v1/onboarding/...` REST endpoints (see `openapi.json`), the
`deploy/` assembly point (docker-compose, Dockerfiles, `serve.py`), and the seeded
demo repository at `demo_repo/`.

**Depends on:** the contracts of all other sections, but builds against stubs —
no runtime import on any other section's code.

## Key deliverables

| Deliverable | Location | Plan reference |
|---|---|---|
| Onboarding steps (frozen order) | `src/harness_onboarding/steps.py` | §11, §16 Q9 |
| Security checklist (pre-connect) | `src/harness_onboarding/checklist.py` | §23 Q17 |
| Guided demo walkthrough | `src/harness_onboarding/guided_demo.py` | §11, §16 Q10, §23 Q19 |
| Demo repository (seeded) | `demo_repo/` | §11, §16 Q7, §23 Q18 |
| Telemetry consent policy | `src/harness_onboarding/telemetry.py` | §25, §16 Q25, §351-357 |
| State persistence (JSON) | `src/harness_onboarding/store.py` | — |
| HTTP API | `src/harness_onboarding/api.py` | — |
| OpenAPI fragment | `openapi.json` | contracts §API convention |
| Deployment packaging | `deploy/` | §7, §16 Q4 |

## Running

```sh
cd section10
python -m venv .venv && .venv/bin/pip install -e ../contracts -e ".[dev]"
./.venv/bin/uvicorn harness_onboarding.api:app --port 8010 --reload
```

Or via the deployment packaging (see `deploy/README.md`):

```sh
cd deploy && docker compose up
```

## Testing

```sh
./.venv/bin/python -m pytest tests/ -q     # 49 tests
./.venv/bin/ruff check src tests            # must be clean
```

To regenerate golden fixtures after an intentional contract change:

```sh
./.venv/bin/python tests/fixtures/generate_fixtures.py
```

## Brief traceability

§7, §11, §16 Q4/6/7/9/10/25-28, §23 Q17-19, §351-357.
