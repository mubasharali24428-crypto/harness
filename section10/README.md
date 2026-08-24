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

The onboarding API currently runs **standalone** (it is not yet one of the
compose services — `deploy/docker-compose.yml` boots the seven backend services
plus frontend; see `deploy/ONBOARDING.md` for the full-stack flow):

```sh
cd section10
python -m venv .venv && .venv/bin/pip install -e ../contracts -e ".[dev]"
./.venv/bin/uvicorn --factory harness_onboarding.api:make_app --port 8010 --reload
```

(`api.py` exposes a `make_app()` factory, not a module-level `app`, so pass
`--factory`.)

Runtime configuration comes from environment variables — copy the repo-root
[`../.env.example`](../.env.example) and set what you need (none are required
for this service's tests).

## Testing

```sh
./.venv/bin/python -m pytest tests/ -q     # 65 tests (verified 2026-08-23)
./.venv/bin/ruff check src tests            # must be clean
```

The count above is hand-updated and has rotted before (it read "49" through
three waves). Re-derive it rather than trusting it:

```sh
./.venv/bin/python -m pytest tests/ --collect-only -q | tail -1
```

To regenerate golden fixtures after an intentional contract change:

```sh
./.venv/bin/python tests/fixtures/generate_fixtures.py
```

## Brief traceability

§7, §11, §16 Q4/6/7/9/10/25-28, §23 Q17-19, §351-357.
