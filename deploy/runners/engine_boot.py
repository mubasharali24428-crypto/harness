#!/usr/bin/env python3
"""Engine boot validator — validate-or-exit (M11 Wave 1, lane W1-B1).

Closes finding C-004's deploy half: containers used to boot with whatever
the compose defaults happened to provide (placeholder ``DEEPSEEK_API_KEY=ollama``
plus a host-Ollama base URL), silently downgrading every run. Now the
engine environment is validated BEFORE uvicorn starts; a missing or
degenerate configuration exits non-zero with an actionable message.

Secrecy law (brief W1-B1 hard rule): this script prints environment
variable NAMES and non-secret configuration (mode, model name, base URL)
— never credential VALUES. The API key is read only through
``live_mode.validate_engine_env``, whose summary carries no secrets.

Deployment wiring: the orchestrator/sandbox services run

    python /opt/harness/deploy/runners/engine_boot.py && exec uvicorn ...

in their compose ``command:``, so a bad config fails the container (and
its healthcheck) instead of serving degraded answers.

Boot policy (brief W1-B1): an UNSET ``ENGINE_LIVE_MODE`` is itself a
failure — a deploy must explicitly choose live or dry; the engine never
guesses (and never silently downgrades a configured model).

Exit codes:
    0 — configuration valid (summary printed, names only)
    2 — invalid/missing configuration (EngineConfigError path)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]
_SECTION4_SRC = HARNESS_ROOT / "section4" / "src"
if _SECTION4_SRC.is_dir():
    sys.path.insert(0, str(_SECTION4_SRC))
# Importing harness_orchestrator pulls in harness_contracts (frozen package,
# Section 1). When neither is installed (bare deploy checkout), both source
# trees must be on sys.path or boot dies with an import traceback instead of
# the actionable EngineConfigError message this script exists to print.
_CONTRACTS_SRC = HARNESS_ROOT / "contracts" / "src"
if _CONTRACTS_SRC.is_dir():
    sys.path.insert(0, str(_CONTRACTS_SRC))

from harness_orchestrator.live_mode import (  # noqa: E402 (path bootstrap first)
    ENGINE_LIVE_MODE_ENV,
    EngineConfigError,
    validate_engine_env,
)


def main(argv: list[str] | None = None) -> int:
    try:
        summary = validate_engine_env(os.environ, require_explicit_mode=True)
    except EngineConfigError as exc:
        print(f"ENGINE BOOT FAILED: {exc}", file=sys.stderr)
        print(
            f"Set {ENGINE_LIVE_MODE_ENV}=dry to run without model credentials, "
            f"or provide the required variables listed above and retry "
            f"with {ENGINE_LIVE_MODE_ENV}=live.",
            file=sys.stderr,
        )
        return 2
    mode = summary["mode"]
    print(f"engine boot OK: mode={mode}")
    if mode == "live":
        # Names and non-secret values ONLY — never the API key itself.
        print(f"  DEEPSEEK_BASE_URL={summary.get('base_url') or '<sdk default>'}")
        print(f"  DSH_MODEL={summary.get('model')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
