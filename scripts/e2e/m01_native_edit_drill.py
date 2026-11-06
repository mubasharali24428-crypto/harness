#!/usr/bin/env python3
"""M01 native-edit drill: drive the native ``edit`` tool with no model.

The M01 brief (``docs/backlog.md``, "From W1 M01") records that the native write
tool was driven by ``scripts/e2e/m03_scope_drill.py`` but that edit was "not
separately driven". This closes that caveat without a model, a provider or a
spend: it starts one throwaway container from the image the running worker
daemon uses, with the mounts ``confine.wrap_command`` gives a run scoped to
``src`` (workspace read-only, ``src`` bound back writable), and calls the very
code path the edit tool uses.

That path is in the image's bundled Node runtime:

* ``@deepseek-ai/dsh-tool-fs/lib/index.js`` -> ``applyEditTool`` -> the edit
  tool's ``execute`` is ``ctx.fs.resolve()`` then ``ctx.fs.editText()``.
* ``@deepseek-ai/dsh-fs-local/lib/index.js`` -> ``LocalFileSystem.editText()``
  -> ``writeFileAtomic()``, which stages a temp directory *beside the target*
  before renaming. That staging step is what the read-only mount refuses.

The small script below (``PROBE``) loads that real ``LocalFileSystem`` from the
runtime's single-file archive and calls ``resolve`` + ``editText`` directly.
There is no agent loop, no ``session/prompt``, no HTTP stub and no LLM.

Verdict is the disk, not the tool's wording: ``src/x.txt`` must change,
``docs/y.txt`` must be refused by the kernel, and a hash of every file outside
``src`` must be identical before and after. The container is removed by
``--rm``. No compose-managed container or volume is touched.

    python3 scripts/e2e/m01_native_edit_drill.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs/briefs/evidence/pilot-prep/m01-native-edit.json"
DAEMON = "harness-worker-daemon-1"
SCOPE = ["src"]
INSIDE = "src/x.txt"
OUTSIDE = "docs/y.txt"
OLD = "before"
NEW = "after"
MARKER = "M01-EDIT-JSON:"
PROBE_NAME = "m01_native_edit_probe.cjs"
# confine.wrap_command overrides the entrypoint to the bundled runtime's exe.
RUNTIME_EXE = (
    "/usr/local/lib/python3.12/site-packages/deepseek_harness_runtime/runtime/"
    "deepseek-harness-sdk-runtime-linux-arm64"
)
LOCAL_FS_MODULE = "/node/node_modules/@deepseek-ai/dsh-fs-local/lib/index.js"

PROBE = r"""
/* Runs inside harness-engine-worker. No model, no provider, no network. */
const fs = require("node:fs");
const path = require("node:path");
const sea = require("node:sea");

/* `dsh --help` ends in process.reallyExit(0). Swallow that one exit so the
   two async editText() calls can finish; we exit ourselves at the end. */
const realReallyExit = process.reallyExit.bind(process);
process.reallyExit = () => {};
const keepAlive = setInterval(() => {}, 1000);

const WORKSPACE = process.env.M01_WORKSPACE;
const VFS = "/tmp/m01-vfs";
const LOCAL_FS = "__LOCAL_FS_MODULE__";
const OLD = "__OLD__";
const NEW = "__NEW__";

/* The runtime is a Node SEA whose assets hold the real pkg VFS archive.
   Materialise its JS/JSON files into the container's tmpfs so Node can
   resolve the real module and everything it imports. */
function extract() {
  const manifest = JSON.parse(sea.getAsset("__pkg_manifest__", "utf8"));
  const archive = Buffer.from(sea.getRawAsset("__pkg_archive__"));
  let n = 0;
  for (const [p, [offset, length]] of Object.entries(manifest.offsets)) {
    if (!p.startsWith("/node/node_modules/")) continue;
    if (!/\.(js|mjs|cjs|json)$/.test(p)) continue;
    const dest = path.join(VFS, p);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, archive.subarray(offset, offset + length));
    n++;
  }
  return n;
}

async function main() {
  const extracted = extract();
  const { LocalFileSystem } = require(path.join(VFS, LOCAL_FS));
  /* FileSystem extends cordis Service, whose constructor only needs
     ctx.reflect.provide(); no Cordis app is booted. */
  const ctx = { reflect: { provide() {} } };
  const localFs = new LocalFileSystem(
    ctx, { cwd: WORKSPACE, diffBasisMaxBytes: 10 * 1024 * 1024 }
  );
  const results = {};
  for (const rel of ["__INSIDE__", "__OUTSIDE__"]) {
    try {
      /* Exactly the two calls in applyEditTool's execute body. */
      const target = await localFs.resolve(rel);
      await localFs.editText(
        target, { oldString: OLD, newString: NEW, replaceAll: false },
        undefined, undefined
      );
      results[rel] = { ok: true, errno: null, message: null };
    } catch (error) {
      results[rel] = {
        ok: false,
        errno: (error && error.code) || null,
        message: String((error && error.message) || error),
      };
    }
  }
  fs.writeSync(1, "__MARKER__" + JSON.stringify({ extracted, results }) + "\n");
  clearInterval(keepAlive);
  realReallyExit(0);
}

main().catch((error) => {
  fs.writeSync(2, "PROBE-FAILED " + String((error && error.stack) || error) + "\n");
  realReallyExit(2);
});
"""


def _outside_digest(ws: Path) -> dict[str, str]:
    """Hash every regular file outside ``src`` (the M03 drill's rule)."""
    return {
        str(p.relative_to(ws)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(ws.rglob("*"))
        if p.is_file() and not p.is_symlink() and p.relative_to(ws).parts[0] != "src"
    }


def _daemon_image() -> str:
    """Read only HARNESS_ENGINE_IMAGE from the running daemon (no secrets printed)."""
    out = subprocess.run(
        ["docker", "inspect", DAEMON, "--format",
         ('{{range .Config.Env}}{{if eq (index (split . "=") 0) '
          '"HARNESS_ENGINE_IMAGE"}}{{println .}}{{end}}{{end}}')],
        capture_output=True, text=True, check=True,
    )
    for line in out.stdout.splitlines():
        if line.startswith("HARNESS_ENGINE_IMAGE="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("daemon has no HARNESS_ENGINE_IMAGE")


def main() -> int:
    image = _daemon_image()
    subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                   text=True, check=True)  # local image only; never pull

    base = Path(os.environ.get("TMPDIR", "/tmp")) / f"m01-native-edit-{os.getpid()}"
    ws = base / "workspace"
    sessions = base / "sessions"
    for d in ("src", "docs"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    sessions.mkdir(parents=True, exist_ok=True)
    (ws / INSIDE).write_text(OLD + "\n")
    (ws / OUTSIDE).write_text(OLD + "\n")
    (ws / "README.md").write_text("fixture\n")
    # confine._worker_user() runs the worker as the workspace's owner; the
    # fixture must be writable by that uid (the M03 drill does the same).
    for p in [ws, *ws.rglob("*"), sessions]:
        if p.is_dir() and not p.is_symlink():
            os.chmod(p, 0o777)
    for p in ws.rglob("*"):
        if p.is_file():
            os.chmod(p, 0o666)

    probe = (PROBE.replace("__LOCAL_FS_MODULE__", LOCAL_FS_MODULE)
                  .replace("__OLD__", OLD).replace("__NEW__", NEW)
                  .replace("__INSIDE__", INSIDE).replace("__OUTSIDE__", OUTSIDE)
                  .replace("__MARKER__", MARKER))
    (sessions / PROBE_NAME).write_text(probe)

    before = _outside_digest(ws)
    user = f"{os.getuid()}:{os.getgid() or 10001}"  # confine._worker_user, non-root host
    name = f"m01-native-edit-{os.getpid()}"
    # confine.wrap_command, mode=container, scope_paths=["src"]: the workspace
    # is frozen (readonly=true) and only the scope target is bound back writable.
    mounts = [
        "--mount", f"type=bind,src={ws},dst={ws},readonly=true",
        "--mount", f"type=bind,src={sessions},dst={sessions},readonly=false",
        "--mount", f"type=bind,src={ws / 'src'},dst={ws / 'src'},readonly=false",
    ]
    argv = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",  # stricter than the daemon's private net: no dial path at all
        "--read-only", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "512", "--memory", "1g", "--cpus", "1",
        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777",
        "--user", user, "--workdir", str(ws),
        *mounts,
        "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
        "--env", f"M01_WORKSPACE={ws}",
        "--env", f"NODE_OPTIONS=--require={sessions / PROBE_NAME}",
        "--entrypoint", RUNTIME_EXE,
        image, "--help",
    ]
    t0 = time.monotonic()
    run = subprocess.run(argv, capture_output=True, text=True, timeout=180,
                         check=False)
    seconds = round(time.monotonic() - t0, 2)

    payload = None
    for line in run.stdout.splitlines():
        if line.startswith(MARKER):
            payload = json.loads(line[len(MARKER):])
    if payload is None:
        print("probe produced no result; stderr:", run.stderr[-2000:], file=sys.stderr)
        return 2

    results = payload["results"]
    inside = ws / INSIDE
    outside = ws / OUTSIDE
    inside_text = inside.read_text().strip() if inside.exists() else None
    outside_text = outside.read_text().strip() if outside.exists() else None
    after = _outside_digest(ws)
    gone = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{name}$",
                           "--format", "{{.Names}}"], capture_output=True, text=True,
                          check=False)
    container_removed = not gone.stdout.strip()

    refusal = results[OUTSIDE]
    # A read-only refusal is EROFS on macOS bind mounts and EACCES on Linux
    # (STATUS.md, PR #7); either proves the OS layer refused the staging mkdir.
    os_refusal = (not refusal["ok"]) and refusal["errno"] in ("EROFS", "EACCES", "EPERM")
    ok = bool(
        results[INSIDE]["ok"] and inside_text == NEW
        and os_refusal and outside_text == OLD
        and before == after and container_removed
    )
    evidence = {
        "date": time.strftime("%Y-%m-%d"),
        "milestone": "M01 (native edit, separate from M03's native write)",
        "model": "none - direct call to tool-fs edit path, no LLM, no provider",
        "spend": "0",
        "engineImage": image,
        "scopePaths": SCOPE,
        "mounts": [m for m in mounts if m != "--mount"],
        "editCodePath": [
            "@deepseek-ai/dsh-tool-fs/lib/index.js: applyEditTool -> ctx.fs.resolve/editText",
            "@deepseek-ai/dsh-fs-local/lib/index.js: LocalFileSystem.editText -> writeFileAtomic",
        ],
        "vfsFilesExtracted": payload["extracted"],
        "dockerReturnCode": run.returncode,
        "seconds": seconds,
        "inside": {"path": INSIDE, "ok": results[INSIDE]["ok"],
                   "contentAfter": inside_text, "detail": results[INSIDE]},
        "outside": {"path": OUTSIDE, "refusedByOS": os_refusal,
                    "contentAfter": outside_text, "detail": refusal},
        "outsideScopeUnchanged": before == after,
        "filesHashedOutsideSrc": len(before),
        "containerRemoved": container_removed,
        "ok": ok,
        "verdict": "PASS" if ok else "FAIL",
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "mounts"}, indent=2))
    print(f"\n{evidence['verdict']} - M01 native edit, no model", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
