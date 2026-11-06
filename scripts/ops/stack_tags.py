#!/usr/bin/env python3
"""Read-only report of the running stack's image tags.

Prints every running compose service with its image tag, then says whether the
backend services agree on one tag. The W27 slice 2/3 deploys left the stack
mixed (STATUS.md, "W27 slice 2 deployed" / "W27 slice 3 deployed"), so a person
needs to see the mix before choosing a redeploy.

The ONLY external command is `docker ps`. Nothing here creates, stops, removes,
rebuilds or reconfigures anything, and this script always exits 0.
"""
from __future__ import annotations

import subprocess

PS_FORMAT = '{{.Label "com.docker.compose.service"}}|{{.Names}}|{{.Image}}'

# Services in deploy/docker-compose.yml that inherit `<<: *backend-build`
# (image: harness-backend:v${HARNESS_IMAGE_TAG}) and run continuously. One-shot
# `migrate`/`backend` services and the *-loadtest profiles are not expected up.
EXPECTED_BACKEND_SERVICES = (
    "events-retention",
    "github",
    "identity",
    "meeting",
    "models",
    "orchestrator",
    "review",
    "sandbox",
    "sandbox-egress",
    "siem-push",
    "webhook-delivery",
)
WORKER_DAEMON_SERVICE = "worker-daemon"


def parse_ps(text: str) -> dict[str, tuple[str, str]]:
    """`docker ps --format` text -> {service: (container name, image ref)}.

    A container with no compose service label is keyed by its own name; a
    malformed or blank line is skipped rather than guessed at.
    """
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not parts[1] or not parts[2]:
            continue
        service = parts[0] or parts[1]
        out[service] = (parts[1], parts[2])
    return out


def image_tag(image: str) -> str:
    """The tag of an image ref; digests and untagged refs are named honestly."""
    if "@sha256:" in image:
        return "<digest>"
    tail = image.rsplit("/", 1)[-1]
    return tail.rsplit(":", 1)[1] if ":" in tail else "<untagged>"


def stack_report(ps_text: str) -> list[str]:
    """Pure: canned docker ps text in, report lines out. No Docker needed."""
    services = parse_ps(ps_text)
    lines = ["Stack tag report (read-only: docker ps)", "", "Running services:"]
    for service in sorted(services):
        name, image = services[service]
        lines.append(f"  {service:<22} {image:<40} tag={image_tag(image)}  ({name})")
    lines.append("")

    found = {s: image_tag(services[s][1])
             for s in EXPECTED_BACKEND_SERVICES if s in services}
    missing = [s for s in EXPECTED_BACKEND_SERVICES if s not in services]
    by_tag: dict[str, list[str]] = {}
    for service, tag in found.items():
        by_tag.setdefault(tag, []).append(service)

    lines.append(f"Backend services: {len(found)} of "
                 f"{len(EXPECTED_BACKEND_SERVICES)} expected running")
    lines.append("Missing: " + (", ".join(missing) if missing else "none"))
    if not by_tag:
        lines.append("Agreement: UNKNOWN (no backend containers found)")
    elif len(by_tag) == 1:
        lines.append(f"Agreement: AGREE ({next(iter(by_tag))})")
    else:
        lines.append("Agreement: MIXED")
        for tag in sorted(by_tag, key=lambda t: (-len(by_tag[t]), t)):
            names = sorted(by_tag[tag])
            lines.append(f"  {tag:<12} {len(names):>2} service(s): {', '.join(names)}")

    daemon = services.get(WORKER_DAEMON_SERVICE)
    if daemon:
        lines.append(f"Worker daemon: {daemon[1]} (tag={image_tag(daemon[1])}); "
                     "separate image, not part of the backend group above.")
    else:
        lines.append("Worker daemon: not running")
    lines.append("Read-only: no container, image, volume or compose state was changed.")
    return lines


def main() -> int:
    result = subprocess.run(
        ["docker", "ps", "--format", PS_FORMAT],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(f"docker ps exited {result.returncode}: {result.stderr.strip()}")
    print("\n".join(stack_report(result.stdout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
