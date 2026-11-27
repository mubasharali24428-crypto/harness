#!/usr/bin/env python3
"""Readiness drift: is the deployed image set still the reviewed set? (G-A)

The P0 lesson (week-03 MIXED stack, the vw24b1 gate delta) is that drift is
only visible if something compares the RUNNING stack against the REVIEWED
record — people don't. This tool is that comparison, in two modes:

  default      live stack vs manifest: every manifest image's digest, the
               running compose services' image refs and digests for one
               project, the live schema_migrations state, and the worker
               daemon's shell gate byte-identity against HEAD.
  --ci         no Docker needed: manifest vs HEAD coherence — the manifest
               must name a git_head on this branch's lineage, and the schema
               it records must match the migration files on disk. A freshly
               merged migration flips CI red until the stack is redeployed
               and a fresh manifest is written (that IS the G-A drift check).

  --write-manifest <path> [--project harness-rc]
               inspect the live stack and write a manifest for it (the
               redeploy runbook's last step). Read-only inspection; writes
               only the manifest file you name.

External commands: `docker image inspect`, `docker ps`, `docker exec <db> psql
...`, `docker exec <worker-daemon> sha256sum ...`. Nothing is created, started,
stopped or reconfigured. Exit: 0 = no drift, 1 = drift found (each check prints
its own line), 2 = docker unusable (live mode).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import stack_tags  # same directory; provides PS_FORMAT and parse_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = "docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json"
GATE_PATH_IN_CONTAINER = "/opt/harness-shell/shell_gate.py"
GATE_PATH_IN_REPO = "section5/src/harness_sandbox/shell_gate.py"


def _run(command: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def image_digest(ref: str) -> tuple[str | None, str | None]:
    """`docker image inspect` -> (Id digest, None) or (None, stderr)."""
    code, out, err = _run(["docker", "image", "inspect", "--format", "{{.Id}}", ref])
    if code != 0:
        return None, err or f"inspect exited {code}"
    return out, None


def schema_state(container: str) -> tuple[tuple[str, str] | None, str | None]:
    """(count, max_version) from the container's schema_migrations."""
    code, out, err = _run([
        "docker", "exec", container, "psql", "-U", "harness", "-d", "harness",
        "-t", "-A", "-c", "select count(*), max(version) from schema_migrations;",
    ])
    if code != 0:
        return None, err or f"psql exited {code}"
    parts = [p.strip() for p in out.split("|")]
    if len(parts) != 2:
        return None, f"unexpected psql output: {out!r}"
    return (parts[0], parts[1]), None


def gate_digest(container: str) -> tuple[str | None, str | None]:
    code, out, err = _run(["docker", "exec", container, "sha256sum", GATE_PATH_IN_CONTAINER])
    if code != 0:
        return None, err or f"sha256sum exited {code}"
    return out.split()[0], None


def repo_gate_digest() -> str:
    return hashlib.sha256((REPO_ROOT / GATE_PATH_IN_REPO).read_bytes()).hexdigest()


def disk_migrations() -> tuple[int, str]:
    files = sorted((REPO_ROOT / "section1" / "migrations").glob("*.sql"))
    stem = files[-1].stem
    return len(files), stem.split("_", 1)[0]


def manifest_git_head_ancestor(manifest_head: str) -> tuple[bool, str | None]:
    code, _out, err = _run(["git", "merge-base", "--is-ancestor", manifest_head, "HEAD"])
    if code == 0:
        return True, None
    if code == 1:
        return False, None
    return False, err or f"git merge-base exited {code}"


def check_live(manifest: dict, project: str) -> tuple[list[str], int]:
    """Live-mode checks. Returns (report lines, drift count)."""
    lines: list[str] = []
    drift = 0
    code, _, err = _run(["docker", "ps", "--format", stack_tags.PS_FORMAT])
    if code != 0:
        message = (f"docker ps failed: {err} - the live stack cannot be seen, "
                   "so a quiet exit would read as 'no drift'")
        return [message], 2

    # The manifest's tag field is prose ("vv90r6 (compose v-prefix of …)");
    # the first whitespace-delimited token is the image tag it names.
    tag = manifest.get("tag", "").split()[0] if manifest.get("tag") else ""
    images: dict[str, str] = manifest.get("images", {})

    for ref, recorded in sorted(images.items()):
        digest, error = image_digest(ref)
        if digest is None:
            lines.append(f"DRIFT image {ref}: not inspectable ({error})")
            drift += 1
        elif digest != recorded:
            lines.append(f"DRIFT image {ref}: running {digest[:19]}… != manifest {recorded[:19]}…")
            drift += 1
        else:
            lines.append(f"ok   image {ref}: {digest[:19]}…")

    rows = stack_tags.parse_rows(_run(["docker", "ps", "--format", stack_tags.PS_FORMAT])[1])
    project_rows = [r for r in rows if r[0] == project]
    backend = {r[1]: r for r in project_rows if r[1] in stack_tags.EXPECTED_BACKEND_SERVICES}
    missing = [s for s in stack_tags.EXPECTED_BACKEND_SERVICES if s not in backend]
    if missing:
        lines.append(f"DRIFT project {project}: backend services not running: {', '.join(missing)}")
        drift += 1
    wrong = [f"{s} on {stack_tags.image_tag(r[3])} (manifest tag {tag})"
             for s, r in backend.items() if stack_tags.image_tag(r[3]) != tag]
    if wrong:
        lines.append(f"DRIFT project {project}: services off the manifest tag: {'; '.join(wrong)}")
        drift += 1
    else:
        lines.append(f"ok   project {project}: {len(backend)}/"
                     f"{len(stack_tags.EXPECTED_BACKEND_SERVICES)} backend services on {tag}")

    db_container = next((r[2] for r in project_rows if r[1] == "db"), None)
    recorded_schema = manifest.get("schema")
    if db_container and recorded_schema:
        live_schema, error = schema_state(db_container)
        if live_schema is None:
            lines.append(f"DRIFT schema: unreadable on {db_container} ({error})")
            drift += 1
        elif f"{live_schema[0]}|{live_schema[1]}" != f"{recorded_schema['applied']}|{recorded_schema['max_version']}":
            lines.append(f"DRIFT schema: live {live_schema[0]}|{live_schema[1]} != "
                         f"manifest {recorded_schema['applied']}|{recorded_schema['max_version']}")
            drift += 1
        else:
            lines.append(f"ok   schema: {live_schema[0]}|{live_schema[1]}")
    elif not recorded_schema:
        lines.append("warn manifest has no schema record — refresh it with --write-manifest")

    daemon_container = next((r[2] for r in project_rows if r[1] == stack_tags.WORKER_DAEMON_SERVICE), None)
    if daemon_container:
        live_gate, error = gate_digest(daemon_container)
        local_gate = repo_gate_digest()
        if live_gate is None:
            lines.append(f"DRIFT gate: unreadable in {daemon_container} ({error})")
            drift += 1
        elif live_gate != local_gate:
            lines.append(f"DRIFT gate: {daemon_container} {live_gate[:16]}… != HEAD {local_gate[:16]}…")
            drift += 1
        else:
            lines.append(f"ok   gate: byte-identical to HEAD ({live_gate[:16]}…)")
    else:
        lines.append(f"warn worker-daemon for project {project} not running — gate parity unchecked")

    return lines, drift


def check_ci(manifest: dict) -> tuple[list[str], int]:
    """Manifest-vs-HEAD coherence without Docker."""
    lines: list[str] = []
    drift = 0
    head = manifest.get("git_head", "")
    sha = head.split()[0] if head else ""
    if not sha:
        lines.append("DRIFT manifest has no git_head")
        drift += 1
    else:
        is_ancestor, error = manifest_git_head_ancestor(sha)
        if error:
            return [f"DRIFT git lookup failed: {error}"], 1
        if is_ancestor:
            behind = _run(["git", "rev-list", "--count", f"{sha}..HEAD"])[1]
            lines.append(f"ok   manifest git_head {sha[:12]} is on HEAD's lineage "
                         f"(HEAD is {behind} commit(s) ahead; a redeploy refreshes the manifest)")
        else:
            lines.append(f"DRIFT manifest git_head {sha[:12]} is NOT an ancestor of HEAD "
                         "(the manifest does not describe this branch's lineage)")
            drift += 1

    recorded = manifest.get("schema")
    count, max_version = disk_migrations()
    if recorded:
        if (str(recorded["applied"]) != str(count)
                or str(recorded["max_version"]) != max_version):
            lines.append(f"DRIFT migrations: disk {count}|{max_version} != manifest "
                         f"{recorded['applied']}|{recorded['max_version']} — a merged migration "
                         "is not in the reviewed deployed set; redeploy, then --write-manifest")
            drift += 1
        else:
            lines.append(f"ok   migrations: disk {count}|{max_version} = manifest")
    else:
        lines.append("warn manifest has no schema record — refresh it with --write-manifest")

    for ref in manifest.get("images", {}):
        if ":" not in ref:
            lines.append(f"DRIFT manifest image ref without a tag: {ref}")
            drift += 1
    if not manifest.get("images"):
        lines.append("DRIFT manifest records no images")
        drift += 1
    return lines, drift


def write_manifest(path: Path, project: str) -> int:
    code, ps_text, err = _run(["docker", "ps", "--format", stack_tags.PS_FORMAT])
    if code != 0:
        print(f"docker ps failed: {err}", file=sys.stderr)
        return 2
    rows = [r for r in stack_tags.parse_rows(ps_text) if r[0] == project]
    if not rows:
        print(f"no running containers for compose project {project!r}", file=sys.stderr)
        return 2
    tags = {stack_tags.image_tag(r[3]) for r in rows
            if r[1] in stack_tags.EXPECTED_BACKEND_SERVICES}
    if len(tags) != 1:
        print(f"project {project} is MIXED ({', '.join(sorted(tags))}): "
              "redeploy to one tag before writing a manifest", file=sys.stderr)
        return 1
    tag = tags.pop()
    images: dict[str, str] = {}
    for ref in sorted({r[3] for r in rows}):
        digest, error = image_digest(ref)
        if digest is None:
            print(f"cannot inspect {ref}: {error}", file=sys.stderr)
            return 2
        images[ref] = digest
    db_container = next((r[2] for r in rows if r[1] == "db"), None)
    schema = None
    if db_container:
        state, error = schema_state(db_container)
        if state:
            schema = {"applied": int(state[0]), "max_version": state[1]}
        else:
            print(f"warn: schema unreadable ({error}); manifest omits it", file=sys.stderr)
    daemon = next((r[2] for r in rows if r[1] == stack_tags.WORKER_DAEMON_SERVICE), None)
    gate = gate_digest(daemon)[0] if daemon else None
    head = _run(["git", "rev-parse", "HEAD"])[1]
    manifest = {
        "captured_at_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_head": head,
        "project": f"{project} (DEPLOYED — written by scripts/ops/readiness_drift.py --write-manifest)",
        "tag": f"vv{tag}" if not tag.startswith("vv") else tag,
        "images": images,
        "schema": schema,
        "shell_gate_sha256": gate,
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest written: {path} (commit it — the drift checks read it)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--project", default="harness-rc",
                        help="compose project the manifest describes (live mode)")
    parser.add_argument("--ci", action="store_true",
                        help="manifest vs HEAD only; no Docker required")
    parser.add_argument("--write-manifest", metavar="PATH",
                        help="inspect the live stack and write a manifest to PATH, then exit")
    args = parser.parse_args()

    if args.write_manifest:
        return write_manifest(Path(args.write_manifest), args.project)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"manifest missing: {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text())

    if args.ci:
        lines, drift = check_ci(manifest)
        print(f"Readiness drift (CI mode: manifest vs HEAD) — {manifest_path}")
    else:
        lines, drift = check_live(manifest, args.project)
        print(f"Readiness drift (live mode: stack vs manifest) — {manifest_path}")
    for line in lines:
        print(line)
    if drift == 2:
        print("UNDECIDABLE: docker could not be seen. Not a pass.")
        return 2
    print(f"drift: {drift} item(s)")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
