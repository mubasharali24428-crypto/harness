import pytest


@pytest.fixture(autouse=True)
def _allow_unconfined_tests(monkeypatch):
    """Tests exercise LocalSandboxRunner with confinement='off'.

    Production requires the same env var; setting it per-test keeps the
    explicit-off gate honest without weakening it.
    """
    monkeypatch.setenv("HARNESS_SANDBOX_ALLOW_UNCONFINED", "1")
