#!/usr/bin/env python3
"""Read-only report of the running stack's image tags.

Prints every running compose service with its image tag, then says whether the
backend services agree on one tag. The W27 slice 2/3 deploys left the stack
mixed (STATUS.md, "W27 slice 2 deployed" / "W27 slice 3 deployed"), so a person
needs to see the mix before choosing a redeploy.

When more than one Compose project is running (the owner's Mac normally runs
harness-rc AND harness-demo together), a service-name keyed AGREE describes
whichever stack's container kept the dict slot — not the stack the reader
means. With multiple projects up, agreement is therefore reported PER PROJECT
and no single-stack AGREE verdict is printed.

The ONLY external command is `docker ps`. Nothing here creates, stops, removes,
rebuilds or reconfigures anything. Exit status: 0 when `docker ps` answered (drift is reported in
the text, not the status), 2 when `docker ps` failed: the stack could not be seen, so a quiet exit
would read as "no drift".
"""
from __future__ import annotations

import argparse
import subprocess

PS_FORMAT = ('{{.Label "com.docker.compose.project"}}|'
             '{{.Label "com.docker.compose.service"}}|{{.Names}}|{{.Image}}')

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


def parse_rows(text: str) -> list[tuple[str, str, str, str]]:
    """`docker ps --format` text -> (project, service, container name, image).

    Accepts the 4-field format (project|service|name|image) and the legacy
    3-field format (service|name|image, project ""). A container with no
    compose service label is keyed by its own name; a malformed or blank line
    is skipped rather than guessed at. Nothing is deduplicated: two stacks
    running the same service label both appear.
    """
    rows: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 4:
            project, service, name, image = parts
            if not name or not image:
                continue
            rows.append((project, service or name, name, image))
        elif len(parts) == 3:
            service, name, image = parts
            if not name or not image:
                continue
            rows.append(("", service or name, name, image))
    return rows


def parse_ps(text: str) -> dict[str, tuple[str, str]]:
    """{service: (container name, image)} — the LAST row wins per label.

    Kept for callers that inspect single-stack output. With two compose
    projects up, same-label services from both stacks collide here; the
    report itself reasons on parse_rows, never on this dict.
    """
    out: dict[str, tuple[str, str]] = {}
    for _project, service, name, image in parse_rows(text):
        out[service] = (name, image)
    return out


def image_tag(image: str) -> str:
    """The tag of an image ref; digests and untagged refs are named honestly."""
    if "@sha256:" in image:
        return "<digest>"
    tail = image.rsplit("/", 1)[-1]
    return tail.rsplit(":", 1)[1] if ":" in tail else "<untagged>"


def stack_report(ps_text: str) -> list[str]:
    """Pure: canned docker ps text in, report lines out. No Docker needed."""
    rows = parse_rows(ps_text)
    lines = ["Stack tag report (read-only: docker ps)", "", "Running services:"]
    for project, service, name, image in sorted(rows):
        label = f"{project}/{service}" if project else service
        lines.append(f"  {label:<34} {image:<40} tag={image_tag(image)}  ({name})")
    lines.append("")

    by_project: dict[str, dict[str, tuple[str, str]]] = {}
    daemon_by_project: dict[str, tuple[str, str]] = {}
    for project, service, name, image in rows:
        if service == WORKER_DAEMON_SERVICE:
            daemon_by_project[project] = (name, image)
        elif service in EXPECTED_BACKEND_SERVICES:
            by_project.setdefault(project, {})[service] = (name, image)

    active = {p for p, tags in by_project.items() if tags}
    if len(active) <= 1:
        # Single stack (or none): the historical flat report.
        tags = next(iter(by_project.values())) if by_project else {}
        missing = [s for s in EXPECTED_BACKEND_SERVICES if s not in tags]
        lines.append(f"Backend services: {len(tags)} of "
                     f"{len(EXPECTED_BACKEND_SERVICES)} expected running")
        lines.append("Missing: " + (", ".join(missing) if missing else "none"))
        by_tag: dict[str, list[str]] = {}
        for service, (_name, image) in tags.items():
            by_tag.setdefault(image_tag(image), []).append(service)
        if not by_tag:
            lines.append("Agreement: UNKNOWN (no backend containers found)")
        elif len(by_tag) == 1:
            lines.append(f"Agreement: AGREE ({next(iter(by_tag))})")
        else:
            lines.append("Agreement: MIXED")
            for tag in sorted(by_tag, key=lambda t: (-len(by_tag[t]), t)):
                names = sorted(by_tag[tag])
                lines.append(f"  {tag:<12} {len(names):>2} service(s): {', '.join(names)}")
        for _project, (name, image) in sorted(daemon_by_project.items()):
            lines.append(f"Worker daemon: {image} (tag={image_tag(image)}); "
                         "separate image, not part of the backend group above.")
        if not daemon_by_project:
            lines.append("Worker daemon: not running")
    else:
        # Two stacks up (the owner's normal state): one bare AGREE line would
        # describe whichever stack's row won the service dict. Per project only.
        lines.append("Multiple compose projects running - agreement per project; "
                     "no single-stack verdict is safe here.")
        for project in sorted(active):
            label = project or "(no compose project label)"
            tags = by_project[project]
            missing = [s for s in EXPECTED_BACKEND_SERVICES if s not in tags]
            by_tag: dict[str, list[str]] = {}
            for service, (_name, image) in tags.items():
                by_tag.setdefault(image_tag(image), []).append(service)
            suffix = f", missing: {', '.join(missing)}" if missing else ""
            if len(by_tag) == 1:
                lines.append(f"  {label}: {len(tags)}/{len(EXPECTED_BACKEND_SERVICES)} "
                             f"services, AGREE ({next(iter(by_tag))}){suffix}")
            else:
                lines.append(f"  {label}: {len(tags)}/{len(EXPECTED_BACKEND_SERVICES)} "
                             f"services, MIXED{suffix}")
                for tag in sorted(by_tag, key=lambda t: (-len(by_tag[t]), t)):
                    names = sorted(by_tag[tag])
                    lines.append(f"      {tag:<12} {len(names):>2} service(s): {', '.join(names)}")
            daemon = daemon_by_project.get(project)
            if daemon:
                lines.append(f"      worker-daemon: {daemon[1]} (tag={image_tag(daemon[1])})")
    lines.append("Read-only: no container, image, volume or compose state was changed.")
    return lines


def main(project: str | None = None) -> int:
    command = ["docker", "ps", "--format", PS_FORMAT]
    if project:
        command.extend(["--filter", f"label=com.docker.compose.project={project}"])
        print(f"Compose project: {project}")
    result = subprocess.run(
        command,
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(f"docker ps exited {result.returncode}: {result.stderr.strip()}")
    print("\n".join(stack_report(result.stdout)))
    return 2 if result.returncode != 0 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Restrict the report to one Compose project")
    raise SystemExit(main(project=parser.parse_args().project))
