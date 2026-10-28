#!/usr/bin/env python3
"""Local-workspace engine launcher (W6 slice 4): the SAME engine, container-constrained.

This is launch code, not a second executor. It constructs the orchestrator's own
``DeepSeekHarnessWrapper`` with ``isolated=True`` and lets ``confine.wrap_command``
build the worker launch — which on this host means a per-run Docker container
(same image, same flags, same internal network + model relay + egress proxy as
a server-side run). Seatbelt cannot confine the real runtime (engine.py refuses
it: sandbox-exec breaks the dsh PTY), so the launcher demands ``container`` and
fails closed on everything else: no docker, no image, no credentials, unknown
mode -> refuse, never degrade to ``off``.

The agent never touches the live workspace: the directory is copied to a run
dir (and a pristine baseline), the worker runs in the copy, and the diff is
computed INSIDE the boundary (a read-only bind of both copies) rather than by a
client-side reimplementation. Applying the result back is the CLI's job.

    python3 deploy/runners/local_engine.py --dir DIR --goal "..." --run-id ID \
        [--policy-file plan.json] [--timeout 900] [--out result.ndjson]

Stdout: NDJSON — one line per tool event, then the final result line
(``engine_result`` plus the ``diff``/``diffOmitted`` the worker produced).
Exit 0 = a confined run completed; any refusal exits 2 with an actionable
message and no spawn.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HARNESS_ROOT = Path(__file__).resolve().parents[2]
_SECTION4_SRC = HARNESS_ROOT / "section4" / "src"
_CONTRACTS_SRC = HARNESS_ROOT / "contracts" / "src"
for _src in (_SECTION4_SRC, _CONTRACTS_SRC):
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from harness_orchestrator import confine

_IMAGE_ENV = "HARNESS_ENGINE_IMAGE"
_IMAGE_TAG_ENV = "HARNESS_IMAGE_TAG"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def default_image(env: dict[str, str] | None = None) -> str:
    """The worker image a local run uses when the operator names none.

    It tracks ``HARNESS_IMAGE_TAG`` — the same variable the compose stack uses —
    rather than a frozen pilot tag. The frozen ``p0`` default predated the
    W11 vision patch, so a shipped ``.env`` that sets ``DSH_PATCH_FILES`` to
    ``/opt/harness/patches/openrouter-vision.patch.yml`` failed inside the
    worker before any model call: the image was current-revision elsewhere,
    the launcher was not.
    """
    env = os.environ if env is None else env
    tag = env.get(_IMAGE_TAG_ENV, "").strip() or "dev-local"
    return f"harness-engine-worker:v{tag}"


class Refusal(RuntimeError):
    """A fail-closed refusal: actionable, and nothing was spawned."""


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60, check=False
    )


def preflight(confinement_env: dict[str, str] | None = None) -> str:
    """Everything must be true before any copy or spawn; first failure raises."""
    env = os.environ if confinement_env is None else confinement_env
    if shutil.which("docker") is None:
        raise Refusal("local mode needs docker on PATH (the container boundary)")
    mode = env.get(confine.CONFINEMENT_ENV, "").strip().lower()
    if mode != "container":
        raise Refusal(
            f"{confine.CONFINEMENT_ENV}={mode or 'unset'!r}: local mode runs ONLY the "
            "container boundary (seatbelt breaks the real runtime's PTY; off is refused "
            "here regardless of the server's policy)"
        )
    probe = _docker("info", "--format", "{{.ServerVersion}}")
    if probe.returncode != 0:
        raise Refusal(f"the docker daemon is not answering: {(probe.stderr or '').strip()[:200]}")
    image = env.get(_IMAGE_ENV, "").strip() or default_image(env)
    inspect = _docker("image", "inspect", "--format", "{{.Id}}", image)
    if inspect.returncode != 0:
        raise Refusal(
            f"engine image {image!r} is not present on this host "
            f"(build it or set {_IMAGE_ENV})"
        )
    if not (env.get("DEEPSEEK_API_KEY") or "").strip():
        raise Refusal("DEEPSEEK_API_KEY is not set: a live local run must not silently downgrade")
    return image


def copy_tree(src: Path, dst: Path) -> None:
    """Workspace copy without .git — the agent gets files, never repo history/hooks."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == ".git":
            continue
        target = dst / item.name
        if item.is_dir() and not item.is_symlink():
            copy_tree(item, target)
        elif item.is_file():
            shutil.copy2(item, target)


def _touch_tree(path: Path) -> None:
    for p in sorted(path.rglob("*")):
        if p.is_file():
            os.utime(p, None)


def raw_diff(image: str, run_dir: Path, baseline_dir: Path, name_only: bool = False) -> str:
    """The run's diff, computed inside the boundary.

    One-shot container, both trees bound READ-ONLY: the worker image's git
    compares baseline against the run. No host git, no client-side reparse —
    the bytes are the engine's own output, generated under the same mount laws.
    """
    argv = [
        "docker", "run", "--rm",
        "--network", "none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "32",
        "--memory", "128m",
        "--user", f"{os.getuid()}:{os.getgid() or 10001}",
        "--mount", f"type=bind,src={baseline_dir},dst={baseline_dir},readonly",
        "--mount", f"type=bind,src={run_dir},dst={run_dir},readonly",
        "--entrypoint", "git",
        image,
        "-c", f"safe.directory={run_dir}",
        "-C", str(run_dir),
        "diff", "--no-index", *(["--name-only"] if name_only else ["--unified=3", "--no-color"]),
        str(baseline_dir), str(run_dir),
    ]
    for token in (str(baseline_dir), str(run_dir)):
        if "," in token:
            raise Refusal(f"workspace paths with commas cannot be bound: {token!r}")
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode not in (0, 1):  # git diff: 0 identical, 1 differs, >=2 real failure
        raise Refusal(f"worker-image diff failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    return proc.stdout


def _changed_names(run_dir: Path, baseline_dir: Path, image: str) -> list[str]:
    names = raw_diff(image, run_dir, baseline_dir, name_only=True).splitlines()
    out = []
    for line in names:
        rel = line.strip()
        rel = rel.removeprefix(f"{run_dir}/").removeprefix(f"{baseline_dir}/")
        if rel and ".." not in Path(rel).parts:
            out.append(rel)
    return out


def base_hashes(root: Path, names: list[str]) -> dict[str, str | None]:
    """The sha256 each named path had in ``root``; ``None`` when it did not exist.

    This is the hash anchor the apply checks: a path whose live bytes no longer
    match was moved by someone else, and the reviewed result must conflict.
    """
    out: dict[str, str | None] = {}
    for rel in dict.fromkeys(names):
        candidate = root / rel
        out[rel] = (
            hashlib.sha256(candidate.read_bytes()).hexdigest()
            if candidate.is_file() else None
        )
    return out


def copy_out_changed(run_dir: Path, work_out: Path, names: list[str]) -> None:
    """The engine's OUTPUT files leave the scratch under the CLI's control.

    The launcher deletes its scratch; the CLI needs the changed files to apply
    them, so they are copied out HERE, limited to the names the boundary's own
    diff produced.
    """
    work_out.mkdir(parents=True, exist_ok=True)
    for rel in names:
        src = run_dir / rel
        if src.is_file():
            dst = work_out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def deletions_from_diff(text: str) -> list[str]:
    """Files the boundary's diff proves were DELETED ("--- <path>" with no "+++").

    A `--name-only` list only speaks for surviving files — treating "absent
    from it" as deleted would delete every file the engine never touched
    (caught by the live drill). Only the hunk headers of the full diff know
    the difference.
    """
    out: list[str] = []
    prev_minus: str | None = None
    for line in text.splitlines():
        if line.startswith("--- ") and line.strip() != "--- /dev/null":
            prev_minus = line[4:].strip()
        elif line.startswith("+++ "):
            if prev_minus is not None and line.strip() == "+++ /dev/null":
                rel = prev_minus.removeprefix("a/").removeprefix("b/").lstrip("/")
                if rel and ".." not in Path(rel).parts:
                    out.append(rel)
            prev_minus = None
    return out


def normalize_diff(text: str, run_dir: Path, baseline_dir: Path) -> tuple[str, str | None]:
    # git --no-index prints absolute paths under its conventional a/ b/ prefixes
    # ("+++ b//abs/run/x"). Stripping only the absolute root leaves the prefix
    # glued to the name — "+++ bide_note.txt" — so a local editor run applied
    # its change under a wrong filename. Strip prefix+root together, keep "a/".
    for root in (run_dir, baseline_dir):
        absolute = str(root) + os.sep
        text = text.replace("a" + absolute, "a/").replace("b" + absolute, "b/")
        text = text.replace(absolute, "")
    omitted = None
    if len(text) > 400_000:
        omitted = "too-large"
        text = text[:200_000]
    return text, omitted


def run_local(
    directory: Path,
    goal: str,
    run_id: str,
    *,
    shell_policy: dict[str, Any] | None = None,
    timeout_seconds: float = 900.0,
    on_progress=None,
    environ: dict[str, str] | None = None,
    work_out: Path | None = None,
) -> dict[str, Any]:
    """One confined local turn. Returns the engine result plus the worker diff."""
    env = os.environ if environ is None else environ
    if not _RUN_ID_RE.fullmatch(run_id):
        raise Refusal(f"run id {run_id!r} is not path-safe")
    src = directory.resolve()
    if not src.is_dir():
        raise Refusal(f"workspace {src} is not a directory")
    image = preflight(env)

    scratch = Path(tempfile.mkdtemp(prefix=f"harness-local-{run_id}-"))
    try:
        root = scratch / "ws"
        run_dir = root / run_id
        baseline_dir = scratch / "baseline"
        copy_tree(src, run_dir)
        copy_tree(src, baseline_dir)
        _touch_tree(run_dir)  # copies land with source mtimes; git needs to see content

        os.environ[confine.CONFINEMENT_ENV] = "container"
        os.environ["HARNESS_ENGINE_IMAGE"] = image
        # The engine's worker budget is read from the environment at launch; the
        # CLI's --timeout must actually bound the confined process, not just us.
        os.environ["HARNESS_ENGINE_TIMEOUT_SECONDS"] = str(int(timeout_seconds))
        from harness_orchestrator.engine import DeepSeekHarnessWrapper

        wrapper = DeepSeekHarnessWrapper(
            workspace_dir=str(root),
            session_root=str(scratch / "sessions"),
            api_key=env.get("DEEPSEEK_API_KEY", ""),
            base_url=env.get("DEEPSEEK_BASE_URL", "") or None,
            model=env.get("DSH_MODEL") or None,
            live_mode="live",
            isolated=True,
        )
        wrapper.daemon_url = ""  # THIS process is the launcher; workers are ours to make
        if shell_policy:
            wrapper.set_shell_policy(run_id, shell_policy)
        result = wrapper.execute_goal(
            goal, session_id=run_id, on_progress=on_progress,
        )
        names = _changed_names(run_dir, baseline_dir, image)
        text = raw_diff(image, run_dir, baseline_dir)
        diff_text, omitted = normalize_diff(text, run_dir, baseline_dir)
        # Deletions come from the diff's own hunk headers — never from "absent
        # in the change list" (that would delete every untouched file).
        deleted = [
            rel.removeprefix(f"{run_dir}/").removeprefix(f"{baseline_dir}/")
            for rel in deletions_from_diff(diff_text)
        ]
        if work_out is not None:
            copy_out_changed(run_dir, Path(work_out), names)
        # The hash each named path had in the pristine copy. The CLI checks it
        # before writing anything: if the live workspace moved while the run was
        # in flight, the reviewed bytes are stale and the apply must refuse
        # rather than silently overwrite another result or an editor save.
        result["changed"] = names
        result["deleted"] = deleted
        result["baseHashes"] = base_hashes(baseline_dir, [*names, *deleted])
        result["diff"] = diff_text
        if omitted:
            result["diffOmitted"] = omitted
        result["local"] = {
            "engine": "container",
            "image": image,
            "workspace": str(src),
            "confinement": "container",
        }
        return result
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(prog="local_engine", description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--policy-file", type=Path, help="JSON shell policy from the approved plan")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--out", type=Path, help="also write the NDJSON stream to this file")
    ap.add_argument("--work-out", type=Path,
                    help="copy the changed engine output files here before scratch cleanup")
    args = ap.parse_args()

    policy = None
    if args.policy_file:
        policy = json.loads(args.policy_file.read_text(encoding="utf-8"))

    def emit(record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    try:
        result = run_local(
            args.dir, args.goal, args.run_id,
            shell_policy=policy,
            timeout_seconds=args.timeout,
            on_progress=lambda rec: emit({"progress": rec}),
            work_out=args.work_out,
        )
    except confine.ConfinementUnavailableError as exc:
        emit({"refusal": f"confinement unavailable: {exc}"})
        return 2
    except Refusal as exc:
        emit({"refusal": str(exc)})
        return 2
    emit({"engine_result": {k: v for k, v in result.items() if k != "diff"},
          "diff": result.get("diff", ""),
          "diffOmitted": result.get("diffOmitted")})
    if args.out:
        args.out.write_text(json.dumps(result, default=str), encoding="utf-8")
    status = str(result.get("status") or "")
    return 0 if status in {"completed", "done", "succeeded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
