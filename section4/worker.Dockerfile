FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home worker
COPY contracts/pyproject.toml /build/contracts/pyproject.toml
COPY section4/pyproject.toml /build/section4/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/pip python -c "import tomllib, subprocess; from pathlib import Path; projects=[tomllib.loads(p.read_text())['project'] for p in Path('/build').glob('*/pyproject.toml')]; deps=[d for p in projects for d in p['dependencies'] if d != 'harness-contracts']; deps += [d for p in projects for d in p.get('optional-dependencies', {}).get('live', [])]; subprocess.check_call(['pip','install','setuptools','wheel',*deps])"
COPY contracts/src /build/contracts/src
COPY section4/src /build/section4/src
RUN pip install --no-deps --no-build-isolation /build/contracts '/build/section4[live]' \
    && rm -rf /build
# Section 5's shell gate for the engine's bash tool (stdlib only; loads its siblings by path).
COPY section5/src/harness_sandbox/shell_gate.py section5/src/harness_sandbox/policy.py \
     section5/src/harness_sandbox/confinement.py /opt/harness-shell/
ENV HARNESS_SHELL_GATE=/opt/harness-shell/shell_gate.py
# Operator runtime patches (e.g. the OpenRouter transport patch). The worker
# container receives DSH_PATCH_FILES through the engine's scrubbed env, so the
# file must exist at the same path inside this image.
COPY deploy/patches /opt/harness/patches
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "harness_orchestrator.engine_worker"]

FROM base AS isolation-tests
COPY section4/tests/unit/standin_worker.py /opt/probes/standin_worker.py
COPY deploy/pilot/worker.py /opt/probes/pilot_worker.py
ENV PYTHONPATH=/opt/probes

FROM base AS worker
