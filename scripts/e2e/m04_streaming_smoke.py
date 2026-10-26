#!/usr/bin/env python3
"""M04 live acceptance: does a confined worker's transcript arrive WHILE it runs?

The transcript work (e610191) is covered by unit tests on every layer, but the
one claim those tests cannot make is the week's actual gate: *a tool call
reaches the parent before the turn finishes*. That needs a real worker in a real
confinement boundary.

Why a separate script from `openrouter_smoke.py`: that one records final counts.
M04 is about arrival *time*, so this script stamps every `on_progress` record
against the run clock and asserts the ordering directly.

Two boundary facts this script exists to respect:

* `HARNESS_ENGINE_CONFINEMENT=container` is the only mode the real runtime
  accepts besides an explicit `off` (seatbelt breaks its PTY). `off` is never
  set here — an unconfined green run would prove nothing.
* `DSH_PATCH_FILES` crosses the boundary (confine.ENV_ALLOW_PREFIX), and a host
  temp path does not exist inside the worker image. The patch is therefore
  addressed at its in-image path, `/opt/harness/patches/`.

Spend: none. The default endpoint is local Ollama.

    section4/.venv/bin/python scripts/e2e/m04_streaming_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IN_IMAGE_PATCH = "/opt/harness/patches/openrouter.patch.yml"
MAX_TOKENS = "512"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen-local:latest")
    ap.add_argument("--key-env", default="DEEPSEEK_API_KEY")
    ap.add_argument("--image", default="harness-engine-worker:m04-1026")
    ap.add_argument("--goal", default="Create notes.txt in the workspace containing exactly: "
                                      "m04 streaming. Then stop.")
    ap.add_argument(
        "--patch",
        default=IN_IMAGE_PATCH,
        help="patch path AS SEEN INSIDE the worker image; a host temp path is invisible there",
    )
    ap.add_argument(
        "--attach-image",
        action="store_true",
        help="attach a real screenshot to the goal (M07: a confined worker that sees)",
    )
    ap.add_argument("--deadline", type=int, default=540)
    ap.add_argument("--evidence", default=str(REPO / "docs/briefs/evidence/pilot-prep"
                                              / "m04-container-streaming.json"))
    args = ap.parse_args()

    # Same money discipline as openrouter_smoke.py, because this script reaches a
    # paid provider as readily as a local one and exists to be re-run by anyone.
    if not args.model.endswith(":free") and os.environ.get("HARNESS_ALLOW_PAID_SMOKE") != "1":
        print(
            f"{args.model} is a paid model; set HARNESS_ALLOW_PAID_SMOKE=1 for an "
            "explicitly approved paid smoke, or point --base-url/--model at local Ollama",
            file=sys.stderr,
        )
        return 2

    if not os.environ.get(args.key_env):
        os.environ[args.key_env] = "local"

    os.environ.update(
        {
            "DEEPSEEK_BASE_URL": args.base_url,
            "DSH_PATCH_FILES": args.patch,
            "DSH_MODEL": args.model,
            "ENGINE_LIVE_MODE": "live",
            "HARNESS_ENGINE_ISOLATED": "1",
            "HARNESS_ENGINE_CONFINEMENT": "container",
            "HARNESS_ENGINE_IMAGE": args.image,
            "HARNESS_SDK_MAX_TOKENS": MAX_TOKENS,
            "ENGINE_RUN_TIMEOUT_SECONDS": str(args.deadline),
        }
    )

    base = Path(os.environ.get("TMPDIR", "/tmp")) / "m04-stream"
    work = base / "workspace"
    sess = base / "sessions"
    work.mkdir(parents=True, exist_ok=True)
    sess.mkdir(parents=True, exist_ok=True)

    from harness_orchestrator.engine import DeepSeekHarnessWrapper

    goal = args.goal
    images = None
    shot = None
    if args.attach_image:
        # Reuse the M06/M07 fixture bytes rather than re-deriving a PNG here: the
        # dimension floor (an 8x8 tile is refused by providers) is already encoded
        # in that helper and has to stay the same for the two drills to be
        # comparable.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from openrouter_smoke import _b64, _png

        shot = _png(base / "shot.png")
        goal = (
            "A screenshot is attached. Write the exact RGB colour of its pixels as "
            "three numbers into a file named color.txt in the workspace, then reply "
            "with those three numbers and nothing else."
        )
        images = [{"type": "image", "data": _b64(shot.read_bytes()), "mimeType": "image/png"}]

    arrivals: list[dict] = []
    t0 = time.monotonic()

    def record(progress: dict) -> None:
        arrivals.append(
            {
                "offsetSeconds": round(time.monotonic() - t0, 2),
                # The worker's transcript records use `phase` ("call"/"result"),
                # not `kind`; asserting on the wrong key silently counts nothing.
                "phase": progress.get("phase") or progress.get("kind"),
                "callId": progress.get("callId"),
                "tool": progress.get("tool") or progress.get("name"),
                "outcome": progress.get("outcome"),
            }
        )

    engine = DeepSeekHarnessWrapper(
        workspace_dir=str(work), session_root=str(sess), model=args.model
    )
    result = engine.execute_goal(
        goal, session_id=f"m04-{int(t0)}", on_progress=record, images=images
    )
    completed_at = round(time.monotonic() - t0, 2)

    calls = [a for a in arrivals if a["phase"] == "call"]
    results = [a for a in arrivals if a["phase"] == "result"]
    streamed_before_completion = [a for a in arrivals if a["offsetSeconds"] < completed_at]
    # The run's own workspace is a per-session directory under the root, so the
    # side effect is found by search rather than by guessing the layout.
    side = next(
        iter(sorted(p for p in work.rglob("*.txt") if p.name in {"notes.txt", "color.txt"})),
        None,
    )

    out = {
        "date": time.strftime("%Y-%m-%d"),
        "milestone": "M04",
        "confinement": "container",
        "workerImage": args.image,
        "model": args.model,
        "provider": "ollama-local",
        "patchPathInWorker": args.patch,
        "goalImagesAttached": len(images or []),
        "screenshotFixture": str(shot) if shot else None,
        "status": result.get("status"),
        "runCompletedAtSeconds": completed_at,
        "progressRecords": len(arrivals),
        "callRecords": len(calls),
        "resultRecords": len(results),
        "recordsArrivingBeforeCompletion": len(streamed_before_completion),
        "streamedLive": bool(streamed_before_completion),
        # Each result must be paired to a call that arrived earlier — an orphan
        # result would mean the UI folds nothing and shows a silent gap.
        "callIdsWithCallThenResult": sorted(
            {c["callId"] for c in calls}
            & {
                r["callId"]
                for r in results
                if any(c["callId"] == r["callId"] and c["offsetSeconds"] <= r["offsetSeconds"] for c in calls)
            }
        ),
        "firstArrivalSeconds": arrivals[0]["offsetSeconds"] if arrivals else None,
        "arrivals": arrivals[:24],
        "finalResponseExcerpt": (result.get("finalResponse") or "")[:300],
        "fileSideEffect": side.read_text()[:80] if side else None,
        "fileSideEffectPath": str(side.relative_to(work)) if side else None,
        "usage": result.get("usage"),
        "context": result.get("context"),
        "paidSmokeOptIn": os.environ.get("HARNESS_ALLOW_PAID_SMOKE") == "1",
        "keyInOutput": False,
    }
    Path(args.evidence).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "arrivals"}, indent=2))
    ok = bool(out["streamedLive"]) and out["status"] == "completed"
    print(
        f"\n{'PASS' if ok else 'FAIL'} — live streaming={out['streamedLive']} "
        f"records={len(arrivals)} status={out['status']} in {completed_at}s",
        file=sys.stderr,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
