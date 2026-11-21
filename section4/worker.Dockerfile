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
# W4: the web gate and the generated allow-list it reads (stdlib only as well).
# W10 adds the browser gate beside them: it shares egress.py with web_fetch so
# the two tools cannot disagree about what is reachable.
COPY section5/src/harness_sandbox/web_gate.py section5/src/harness_sandbox/egress.py \
     section5/src/harness_sandbox/egress_allowlist.json \
     section5/src/harness_sandbox/browser_gate.py \
     section5/src/harness_sandbox/browser_session.py /opt/harness-shell/
ENV HARNESS_WEB_GATE=/opt/harness-shell/web_gate.py
ENV HARNESS_BROWSER_GATE=/opt/harness-shell/browser_gate.py
# W11: the image gate (diff/sample). Stdlib file arithmetic — it spawns nothing,
# so it lives in `base` with the other gates and needs no Chromium.
COPY section5/src/harness_sandbox/image_gate.py /opt/harness-shell/
ENV HARNESS_IMAGE_GATE=/opt/harness-shell/image_gate.py
# Operator runtime patches (e.g. the OpenRouter transport patch). The worker
# container receives DSH_PATCH_FILES through the engine's scrubbed env, so the
# file must exist at the same path inside this image.
COPY deploy/patches /opt/harness/patches
# W4 slice 3: the reviewed MCP server list. Baked into the image on purpose —
# changing which servers a run may talk to is a rebuild (a review event), not a
# file a running service can swap. DSH_MCP_SERVERS names the path; the engine
# refuses to start a run when a declared list is unreadable or malformed.
COPY deploy/mcp /opt/harness/mcp
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "harness_orchestrator.engine_worker"]

FROM base AS isolation-tests
COPY section4/tests/unit/standin_worker.py /opt/probes/standin_worker.py
COPY deploy/pilot/worker.py /opt/probes/pilot_worker.py
ENV PYTHONPATH=/opt/probes

# M07 worker daemon: the only image that talks to the Docker engine. Same code as the worker
# plus the static Docker CLI; it runs as root because the socket is root-owned, and runs each
# worker as the workspace's owner (confine._worker_user), never as root.
FROM docker:29.5.3-cli AS docker-cli

FROM base AS daemon
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
USER root
ENTRYPOINT ["python", "-m", "harness_orchestrator.worker_daemon"]

FROM base AS worker
# Perf-audit 2026-11-21 item 6: pytest is allow-listed but was not installed,
# so agent test runs on Python repos could not work. Installed in the WORKER
# stage only (the daemon never executes agent tests; it must not carry it).
RUN pip install --no-cache-dir "pytest>=8,<9"
# W10: headless Chromium, in the worker stage only — the daemon never renders, so
# it must not carry the ~300 MB. Debian's chromium is used rather than Node +
# Playwright: the worker has no Node runtime and the tool is render-and-look.
# fonts-dejavu-core so a text page renders glyphs instead of boxes.
USER root
RUN python -c "from pathlib import Path; p = Path('/etc/apt/sources.list.d/debian.sources'); p.write_text(p.read_text().replace('http://deb.debian.org', 'https://deb.debian.org'))" \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 install -y --no-install-recommends \
        chromium fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
