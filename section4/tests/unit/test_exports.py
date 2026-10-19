"""Part 50 (lane SE): history and audit exports. Sqlite with the REAL migrations, a fake clock and a
temporary export store. Behaviour: docs/search-and-export.md."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from harness_orchestrator import exports

from . import test_search_support as support
from .test_search_support import P, err

env = support.env  # the shared fixture

RESERVED = {"export.requested", "export.completed", "export.refused", "export.downloaded"}


def lines(resp):
    return [json.loads(x) for x in resp.text.splitlines()]


def seed_run(env):
    """r1 with comments, events (durable and noise), an approval, agent events, secrets."""
    env.post("lead", "run", "r1", "first comment")
    env.tick(seconds=1)
    env.event("participant.joined", {"role": "participant"}, actor="eng2")
    env.tick(seconds=1)
    env.event(
        "redirect.guidance",
        {
            "content": "add tests",
            "apiToken": "tok-123456",
            "reasoning": "private chain",
            "reason": "because",
        },
    )
    env.event("presence.joined", {"x": 1})
    env.event("guidance.delivered", {"upTo": 2})
    env.tick(seconds=1)
    env.approval("ap1")
    env.tick(seconds=1)
    env.agent_event("engine_completed", {"summary": "done with password=hunter2"})
    env.agent_event("terminal_output", {"summary": "raw log"})
    env.tick(seconds=1)
    env.post("eng2", "run", "r1", "second comment")


# ------------------------------------------------------------ content and ordering


def test_run_history_export_has_ordered_records_with_actor_identity(env):
    seed_run(env)
    job = env.export("lead", kind="run_history", scopeId="r1")
    assert job["state"] == "completed" and job["format"] == "json" and job["recordCount"] > 0
    doc = env.export_doc("lead", job["id"]).json()
    assert doc["format"] == "harness.export/1" and doc["scope"] == {"kind": "run", "id": "r1"}
    recs = doc["records"]
    assert [r["order"] for r in recs] == list(range(1, len(recs) + 1))
    stamps = [r["ts"] for r in recs]
    assert stamps == sorted(stamps)
    kinds = [(r["stream"], r["kind"]) for r in recs]
    assert ("collab", "participant.joined") in kinds and ("approval", "approval.pending") in kinds
    assert ("agent", "engine_completed") in kinds
    first_comment = next(r for r in recs if r["stream"] == "comment")
    assert first_comment["actor"] == {"userId": "lead", "displayName": "Name lead", "kind": "human"}
    joined = next(r for r in recs if r["kind"] == "participant.joined")
    assert joined["actor"]["userId"] == "eng2" and isinstance(joined["seq"], int)
    assert doc["ordering"] and doc["timestamps"] == "ISO 8601, UTC" and doc["redaction"]


def test_exports_contain_no_secrets_reasoning_noise_or_raw_output(env):
    seed_run(env)
    job = env.export("lead", kind="run_history", scopeId="r1")
    body = env.export_doc("lead", job["id"]).text
    for forbidden in (
        "tok-123456",
        "hunter2",
        "private chain",
        "apiToken",
        '"reasoning"',
        "presence.joined",
        "guidance.delivered",
        "raw log",
        "terminal_output",
    ):
        assert forbidden not in body, forbidden
    assert "[redacted]" in body and '"because"' in body


def test_export_is_deterministic_for_the_same_data(env):
    seed_run(env)
    a = env.export_doc("lead", env.export("lead", kind="run_history", scopeId="r1")["id"]).json()
    env.tick(seconds=5)
    b = env.export_doc("lead", env.export("lead", kind="run_history", scopeId="r1")["id"]).json()
    assert a["records"] == b["records"]
    assert a["generatedAt"] != b["generatedAt"] and a["exportId"] != b["exportId"]


def test_ndjson_format_has_header_records_and_a_complete_footer(env):
    seed_run(env)
    job = env.export("lead", kind="run_history", scopeId="r1", format="ndjson")
    resp = env.export_doc("lead", job["id"])
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    rows = lines(resp)
    assert rows[0]["_type"] == "header" and rows[-1] == {
        "_type": "footer",
        "complete": True,
        "recordCount": len(rows) - 2,
    }
    assert {r["_type"] for r in rows[1:-1]} == {"record"}
    assert [r["order"] for r in rows[1:-1]] == list(range(1, len(rows) - 1))


def test_window_limits_the_records(env):
    seed_run(env)
    since = env.plus(seconds=-3).isoformat()
    job = env.export("lead", kind="run_history", scopeId="r1", **{"from": since})
    doc = env.export_doc("lead", job["id"]).json()
    assert {r["stream"] for r in doc["records"]} <= {"agent", "comment"} | {"approval", "collab"}
    assert all(r["ts"] >= since for r in doc["records"]) and doc["window"]["from"] == since
    err(
        env.as_("lead").post(
            f"{P}/exports",
            json={"kind": "run_history", "scopeId": "r1", "from": "2031-01-01", "to": "2030-01-01"},
        ),
        422,
        "BAD_RANGE",
    )


def test_hidden_deleted_and_past_retention_comment_text_is_withheld(env):
    hid = env.post("eng2", "run", "r1", "hidden words")["id"]
    gone = env.post("eng2", "run", "r1", "deleted words")["id"]
    env.post("eng2", "run", "r1", "plain words")
    env.as_("boss").post(
        f"{P}/runs/r1/comments/{hid}/moderate", json={"action": "hide", "reason": "off topic"}
    )
    env.as_("eng2").delete(f"{P}/runs/r1/comments/{gone}")
    body = env.export_doc("lead", env.export("lead", kind="run_history", scopeId="r1")["id"]).text
    assert "plain words" in body and "hidden words" not in body and "deleted words" not in body
    withheld = {
        r["id"]: r["data"]["bodyWithheld"]
        for r in json.loads(body)["records"]
        if r["stream"] == "comment"
    }
    assert withheld[hid] == "hidden" and withheld[gone] == "deleted"
    admin = env.export_doc("boss", env.export("boss", kind="run_history", scopeId="r1")["id"]).text
    assert "hidden words" in admin and "deleted words" not in admin
    env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 30})
    env.tick(days=31)
    late = env.export_doc("boss", env.export("boss", kind="run_history", scopeId="r1")["id"]).text
    assert "plain words" not in late and "hidden words" not in late


def test_session_history_covers_session_comments_and_linked_run_events(env):
    env.post("lead", "session", "s1", "session level note")
    env.post("lead", "run", "r1", "run level note")
    env.event("participant.joined", {"role": "participant"}, actor="eng2")
    env.event("participant.joined", {"role": "participant"}, run="r2", actor="eng2")
    doc = env.export_doc(
        "lead", env.export("lead", kind="session_history", scopeId="s1")["id"]
    ).json()
    bodies = [r["data"].get("body") for r in doc["records"] if r["stream"] == "comment"]
    assert sorted(bodies) == ["run level note", "session level note"]
    assert [r["runId"] for r in doc["records"] if r["kind"] == "participant.joined"] == ["r1"]
    assert doc["session"]["linkedRuns"] == ["r1"]
    assert {m["userId"] for m in doc["session"]["members"]} == {"lead", "eng2", "view"}
    assert "SECRET" not in json.dumps(doc)


def test_include_actors_false_uses_pseudonyms_and_drops_the_roster(env):
    seed_run(env)
    job = env.export("lead", kind="session_history", scopeId="s1", includeActors=False)
    doc = env.export_doc("lead", job["id"]).json()
    assert "members" not in doc["session"] and doc["actorsIncluded"] is False
    actors = {json.dumps(r["actor"], sort_keys=True) for r in doc["records"] if r["actor"]}
    assert all("pseudonym" in a for a in actors) and len(actors) >= 2
    blob = json.dumps(doc["records"])
    assert "Name lead" not in blob and '"userId"' not in blob.replace('"userId":null', "")


# ------------------------------------------------------------------ authorization


@pytest.mark.parametrize("user", ["view", "guest"])
def test_roles_without_export_permission_are_refused_and_audited(env, user):
    r = env.as_(user).post(f"{P}/exports", json={"kind": "run_history", "scopeId": "r1"})
    err(r, 403, "ROLE_DENIED")
    assert env.q("SELECT COUNT(*) AS n FROM export_jobs")[0].n == 0
    refused = env.audit("export.refused")
    assert len(refused) == 1 and json.loads(refused[0].detail)["reason"] == "ROLE_DENIED"
    err(env.as_(user).get(f"{P}/exports"), 403, "ROLE_DENIED")


def test_audit_evidence_is_admin_only(env):
    err(env.as_("lead").post(f"{P}/exports", json={"kind": "audit_evidence"}), 403, "ROLE_DENIED")
    job = env.export("boss", kind="audit_evidence")
    assert job["scope"] == {"kind": "team", "id": "team-a"}


def test_foreign_unknown_and_private_scopes_answer_the_same_404(env):
    calls = [
        ("run_history", "r3"),
        ("run_history", "nope"),
        ("session_history", "sb"),
        ("session_history", "spriv"),
        ("session_history", "nope"),
    ]
    bodies = []
    for kind, scope in calls:
        user = "eng2" if scope == "spriv" else "lead"
        r = env.as_(user).post(f"{P}/exports", json={"kind": kind, "scopeId": scope})
        bodies.append(err(r, 404, "NOT_FOUND"))
    assert all(b == bodies[0] for b in bodies)
    assert env.q("SELECT COUNT(*) AS n FROM export_jobs")[0].n == 0
    assert len(env.audit("export.refused")) == len(calls)
    ok = env.as_("boss").post(f"{P}/exports", json={"kind": "session_history", "scopeId": "spriv"})
    assert ok.status_code == 201  # admins see every session of their team
    err(env.as_("lead").post(f"{P}/exports", json={"kind": "run_history"}), 422, "BAD_SCOPE")


def test_audit_evidence_is_team_scoped_scrubbed_and_ordered(env):
    env.x(
        "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id,"
        " detail, ts) VALUES ('m-lead', 'human', 'x.did', 'agent_run', 'r1', :d, :ts)",
        d=json.dumps(
            {
                "userId": "lead",
                "teamId": "team-a",
                "note": "ok",
                "apiToken": "tok-999",
                "thoughts": "xx",
                "free": "password=hunter2",
            }
        ),
        ts=env.clock["t"],
    )
    env.x(
        "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id,"
        " detail, ts) VALUES ('m-evil', 'human', 'x.foreign', 'agent_run', 'r3', '{}', :ts)",
        ts=env.clock["t"],
    )
    env.x(
        "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id,"
        " detail, ts) VALUES (NULL, 'human', 'authz.denied', 'agent_run', 'r1', :d, :ts)",
        d=json.dumps({"userId": "eng3", "teamId": "team-a"}),
        ts=env.clock["t"],
    )
    doc = env.export_doc("boss", env.export("boss", kind="audit_evidence")["id"]).json()
    actions = [r["kind"] for r in doc["records"]]
    assert "x.did" in actions and "authz.denied" in actions and "x.foreign" not in actions
    did = next(r for r in doc["records"] if r["kind"] == "x.did")
    assert did["actor"]["displayName"] == "Name lead" and did["data"]["detailRedacted"] is True
    assert did["data"]["detail"] == {
        "userId": "lead",
        "teamId": "team-a",
        "note": "ok",
        "free": "password=[redacted]",
    }
    assert [r["seq"] for r in doc["records"]] == sorted(r["seq"] for r in doc["records"])
    narrowed = env.export_doc(
        "boss", env.export("boss", kind="audit_evidence", scopeId="r1")["id"]
    ).json()
    assert {r["data"]["targetId"] for r in narrowed["records"]} == {"r1"}


# ---------------------------------------------------------- size limits, jobs, retries


def test_over_the_record_limit_is_refused_with_a_stable_code_and_no_file(env, monkeypatch):
    seed_run(env)
    monkeypatch.setenv("HARNESS_EXPORT_MAX_RECORDS", "3")
    r = env.as_("lead").post(f"{P}/exports", json={"kind": "run_history", "scopeId": "r1"})
    detail = err(r, 413, "EXPORT_TOO_LARGE")
    assert detail["limit"] == "records" and detail["max"] == 3
    job = env.q("SELECT state, error_code, storage_ref FROM export_jobs")[0]
    assert (job.state, job.error_code, job.storage_ref) == ("refused", "EXPORT_TOO_LARGE", None)
    assert (
        not any(Path(exports.store_dir()).glob("*")) if Path(exports.store_dir()).exists() else True
    )
    names = [a.action for a in env.audit() if a.action.startswith("export.")]
    assert names == ["export.requested", "export.refused"]
    listed = env.as_("lead").get(f"{P}/exports").json()["exports"]
    assert listed[0]["state"] == "refused" and listed[0]["download"] is None


def test_over_the_byte_limit_is_refused_too(env, monkeypatch):
    seed_run(env)
    monkeypatch.setenv("HARNESS_EXPORT_MAX_BYTES", "500")
    detail = err(
        env.as_("lead").post(f"{P}/exports", json={"kind": "run_history", "scopeId": "r1"}),
        413,
        "EXPORT_TOO_LARGE",
    )
    assert detail["limit"] == "bytes"


def test_a_retried_request_id_is_one_job(env):
    seed_run(env)
    first = env.export("lead", kind="run_history", scopeId="r1", requestId="req-1")
    again = env.export("lead", expect=200, kind="run_history", scopeId="r1", requestId="req-1")
    assert again["id"] == first["id"] and again["duplicate"] is True
    assert env.q("SELECT COUNT(*) AS n FROM export_jobs")[0].n == 1
    other = env.export("eng2", kind="run_history", scopeId="r1", requestId="req-1")
    assert other["id"] != first["id"]  # ids are per requester


def test_concurrent_duplicate_requests_create_one_job(env):
    seed_run(env)

    async def go():
        body = exports.ExportBody(kind="run_history", scopeId="r1", requestId="race")
        return await asyncio.gather(
            *[exports.create_export(env.factory, env.principal("lead"), body) for _ in range(4)]
        )

    out = asyncio.run(go())
    assert len({view["id"] for view, _ in out}) == 1
    assert sum(1 for _, created in out if created) == 1
    assert env.q("SELECT COUNT(*) AS n FROM export_jobs")[0].n == 1


def test_job_record_lists_requester_scope_format_size_and_digest(env):
    seed_run(env)
    job = env.export("lead", kind="run_history", scopeId="r1", format="ndjson")
    data = env.export_doc("lead", job["id"]).content
    assert job["requestedBy"] == "lead" and job["scope"] == {"kind": "run", "id": "r1"}
    assert job["sizeBytes"] == len(data) and job["sha256"] == hashlib.sha256(data).hexdigest()
    assert job["expiresAt"] > job["createdAt"] and job["downloadCount"] == 0
    assert env.as_("lead").get(f"{P}/exports/{job['id']}").json()["id"] == job["id"]
    assert [j["id"] for j in env.as_("lead").get(f"{P}/exports").json()["exports"]] == [job["id"]]
    assert env.as_("eng2").get(f"{P}/exports").json()["exports"] == []
    assert [j["id"] for j in env.as_("boss").get(f"{P}/exports").json()["exports"]] == [job["id"]]


# ------------------------------------------------------------------ download, expiry


def test_download_headers_checksum_and_audit(env):
    seed_run(env)
    job = env.export("lead", kind="run_history", scopeId="r1")
    resp = env.as_("lead").get(f"{P}/exports/{job['id']}/download")
    assert resp.headers["content-disposition"].startswith("attachment; filename=")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-export-sha256"] == job["sha256"]
    assert hashlib.sha256(resp.content).hexdigest() == job["sha256"]
    env.export_doc("boss", job["id"])
    assert env.as_("lead").get(f"{P}/exports/{job['id']}").json()["downloadCount"] == 2
    audit = env.audit("export.downloaded")
    assert [json.loads(a.detail)["userId"] for a in audit] == ["lead", "boss"]
    names = [a.action for a in env.audit() if a.action.startswith("export.")]
    assert set(names) <= RESERVED and names[:2] == ["export.requested", "export.completed"]
    stored = list(Path(exports.store_dir()).iterdir())
    assert [p.name for p in stored] == [f"{job['id']}.json"] and (
        stored[0].stat().st_mode & 0o777
    ) == 0o600


def test_other_engineers_foreign_teams_and_unknown_ids_cannot_download(env):
    seed_run(env)
    job = env.export("lead", kind="run_history", scopeId="r1")
    unknown = env.as_("eng2").get(f"{P}/exports/nope/download")
    for user in ("eng2", "evil"):
        r = env.as_(user).get(f"{P}/exports/{job['id']}/download")
        assert err(r, 404, "EXPORT_NOT_FOUND") == err(unknown, 404, "EXPORT_NOT_FOUND")
        assert env.as_(user).get(f"{P}/exports/{job['id']}").status_code == 404


def test_download_reauthorises_the_scope(env):
    env.post("lead", "session", "s1", "x")
    job = env.export("eng2", kind="session_history", scopeId="s1")
    assert env.as_("eng2").get(f"{P}/exports/{job['id']}/download").status_code == 200
    env.x(
        "UPDATE session_members SET status = 'removed' WHERE session_id = 's1' AND user_id = 'eng2'"
    )
    err(env.as_("eng2").get(f"{P}/exports/{job['id']}/download"), 404, "EXPORT_NOT_FOUND")
    assert env.as_("boss").get(f"{P}/exports/{job['id']}/download").status_code == 200


def test_expired_exports_are_refused_and_the_purge_removes_the_file(env, monkeypatch):
    monkeypatch.setenv("HARNESS_EXPORT_TTL_SECONDS", "60")
    job = env.export("lead", kind="run_history", scopeId="r1")
    path = Path(exports.store_dir()) / f"{job['id']}.json"
    assert path.exists()
    env.tick(seconds=61)
    err(env.as_("lead").get(f"{P}/exports/{job['id']}/download"), 410, "EXPORT_EXPIRED")
    assert env.as_("lead").get(f"{P}/exports/{job['id']}").json()["state"] == "expired"
    assert asyncio.run(exports.purge_expired_exports(env.factory)) == 1
    assert not path.exists()
    assert env.q("SELECT state, storage_ref FROM export_jobs")[0].storage_ref is None


def test_a_tampered_file_fails_its_checksum(env):
    job = env.export("lead", kind="run_history", scopeId="r1")
    path = Path(exports.store_dir()) / f"{job['id']}.json"
    path.write_bytes(path.read_bytes() + b" ")
    err(env.as_("lead").get(f"{P}/exports/{job['id']}/download"), 500, "EXPORT_CORRUPT")


def test_storage_refs_never_come_from_the_client(env):
    with pytest.raises(ValueError):
        exports._file("../../etc/passwd")
    with pytest.raises(ValueError):
        exports._file("abc.json")
    assert exports._remove_file("../x") is False
