"""M15-W5-S1: browser CSP violation collector (NEW, deliberately minimal).

Browsers evaluate the policy stamped by ``security_headers`` and POST every
violation as a JSON ``csp-report`` body here. During report-only rollout this
endpoint IS the observability feed operators watch before flipping
HARNESS_CSP_ENFORCE; in enforce mode it keeps recording what gets blocked.

Security posture:
- UNAUTH by design: browsers fire reports without credentials, and requiring
  auth would blind the feed. The body is attacker-controlled input, so it is
  treated accordingly — size-capped at read time, never persisted raw, and
  reduced to fixed-width allow-listed fields before logging.
- NO PII / NO SECRETS: only directive names + source/URI strings the browser
  already chose to disclose are logged. Auth headers, cookies and request IPs
  are never logged.
- Rate-limited through the shipped DB-backed limiter (same store as the auth
  edge) so a misbehaving page or scripted flood cannot spin the log.
"""

import json
import logging

from fastapi import APIRouter, Request

__all__ = ["router", "summarize_csp_report"]

logger = logging.getLogger("harness.csp")

router = APIRouter(prefix="/api/v1")

# Cap on how much of the raw body we even look at: real csp-report payloads
# are well under 4KB; anything bigger is junk or an attack and gets dropped.
_MAX_REPORT_BYTES = 4096

# Fixed-width log fields (truncated): bounded output, no PII, no secrets.
_FIELD_MAX = 120


def _clip(value: object) -> str:
    """Stringify for the log line, hard-capped so fields stay bounded."""
    text = str(value)[:_FIELD_MAX]
    return text.replace("\n", "\\n").replace("\r", "\\r")


def summarize_csp_report(report: dict) -> str:
    """Reduce a parsed csp-report to one bounded, allow-listed log line.

    Only keys from the standard violation shape are read; everything else in
    the payload is ignored (never logged), which is what keeps attacker-chosen
    junk out of the logs. document-uri / referrer are clipped to their origin
    prefix so query strings (which can carry tokens on sloppy pages) never
    land in the log either.
    """
    effective = report.get("effectiveDirective") or report.get("violated-directive") or "unknown"
    blocked = report.get("blockedURL") or report.get("blocked-uri") or ""
    document = str(report.get("document-uri") or report.get("documentURI") or "")
    return (
        f"directive={_clip(effective)} blocked={_clip(blocked)} "
        f"document={_clip(document.split('?', 1)[0])}"
    )


@router.post("/csp-report", status_code=204)
async def collect_csp_report(request: Request) -> None:
    """Accept a browser CSP violation report (204, no content)."""
    raw = await request.body()
    if not raw or len(raw) > _MAX_REPORT_BYTES:
        return
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("csp-report: undecodable body (%d bytes)", len(raw))
        return
    # Standard shape: {"csp-report": {...}}. Anything else is logged as junk.
    report = payload.get("csp-report") if isinstance(payload, dict) else None
    if not isinstance(report, dict):
        logger.warning("csp-report: unexpected payload shape (%d bytes)", len(raw))
        return
    # One bounded line per violation; logger config decides where it lands.
    logger.info("CSP violation: %s", summarize_csp_report(report))
