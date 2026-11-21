"""Session-wide test seam for Section 4.

M18-C1 fail-closed sweep: bare unit mode (no session factory) used to be an
implicit allow inside the authorization guards. It is now a DENY unless
``HARNESS_AUTHZ_TEST_MODE=1`` is explicitly set — so the environment flag is
turned on here, once, for every legacy suite that wires services without a
DB. Production assemblies never import this package and never set the flag:
for them, an unwired factory denies with AUTHZ_UNAVAILABLE.
"""

import os

os.environ.setdefault("HARNESS_AUTHZ_TEST_MODE", "1")
# In-process fake SDKs by default; the isolation tests opt in explicitly.
os.environ.setdefault("HARNESS_ENGINE_ISOLATED", "0")
# I-09 default is ON in production since the 2026-11-14 decision
# (docs/decisions/2026-11-14-verification-first-default-on.md). Most suites
# here record no engine tool calls, so the session pins the explicit opt-out;
# tests of the default itself use monkeypatch.delenv to see the real default.
os.environ.setdefault("HARNESS_VERIFICATION_FIRST", "false")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_denial_budget():
    """The T18 denial-audit budget is process-global; no test may inherit another's refusals."""
    from harness_orchestrator import collab

    collab._denial_window.clear()
