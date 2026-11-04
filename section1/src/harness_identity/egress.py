"""One egress rule for every destination an admin can point the product at.

Webhooks and SIEM collectors (and anything else that POSTs to a customer-
supplied URL) must obey the same law, because the risk is the same: a stored
destination is an outbound request made by a privileged service, so it can be
used to reach internal endpoints, to leak a signature to a look-alike host, or
to walk a redirect into somewhere the operator never allow-listed.

The rule lives here once:

* HTTPS, and only the default port — no `http:`, no `:8443`;
* no userinfo, query or fragment — credentials and tokens do not belong in a
  destination we store and echo back to other admins;
* the host must appear on an operator allow-list (`HARNESS_*_ALLOWED_HOSTS`):
  storing a URL is not the same as being granted egress to it;
* no literal non-global IP, and no `.localhost`/`.local` names;
* delivery re-resolves DNS and refuses private answers (a rebind after the
  admin saved the value is still refused) — the container egress firewall stays
  the real boundary, this is the guard rail in front of it.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from fastapi import HTTPException
from harness_contracts import error_body


def allowed_hosts(env_var: str) -> set[str]:
    return {
        part.strip().lower().rstrip(".")
        for part in os.getenv(env_var, "").split(",")
        if part.strip()
    }


def host_is_public(host: str) -> bool:
    """Reject private DNS answers at delivery time; egress firewall remains required."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(answers) and all(ipaddress.ip_address(row[4][0]).is_global for row in answers)
    except (OSError, ValueError):
        return False


def validate_public_https(url: str, *, allowed: set[str], code: str, label: str) -> str:
    """Normalize-and-refuse. `code` is the caller's error code, `label` its noun,
    so each surface keeps its own stable wire contract over one shared rule."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        raise HTTPException(
            status_code=422, detail=error_body(code, f"invalid {label} URL")
        ) from None
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        port = None
        host = ""
    if (
        parsed.scheme != "https"
        or not host
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or host not in allowed
    ):
        raise HTTPException(
            status_code=422,
            detail=error_body(
                code, f"HTTPS URL host must be operator-allow-listed for the {label}"
            ),
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".localhost", ".local")
        ):
            raise HTTPException(
                status_code=422,
                detail=error_body(code, "local destinations are forbidden"),
            ) from None
    else:
        if not ip.is_global:
            raise HTTPException(
                status_code=422,
                detail=error_body(code, "non-public destinations are forbidden"),
            )
    return url.strip()
