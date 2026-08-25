"""Fetch the seven section OpenAPI specs into scripts/sdk-gen/specs/.

Run from anywhere; the repo root is located relative to this file. The
recorded specs are the generator's input of record — regenerate them when a
section's API changes (`python scripts/sdk-gen/fetch_specs.py`).

Requires only the Python standard library.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = Path(__file__).resolve().parent / "specs"

SERVICES: dict[int, str] = {
    8001: "identity",
    8002: "github",
    8003: "router",
    8004: "orchestrator",
    8005: "sandbox",
    8006: "meeting",
    8008: "review",
}


def fetch(port: int, retries: int = 3, timeout: float = 10.0) -> dict:
    url = f"http://localhost:{port}/openapi.json"
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.load(r)
        except Exception as err:  # noqa: BLE001 - retry any transport failure
            last_err = err
            if attempt < retries:
                time.sleep(1.0 * attempt)
    raise SystemExit(f"FAILED {url}: {last_err}")


def main() -> int:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for port, name in sorted(SERVICES.items()):
        try:
            spec = fetch(port)
        except SystemExit as err:
            failures.append(str(err))
            continue
        out = SPECS_DIR / f"{name}.openapi.json"
        out.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
        print(f"{name:13} {spec['info']['title']:22} paths={len(spec['paths']):3} -> {out}")
    if failures:
        print("\nSome sections were unreachable — is the compose stack up?")
        print("  docker compose --env-file .env -f deploy/docker-compose.yml up -d")
        for f in failures:
            print(" ", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
