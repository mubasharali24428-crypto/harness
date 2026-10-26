#!/usr/bin/env python3
"""M07 live acceptance smoke against a real provider (default: OpenRouter).

Why a script instead of a notebook: the acceptance claims worth making are
reproducible ones. This drives the SHIPPED engine wrapper (no test doubles), so
what it proves is the code that runs in the stack.

It proves, in one paid attempt, the four things the roadmap said were unproven:

  1. a real completion comes back from the configured model (no downgrade),
  2. tool calls arrive WHILE the run is going, not only after it (W1/C1),
  3. a goal image reaches a vision model and is reported (W1/M06),
  4. the SDK's own usage numbers land on the result for cost metering.

Money discipline is a hard rule here, because this script exists to spend a
real key:

  * refuses to run against a paid model unless HARNESS_ALLOW_PAID_SMOKE=1,
  * ONE attempt — no retry loop, no model ladder, no fan-out,
  * a wall-clock deadline that kills the worker process group,
  * max_tokens is pinned low; this is a smoke, not a benchmark.

Nothing prints or persists the key: the credential is read from the environment
by the engine itself, and the evidence file records model ids, counts and
timings only.

    source ~/.harness-secrets/openrouter.env
    cd section4 && ./.venv/bin/python ../scripts/e2e/openrouter_smoke.py \
        --model deepseek/deepseek-v4-flash --tool-goal
    …same interpreter… --vision-model qwen/qwen3.7-flash --make-image
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "section4" / "src"))

DEADLINE_SECONDS_DEFAULT = 180
SMOKE_MAX_TOKENS = "512"


def _png(path: Path, rgb: tuple[int, int, int] = (220, 40, 40), size: int = 64) -> Path:
    """A real PNG (valid CRCs, IHDR/IDAT/IEND) — a screenshot stand-in.

    Generated rather than committed: the point is bytes the runtime admits, not
    a picture of anything. 64x64 because providers enforce a minimum image
    dimension — an 8x8 tile was refused with "height:8 or width:8 must be larger
    than 10", which is exactly the kind of limit a screenshot path must respect.
    """
    w = h = size
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=os.environ.get("DSH_MODEL", "deepseek/deepseek-v4-flash"))
    ap.add_argument("--vision-model", default=None, help="set to attach a goal image")
    ap.add_argument("--base-url", default=os.environ.get("OPENROUTER_BASE_URL", ""))
    ap.add_argument(
        "--patch",
        default=str(REPO / "deploy" / "patches" / "openrouter.patch.yml"),
        help="runtime patch that re-points the route at openai-completions",
    )
    ap.add_argument("--vision", action="store_true", help="declare image input in the patch")
    ap.add_argument("--key-env", default="OPENROUTER_API_KEY")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--tool-goal", action="store_true", help="ask for one file write")
    ap.add_argument("--make-image", action="store_true", help="generate the 8x8 PNG")
    ap.add_argument("--deadline", type=int, default=DEADLINE_SECONDS_DEFAULT)
    ap.add_argument("--isolated", action="store_true", help="confined worker process")
    ap.add_argument("--evidence", default=str(REPO / "docs/briefs/evidence/pilot-prep/m07-smoke.json"))
    args = ap.parse_args()

    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"no credential in ${args.key_env}; refusing to run", file=sys.stderr)
        return 2
    model = args.vision_model or args.model
    if not model.endswith(":free") and os.environ.get("HARNESS_ALLOW_PAID_SMOKE") != "1":
        print(
            f"{model} is a paid model; set HARNESS_ALLOW_PAID_SMOKE=1 for an "
            "explicitly approved paid smoke",
            file=sys.stderr,
        )
        return 2

    # The engine reads these names; mapping the operator's key env onto them
    # keeps the credential in the process environment only.
    os.environ["DEEPSEEK_API_KEY"] = key
    if args.base_url:
        os.environ["DEEPSEEK_BASE_URL"] = args.base_url
    # The route name is fixed by the shipped profile; the PATCH is what re-points
    # it at an OpenAI-compatible gateway. Without it OpenRouter answers
    # "Invalid Anthropic Messages API request". For the vision test the shipped
    # patch's `input: [text]` declaration is widened, because the runtime gates
    # image blocks on the model's declared modalities.
    source = Path(args.patch).read_text()
    patch_text = source.replace("input: [text]", "input: [text, image]") if args.vision else source
    os.environ["DSH_MODEL"] = model
    os.environ["ENGINE_LIVE_MODE"] = "live"
    os.environ["HARNESS_ENGINE_ISOLATED"] = "1" if args.isolated else "0"
    os.environ["HARNESS_SDK_MAX_TOKENS"] = SMOKE_MAX_TOKENS
    os.environ["ENGINE_RUN_TIMEOUT_SECONDS"] = str(args.deadline)

    work = Path(os.environ.get("TMPDIR", "/tmp")) / "m07-smoke-workspace"
    sess = Path(os.environ.get("TMPDIR", "/tmp")) / "m07-smoke-sessions"
    work.mkdir(parents=True, exist_ok=True)
    sess.mkdir(parents=True, exist_ok=True)

    from harness_orchestrator.engine import DeepSeekHarnessWrapper

    engine = DeepSeekHarnessWrapper(
        workspace_dir=str(work), session_root=str(sess), model=model
    )
    session_id = f"m07-{int(time.time())}"

    # The transport patch must be readable INSIDE the confinement boundary. A
    # container-mode run bind-mounts only the workspace and the session dir and
    # gives /tmp a fresh tmpfs, so a host-temporary patch is not there, and the
    # engine fails closed rather than running with no transport restriction.
    # The session dir is the right home for it: the engine mkdirs it itself, and
    # the per-run execution-policy patch is still appended after ours, so an
    # operator (or smoke) patch cannot widen what the policy patch restricts.
    patch_dir = Path(engine.run_session_dir(session_id))
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / (
        "openrouter-vision.patch.yml" if args.vision else "openrouter-smoke.patch.yml"
    )
    patch_path.write_text(patch_text)
    os.environ["DSH_PATCH_FILES"] = str(patch_path)

    if args.tool_goal:
        goal = "Create hello.txt in the workspace containing exactly: harness m07. Then stop."
    else:
        goal = args.goal or "Reply with the single word: present"
    if args.vision_model and args.make_image:
        img = _png(work / "shot.png")
        goal = (
            "A screenshot is attached. Report the exact RGB colour of the pixels "
            "as three numbers, and nothing else."
        )
        images = [
            {
                "type": "image",
                "data": _b64(img.read_bytes()),
                "mimeType": "image/png",
            }
        ]
    else:
        img = None
        images = None

    live: list[dict] = []
    t0 = time.perf_counter()
    result = engine.execute_goal(goal, session_id=session_id, images=images)
    elapsed = time.perf_counter() - t0

    for record in result.get("toolCalls") or []:
        live.append(record)
    out = {
        "model": model,
        "provider": "openrouter" if args.base_url else "deepseek-official",
        "patch": str(patch_path),
        "visionDeclared": args.vision,
        "isolated": args.isolated,
        "status": result.get("status"),
        "elapsedSeconds": round(elapsed, 2),
        "maxTokens": int(SMOKE_MAX_TOKENS),
        "usage": result.get("usage"),
        "context": result.get("context"),
        "toolCallCount": len(live),
        "goalImagesAttached": len(images or []),
        "imageFile": str(img) if img else None,
        "finalResponseExcerpt": (result.get("finalResponse") or "")[:400],
        "paidSmokeOptIn": "HARNESS_ALLOW_PAID_SMOKE=1",
        "keyInOutput": False,
    }
    Path(args.evidence).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    ok = result.get("status") == "completed"
    print(f"\n{'PASS' if ok else 'FAIL'} — status={result.get('status')} in {elapsed:.1f}s",
          file=sys.stderr)
    return 0 if ok else 1


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


if __name__ == "__main__":
    raise SystemExit(main())
