"""Single entry point for scripting the Harness.

One object, seven section clients, one event stream::

    from harness_sdk import Harness

    sdk = Harness(token)                      # bearer token from /auth/login
    sdk.identity.list_projects()
    sdk.orchestrator.get_run_status(run_id)
    for envelope in sdk.events.stream(project_id, token):
        ...

See clients/python/README.md and the "Scripting the API" section of
docs/TUTORIAL.md.
"""

from __future__ import annotations

import os

from .errors import ApiError  # noqa: F401  (re-exported for scripters)
from .github import GithubClient
from .identity import (
    EventReplayResponse,
    IdentityClient,
    LoginRequest,
    OkResponse,
    SessionResponse,
)
from .meeting import MeetingClient
from .orchestrator import OrchestratorClient
from .review import ReviewClient
from .router import RouterClient
from .runtime import SERVICE_PORTS, HarnessHTTP, base_url_for
from .sandbox import SandboxClient
from .ws import WsEventStream

__all__ = ["Harness", "HarnessHTTP", "WsEventStream", "ApiError", "__version__"]
__version__ = "0.1.0"


class Harness:
    """Aggregate SDK: ``sdk.<service>.<method>(...)`` plus ``sdk.events``.

    Args:
        token: bearer session token (mint one via :meth:`login`, or
               request-otp/verify-otp below, or pass an existing token).
        timeout: per-request timeout in seconds.
        max_retries: transient-failure retries (429/502/503/504 + transport).
        <service>_url: per-service base override; default is the
            ``HARNESS_<SERVICE>_URL`` env var, else
            ``http://localhost:<default port>``.
    """

    #: Declared up front so type checkers see every section client.
    identity: IdentityClient
    github: GithubClient
    router: RouterClient
    orchestrator: OrchestratorClient
    sandbox: SandboxClient
    meeting: MeetingClient
    review: ReviewClient
    events: WsEventStream

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        identity_url: str | None = None,
        github_url: str | None = None,
        router_url: str | None = None,
        orchestrator_url: str | None = None,
        sandbox_url: str | None = None,
        meeting_url: str | None = None,
        review_url: str | None = None,
    ) -> None:
        self.token = token
        overrides = {
            "identity": identity_url, "github": github_url, "router": router_url,
            "orchestrator": orchestrator_url, "sandbox": sandbox_url,
            "meeting": meeting_url, "review": review_url,
        }
        self.http = HarnessHTTP(token, timeout=timeout, max_retries=max_retries)
        bases = {s: (overrides[s] or base_url_for(s)).rstrip("/") for s in overrides}
        # The WsEventStream shares this http transport so ws-ticket minting
        # rides the same bearer auth + retry policy as every REST call.
        self.events = WsEventStream(self.http, identity_base=bases["identity"])
        self.identity = IdentityClient(self.http, bases["identity"])
        self.github = GithubClient(self.http, bases["github"])
        self.router = RouterClient(self.http, bases["router"])
        self.orchestrator = OrchestratorClient(self.http, bases["orchestrator"])
        self.sandbox = SandboxClient(self.http, bases["sandbox"])
        self.meeting = MeetingClient(self.http, bases["meeting"])
        self.review = ReviewClient(self.http, bases["review"])

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Auth helpers (section1 flows; see docs/TUTORIAL.md §2)
    # ------------------------------------------------------------------

    @classmethod
    def login(cls, email: str, *, team_name: str | None = None,
              identity_url: str | None = None, **kwargs) -> "Harness":
        """Legacy passwordless login -> ready ``Harness``.

        Raises ``ApiError(code="OWNERSHIP_REQUIRED")`` when the deployment
        requires inbox ownership (the default since H2r) — catch it and fall
        back to ``request_otp`` + ``verify_otp``.
        """
        probe = cls(None, identity_url=identity_url, **kwargs)
        try:
            req: LoginRequest = {"email": email}
            if team_name:
                req["teamName"] = team_name
            body: SessionResponse = probe.identity.login(req)
        finally:
            probe.close()
        harness = cls(body["token"], identity_url=identity_url, **kwargs)
        harness.principal = dict(body.get("principal") or {})
        return harness

    def request_otp(self, email: str) -> OkResponse:
        """Ownership login step 1: email a single-use code. Dev stacks print
        it to the identity container logs — grep ``HARNESS-OTP``."""
        req: LoginRequest = {"email": email}
        return self.identity.request_login(req)

    def verify_otp(self, email: str, code: str) -> SessionResponse:
        """Ownership login step 2: swap the code for a session. Updates this
        instance's token in place and returns the raw response."""
        body: SessionResponse = self.identity.verify_otp_route(
            {"email": email, "code": code})
        self.token = body["token"]
        self.http.token = body["token"]
        self.principal = dict(body.get("principal") or {})
        return body

    def replay_events(self, project_id: str, after_seq: int = 0,
                      limit: int | None = None) -> EventReplayResponse:
        """REST replay page — the non-streaming alternative to
        ``sdk.events.stream()``; returns ``{items, nextCursor}``."""
        if limit is not None:
            return self.identity.replay_events(project_id, after_seq, limit)
        return self.identity.replay_events(project_id, after_seq)


def env_token(var: str = "HARNESS_TOKEN") -> str | None:
    """Convenience: read a session token from the environment."""
    return os.getenv(var)
