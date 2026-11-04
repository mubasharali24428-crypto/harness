#!/usr/bin/env python3
"""Generate Section 5's egress allow-list from the provider registry.

W4 requires the sandbox/web allow-list to be *derived from* the provider
registry rather than hand-maintained in three places. Sections may not import
each other, so the derivation happens here, at build/review time, and the result
is a checked-in JSON artifact that Section 5 loads at runtime:

    section5/src/harness_sandbox/egress_allowlist.json

Run it after changing ``section3/src/harness_router/provider_registry.py``:

    python3 scripts/gen_egress_allowlist.py            # rewrite the artifact
    python3 scripts/gen_egress_allowlist.py --check    # fail if it is stale

``section5/tests/unit/test_egress_allowlist.py`` runs the same derivation, so a
stale artifact fails the suite instead of surviving review.

What is derived and what is added
---------------------------------
* ``providerEndpoints`` — host of every provider's ``base_url`` (the endpoints
  the registry declares we talk to).
* ``providerDocs``     — host of every provider's ``docs_url`` (what a web tool
  may read; a sandboxed shell may not).
* ``packageRegistries`` and ``gitHosts`` — reviewed additions. They are not
  provider endpoints, so they cannot be derived; they live here so the artifact
  has exactly one author.

Hosts that the registry declares but that are not real destinations
(``replace-with-…`` templates, loopback, private ranges) are dropped. "Derived"
must never mean "reachable by accident".
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "section3" / "src" / "harness_router" / "provider_registry.py"
OUTPUT_PATH = REPO_ROOT / "section5" / "src" / "harness_sandbox" / "egress_allowlist.json"

#: Not provider endpoints: the registries installs pull from and the git hosts
#: a plan may fetch from. Kept here (not in section3) because section3 does not
#: own them; reviewed deliberately, not generated.
PACKAGE_REGISTRIES = ("files.pythonhosted.org", "npmjs.com", "pypi.org", "registry.npmjs.org")
GIT_HOSTS = ("api.github.com", "github.com", "gitlab.com")

_PLACEHOLDER_MARKERS = ("replace-with", "your-account", "your-resource", "your-workspace", "example.")
_RESERVED_EXACT = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})
_PRIVATE_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "0.")


def normalize_host(url: str) -> str:
    """Host only: lower-case, no userinfo, no port, no brackets."""
    parsed = urlparse(url)
    netloc = parsed.netloc or parsed.path
    host = netloc.rsplit("@", 1)[-1].split(":")[0].strip().lower()
    return host


def is_real_destination(host: str) -> bool:
    if not host or host.startswith("["):
        return False
    if any(marker in host for marker in _PLACEHOLDER_MARKERS):
        return False
    if host in _RESERVED_EXACT or host.endswith(".localhost") or host.endswith(".local"):
        return False
    if host.startswith(_PRIVATE_PREFIXES):
        return False
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        if 16 <= second <= 31:
            return False
    return "." in host  # a bare name is not a public destination


def _load_registry():
    spec = importlib.util.spec_from_file_location("_harness_provider_registry", REGISTRY_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging failure
        raise SystemExit(f"cannot load provider registry at {REGISTRY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def build() -> dict[str, object]:
    registry = _load_registry()
    endpoints: set[str] = set()
    docs: set[str] = set()
    for provider in registry.ALL_PROVIDERS:
        for url, bucket in ((provider.base_url, endpoints), (provider.docs_url, docs)):
            if not url:
                continue
            host = normalize_host(url)
            if is_real_destination(host):
                bucket.add(host)
    return {
        "generatedBy": "scripts/gen_egress_allowlist.py",
        "source": "section3/src/harness_router/provider_registry.py",
        "providerEndpoints": sorted(endpoints),
        "providerDocs": sorted(docs),
        "packageRegistries": sorted(PACKAGE_REGISTRIES),
        "gitHosts": sorted(GIT_HOSTS),
    }


def render(data: dict[str, object]) -> str:
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail instead of writing when stale")
    args = parser.parse_args(argv)

    text = render(build())
    current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
    if args.check:
        if current != text:
            print(f"STALE: {OUTPUT_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
            print("Regenerate: python3 scripts/gen_egress_allowlist.py", file=sys.stderr)
            return 1
        print(f"ok: {OUTPUT_PATH.relative_to(REPO_ROOT)} is current")
        return 0
    OUTPUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
