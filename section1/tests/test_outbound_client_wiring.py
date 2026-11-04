"""W15 gap: outbound clients must go through the proxy-safe factory.

Docker Desktop exports ``NO_PROXY=...,::1,[::1]`` and httpx 0.28 turns
``[::1]`` into a mount pattern whose port parses as ``:1]``; constructing the
client then raises before any request. Section 1's SSO path was fixed by
routing through ``harness_contracts.http_client.env_client``; Sections 2, 4 and
6 still constructed their clients directly, carrying the same latent failure.
This guard keeps the remaining outbound call sites on that factory.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECTIONS = ("section2/src", "section4/src", "section6/src")
DIRECT = re.compile(r"httpx\.(?:AsyncClient|Client)\(")


def test_no_direct_httpx_client_construction_outside_the_factory():
    offenders = []
    for section in SECTIONS:
        for path in (ROOT / section).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if DIRECT.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, (
        "construct these through harness_contracts.http_client.env_client "
        f"(survives a bracketed IPv6 NO_PROXY): {offenders}"
    )
