"""harness_sdk — the Python client SDK for the AI Engineering Harness.

Two layers:

- handwritten runtime: ``HarnessHTTP`` (bearer auth + frozen error envelope +
  retries with backoff) and ``WsEventStream`` (ws-ticket acquisition,
  X-WS-Fingerprint presentation, replay/dedupe streaming — extracted from
  section7/src/lib/{api,useEventStream}.ts);
- one generated client module per backend section (identity, github, router,
  orchestrator, sandbox, meeting, review), produced by
  ``scripts/sdk-gen/generate.py`` from the sections' OpenAPI specs.

Start from :class:`harness_sdk.Harness`::

    from harness_sdk import Harness

    sdk = Harness(token)
    sdk.identity.list_projects()

See clients/python/README.md.
"""

from .client import Harness
from .errors import ApiError, parse_api_error
from .runtime import SERVICE_PORTS, HarnessHTTP, backoff_delay
from .ws import WsEventStream

__version__ = "1.0.0rc1"

__all__ = [
    "ApiError",
    "Harness",
    "HarnessHTTP",
    "SERVICE_PORTS",
    "WsEventStream",
    "__version__",
    "backoff_delay",
    "parse_api_error",
]
