import json
import pathlib

import pytest

from harness_sandbox import browser_cap

FIXTURE = (pathlib.Path(__file__).parents[3]
           / "workers/browser/test/fixtures/browser_cap_golden.json")
ARGS = dict(task_id="task-1", team_id="team-a", project_id="proj-1", run_id="run-1",
            allowed_origins=["http://127.0.0.1:4100"], now=1000, nonce="n-golden")
WHO = dict(team_id="team-a", project_id="proj-1", run_id="run-1")


def test_roundtrip_and_denials():
    tok = browser_cap.mint("s", **ARGS)
    got = browser_cap.verify(tok, "s", now=1001, **WHO)
    assert got["allowedOrigins"] == ARGS["allowed_origins"]
    with pytest.raises(ValueError, match="signature"):
        browser_cap.verify(tok, "other", now=1001, **WHO)
    with pytest.raises(ValueError, match="expired"):
        browser_cap.verify(tok, "s", now=1300, **WHO)
    with pytest.raises(ValueError, match="different run"):
        browser_cap.verify(tok, "s", now=1001, **{**WHO, "team_id": "team-b"})


def test_golden_fixture_matches_ts_consumer():
    """Regenerates the fixture the TS worker verifies; fails if it drifted."""
    token = browser_cap.mint("golden-secret", **ARGS)
    payload = browser_cap.verify(token, "golden-secret", now=1001, **WHO)
    golden = {"secret": "golden-secret", "now": 1001, "token": token, "payload": payload}
    if not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n")
    assert json.loads(FIXTURE.read_text()) == golden
