"""Canned `docker ps` output — no Docker daemon, no compose, no live stack."""
from __future__ import annotations

from pathlib import Path

import stack_tags
from stack_tags import (
    EXPECTED_BACKEND_SERVICES,
    WORKER_DAEMON_SERVICE,
    image_tag,
    parse_ps,
)


def _ps(overrides: dict[str, str] | None = None, omit: tuple[str, ...] = ()) -> str:
    tags = dict.fromkeys(EXPECTED_BACKEND_SERVICES, "vw28s6")
    tags.update(overrides or {})
    lines = [
        f"{s}|harness-{s}-1|harness-backend:{tags[s]}"
        for s in EXPECTED_BACKEND_SERVICES
        if s not in omit
    ]
    lines.append(f"{WORKER_DAEMON_SERVICE}|harness-worker-daemon-1|"
                 "harness-worker-daemon:vw27s2")
    lines.append("|harness-ci-scratch-pg|pgvector/pgvector:pg18")
    lines.append("malformed-line-without-separators")
    return "\n".join(lines) + "\n"


def _report(overrides=None, omit=()) -> str:
    return "\n".join(stack_tags.stack_report(_ps(overrides, omit)))


def test_parse_ps_uses_the_container_name_when_the_service_label_is_empty():
    parsed = parse_ps(_ps())
    assert parsed["harness-ci-scratch-pg"] == (
        "harness-ci-scratch-pg", "pgvector/pgvector:pg18")
    assert len(parsed) == len(EXPECTED_BACKEND_SERVICES) + 2


def test_image_tag_handles_registry_ports_digests_and_untagged_refs():
    assert image_tag("harness-backend:vw28s6") == "vw28s6"
    assert image_tag("quay.io/keycloak/keycloak:26.1") == "26.1"
    assert image_tag("localhost:5000/harness-backend:v1") == "v1"
    assert image_tag("bitnamilegacy/minio@sha256:451fe6") == "<digest>"
    assert image_tag("busybox") == "<untagged>"


def test_all_backend_services_on_one_tag_agree():
    report = _report()
    assert "Agreement: AGREE (vw28s6)" in report
    assert "MIXED" not in report
    assert "Missing: none" in report
    assert f"Backend services: {len(EXPECTED_BACKEND_SERVICES)} of " in report
    assert "Worker daemon: harness-worker-daemon:vw27s2" in report


def test_one_service_on_a_different_tag_is_reported_as_mixed():
    report = _report({"orchestrator": "vw27s3"})
    assert "Agreement: MIXED" in report
    assert "vw27s3" in report
    assert "1 service(s): orchestrator" in report
    assert "10 service(s):" in report
    assert "vw28s6" in report


def test_a_missing_backend_service_is_named_and_counted():
    report = _report(omit=("models",))
    assert "Missing: models" in report
    assert "10 of 11 expected running" in report
    assert "Agreement: AGREE (vw28s6)" in report


def test_no_backend_containers_is_an_honest_unknown():
    report = "\n".join(stack_tags.stack_report(
        f"{WORKER_DAEMON_SERVICE}|harness-worker-daemon-1|"
        "harness-worker-daemon:vw27s2\n"))
    assert "Agreement: UNKNOWN (no backend containers found)" in report
    assert "Missing: " + ", ".join(EXPECTED_BACKEND_SERVICES) in report
    assert "Worker daemon: not running" not in report


def test_main_exits_zero_and_prints_the_mix_without_docker(monkeypatch, capsys):
    class Result:
        returncode = 0
        stdout = _ps({"orchestrator": "vw27s3"})
        stderr = ""

    monkeypatch.setattr(stack_tags.subprocess, "run", lambda *a, **k: Result())
    assert stack_tags.main() == 0
    out = capsys.readouterr().out
    assert "Agreement: MIXED" in out
    assert "harness-backend:vw27s3" in out
    assert "Read-only:" in out


# --------------------------------------------------------------------- B2 drift
# EXPECTED_BACKEND_SERVICES must stay aligned with deploy/docker-compose.yml.
# The script hard-codes the tuple; if a service is added/removed in the compose
# file without updating it, the tag report silently lies about what should be
# running. This test parses the compose file (line-based: the repo does not
# depend on PyYAML) and asserts the continuous harness-backend services — those
# that merge `<<: *backend-build` and are NOT behind a `profiles:` gate or a
# `restart: no` one-shot — are exactly EXPECTED_BACKEND_SERVICES.
#
# The script exits 0 even when docker ps fails (a reviewer decision, not altered
# here); this test does not change that.

_COMPOSE = (
    Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"
)


def _compose_backend_services(path: Path) -> set[str]:
    """Continuous harness-backend services named by the compose file.

    A line-based parse (no PyYAML dependency): walk the ``services:`` block,
    collect service keys that merge ``<<: *backend-build``, and drop any that
    declare a ``profiles:`` key or ``restart: … no`` (one-shot or load test).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "services:")
    except StopIteration as exc:  # pragma: no cover - compose file always has services:
        raise AssertionError("deploy/docker-compose.yml has no services: block") from exc
    services: dict[str, dict[str, bool]] = {}
    current: str | None = None
    past_services = False
    for line in lines[start + 1:]:
        if line and not line.startswith((" ", "\t", "#")):
            past_services = True  # left the services block (networks:/volumes:)
            current = None
            continue
        if past_services:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":"):
            current = line.strip()[:-1]
            services[current] = {"backend": False, "profile": False, "oneshot": False}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped == "<<: *backend-build":
            services[current]["backend"] = True
        elif stripped.startswith("profiles:"):
            services[current]["profile"] = True
        elif stripped.startswith("restart:") and "no" in stripped:
            services[current]["oneshot"] = True
    return {
        name for name, flags in services.items()
        if flags["backend"] and not flags["profile"] and not flags["oneshot"]
    }


def test_expected_backend_services_match_the_compose_file():
    """The hard-coded tuple is exactly the continuous harness-backend services."""
    compose_services = _compose_backend_services(_COMPOSE)
    assert compose_services == set(EXPECTED_BACKEND_SERVICES), (
        f"EXPECTED_BACKEND_SERVICES drifts from deploy/docker-compose.yml:\n"
        f"  in script but not compose: {set(EXPECTED_BACKEND_SERVICES) - compose_services}\n"
        f"  in compose but not script: {compose_services - set(EXPECTED_BACKEND_SERVICES)}\n"
        f"Update EXPECTED_BACKEND_SERVICES in stack_tags.py to match."
    )
