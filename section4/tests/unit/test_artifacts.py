"""Part 48.004 / 48.009 (lane AR): artifact upload, download, preview, retention and quarantine.
Sqlite with the REAL migration 0049, a fake clock and a blob store under tmp_path.
Behaviour: docs/artifacts.md."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from harness_orchestrator import artifact_store, artifacts

from . import test_artifacts_support as support
from .test_artifacts_support import ELF, PDF, PNG, err, sha

env = support.env  # the shared fixture

RESERVED = {"artifact.uploaded", "artifact.downloaded", "artifact.deleted", "artifact.quarantined"}
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "artifact not found"}}


# ------------------------------------------------------------- upload and download


def test_upload_then_download_is_an_attachment_with_nosniff_and_a_server_chosen_type(env):
    data = b"hello report\n"
    r = env.upload("lead", "r1", "notes.txt", data, ctype="text/html")  # client claim ignored
    assert r.status_code == 201 and r.json()["replayed"] is False
    art = r.json()["artifact"]
    assert art["state"] == "available" and art["sha256"] == sha(data)
    assert art["mediaType"] == "text/plain; charset=utf-8" and art["sizeBytes"] == len(data)
    assert "storageRef" not in art and "storage_ref" not in art
    c = env.as_("view").get(env.url("r1", f"/artifacts/{art['id']}/content"))  # a viewer may read
    assert c.status_code == 200 and c.content == data
    assert c.headers["content-disposition"].startswith("attachment;")
    assert c.headers["x-content-type-options"] == "nosniff"
    assert c.headers["content-type"].startswith("text/plain")
    assert "sandbox" in c.headers["content-security-policy"]
    assert c.headers["cache-control"] == "private, no-store"
    listing = env.as_("view").get(env.url("r1", "/artifacts")).json()
    assert [i["id"] for i in listing["items"]] == [art["id"]]


@pytest.mark.parametrize(
    "name",
    [
        "../x.txt",
        "a/b.txt",
        "a\\b.txt",
        ".hidden",
        "x‮txt.exe",
        "bell\x07.txt",
        "",
        "x" * 121,
        "trailing.",
        "  ",
        "nul\x00.txt",
    ],
)
def test_unsafe_names_are_refused(env, name):
    err(env.upload("lead", "r1", name, b"x"), 422, "BAD_NAME")
    assert env.blob_files() == []


def test_header_injection_through_the_name_is_impossible(env):
    art = env.put("lead", "r1", 'rep"ort final ü.txt', b"x")
    c = env.as_("lead").get(env.url("r1", f"/artifacts/{art['id']}/content"))
    cd = c.headers["content-disposition"]
    assert 'filename="rep_ort_final__.txt"' in cd and "filename*=UTF-8''" in cd
    assert "\n" not in cd and "\r" not in cd


def test_store_key_comes_from_the_hash_never_from_the_name(env):
    data = b"payload"
    env.put("lead", "r1", "weird name.txt", data)
    [blob] = env.blob_files()
    assert blob.name == sha(data) and "weird" not in str(blob)
    for bad in ("../x", "/etc/passwd", "ab/cd", "x" * 64, "0" * 16 + "/00/" + "g" * 64):
        with pytest.raises(ValueError):
            artifact_store.get(bad)
    with pytest.raises(ValueError):
        artifact_store.storage_ref("t", "../../etc/passwd")


def test_same_bytes_in_two_teams_are_two_blobs_and_deleting_one_keeps_the_other(env):
    a = env.put("lead", "r1", "same.txt", b"same bytes")
    b = env.put("evil", "r3", "same.txt", b"same bytes")
    assert len(env.blob_files()) == 2
    assert env.as_("lead").delete(env.url("r1", f"/artifacts/{a['id']}")).status_code == 200
    assert len(env.blob_files()) == 1
    kept = env.as_("evil").get(env.url("r3", f"/artifacts/{b['id']}/content"))
    assert kept.content == b"same bytes"


def test_size_cap_empty_body_and_run_quota(env, monkeypatch):
    monkeypatch.setenv("HARNESS_ARTIFACT_MAX_BYTES", "100")
    err(env.upload("lead", "r1", "big.txt", b"x" * 101), 413, "ARTIFACT_TOO_LARGE")
    err(env.upload("lead", "r1", "none.txt", b""), 422, "EMPTY_ARTIFACT")
    assert env.put("lead", "r1", "ok.txt", b"x" * 100)["sizeBytes"] == 100
    monkeypatch.setenv("HARNESS_ARTIFACT_RUN_MAX_COUNT", "2")
    env.put("lead", "r1", "two.txt", b"2")
    err(env.upload("lead", "r1", "three.txt", b"3"), 409, "ARTIFACT_QUOTA_EXCEEDED")
    monkeypatch.setenv("HARNESS_ARTIFACT_RUN_MAX_COUNT", "50")
    monkeypatch.setenv("HARNESS_ARTIFACT_RUN_MAX_BYTES", "105")
    err(env.upload("lead", "r1", "bytes.txt", b"123456"), 409, "ARTIFACT_QUOTA_EXCEEDED")
    assert len(env.blob_files()) == 2  # refused uploads wrote nothing


def test_idempotent_upload_and_the_lost_race(env):
    first = env.upload("lead", "r1", "a.txt", b"one", requestId="req-1")
    again = env.upload("lead", "r1", "a.txt", b"one", requestId="req-1")
    assert (first.status_code, again.status_code) == (201, 200) and again.json()["replayed"]
    assert first.json()["artifact"]["id"] == again.json()["artifact"]["id"]
    err(
        env.upload("lead", "r1", "a.txt", b"different", requestId="req-1"), 409, "REQUEST_ID_REUSED"
    )
    err(env.upload("lead", "r1", "a.txt", b"x", requestId="bad id!"), 422, "BAD_REQUEST_ID")
    assert env.q("SELECT COUNT(*) AS n FROM artifacts")[0].n == 1

    async def race():
        p = env.principal("lead")
        return await asyncio.gather(
            *[
                artifacts.create_artifact(
                    env.factory,
                    p,
                    "r1",
                    name="r.txt",
                    kind="upload",
                    data=b"race",
                    request_id="req-race",
                )
                for _ in range(4)
            ]
        )

    res = asyncio.run(race())
    assert len({a["id"] for a, _ in res}) == 1 and sum(1 for _, rep in res if not rep) == 1
    assert env.q("SELECT COUNT(*) AS n FROM artifacts WHERE request_id='req-race'")[0].n == 1


def test_unreadable_or_corrupt_bytes_are_refused_not_served(env):
    art = env.put("lead", "r1", "a.txt", b"original")
    [blob] = env.blob_files()
    blob.write_bytes(b"tampered")
    err(
        env.as_("lead").get(env.url("r1", f"/artifacts/{art['id']}/content")),
        410,
        "ARTIFACT_CONTENT_MISSING",
    )
    blob.unlink()
    err(
        env.as_("lead").get(env.url("r1", f"/artifacts/{art['id']}/content")),
        410,
        "ARTIFACT_CONTENT_MISSING",
    )


# -------------------------------------------------------------------------- quarantine

PE = b"MZ" + b"\x00" * 58 + (64).to_bytes(4, "little") + b"PE\x00\x00" + b"\x00" * 20
TAR = b"a" * 257 + b"ustar" + b"\x00" * 100
QUARANTINE_CASES = [
    ("tool.bin", ELF, "executable_magic:elf"),
    ("tool.dat", PE, "executable_magic:pe"),
    ("run.txt", b"#!/bin/sh\nrm -rf /\n", "script_magic:shebang"),
    ("bundle.dat", b"PK\x03\x04" + b"\x00" * 30, "archive_magic:zip"),
    ("bundle.dat", b"\x1f\x8b\x08" + b"\x00" * 30, "archive_magic:gzip"),
    ("bundle.dat", TAR, "archive_magic:tar"),
    ("setup.exe", b"just text", "executable_extension:.exe"),
    ("report.pdf.exe", PDF, "executable_extension:.exe"),
    ("deploy.sh", b"echo hi", "script_extension:.sh"),
    ("helper.py", b"print(1)", "script_extension:.py"),
    ("page.svg", b"<svg/>", "active_extension:.svg"),
    ("page.html", b"<p>x</p>", "active_extension:.html"),
    ("notes.txt", b"hi <script>alert(1)</script>", "active_content_markup"),
    ("notes.txt", b"<!DOCTYPE html><html></html>", "active_content_markup"),
    ("photo.png", b"<html>not a png</html>", "extension_magic_mismatch:.png"),
    ("doc.pdf", b"plain text", "extension_magic_mismatch:.pdf"),
    ("photo.jpg", PNG, "extension_magic_mismatch:.jpg"),
]


@pytest.mark.parametrize(("name", "data", "reason"), QUARANTINE_CASES)
def test_dangerous_content_is_quarantined_by_magic_extension_and_markup(env, name, data, reason):
    art = env.put("lead", "r1", name, data)
    assert art["state"] == "quarantined" and art["download"] == "administer_only"
    assert reason in art["scan"]["reasons"] and art["previewable"] is False
    assert art["mediaType"] == "application/octet-stream"
    q = env.audit("artifact.quarantined")
    assert len(q) == 1 and q[0].target_id == art["id"]
    assert reason in json.loads(q[0].detail)["reasons"]


@pytest.mark.parametrize(
    ("name", "data", "sniffed"),
    [
        ("notes.txt", b"plain notes\n", "text"),
        ("data.json", b'{"a": 1}', "text"),
        ("pic.png", PNG, "png"),
        ("doc.pdf", PDF, "pdf"),
        ("MZ.txt", b"MZ report: all good", "text"),
        ("blob.dat", bytes(range(1, 200)), "binary"),
        ("log.log", "ünï\n".encode(), "text"),
    ],
)
def test_benign_content_stays_available(env, name, data, sniffed):
    art = env.put("lead", "r1", name, data)
    assert art["state"] == "available" and art["scan"]["sniffed"] == sniffed
    assert art["scan"]["reasons"] == []


def test_quarantined_content_is_downloadable_only_by_an_administer_holder(env):
    art = env.put("lead", "r1", "tool.bin", ELF)
    url = env.url("r1", f"/artifacts/{art['id']}/content")
    for who in ("lead", "eng2", "view", "guest"):  # even the uploader
        err(env.as_(who).get(url), 403, "ARTIFACT_QUARANTINED")
    denials = [json.loads(r.detail) for r in env.audit("authz.denied")]
    assert {d["reason"] for d in denials} >= {"ARTIFACT_QUARANTINED"}
    ok = env.as_("boss").get(url)
    assert ok.status_code == 200 and ok.content == ELF
    assert ok.headers["content-type"] == "application/octet-stream"
    assert ok.headers["x-artifact-state"] == "quarantined"
    assert ok.headers["content-disposition"].startswith("attachment;")
    # a preview of quarantined content never exists, for anybody
    err(
        env.as_("boss").get(env.url("r1", f"/artifacts/{art['id']}/preview")),
        409,
        "PREVIEW_UNAVAILABLE",
    )
    err(
        env.as_("lead").get(env.url("r1", f"/artifacts/{art['id']}/preview")),
        403,
        "ARTIFACT_QUARANTINED",
    )
    err(env.as_("evil").get(url), 404, "NOT_FOUND")  # another team's admin: no such artifact


def test_administer_is_asked_of_the_policy_engine_when_the_team_has_an_org(env, monkeypatch):
    from harness_contracts import policy_enforce

    art = env.put("lead", "r1", "tool.bin", ELF)
    asked = []

    async def org(db, team):
        return "org-1"

    async def decide(db, org_id, principal, perm, chain, now):
        asked.append((principal.userId, perm, [f"{n.level}:{n.id}" for n in chain]))

        class D:
            allowed = principal.userId == "eng2" and perm == "administer"
            reason = "test policy"

        return D()

    monkeypatch.setattr(policy_enforce, "team_org", org)
    monkeypatch.setattr(policy_enforce, "decide_request", decide)
    url = env.url("r1", f"/artifacts/{art['id']}/content")
    assert env.as_("eng2").get(url).status_code == 200  # granted by policy, not by role
    err(env.as_("boss").get(url), 403, "ARTIFACT_QUARANTINED")  # admin role, policy says no
    assert ("eng2", "administer", ["org:org-1", "team:team-a", "run:r1"]) in asked


# ------------------------------------------------------------------------------ preview


def test_preview_is_json_text_only_and_never_html(env):
    body = b"<b>bold?</b> line\x00 one\x1b[31m\nline two\n"
    art = env.put("lead", "r1", "notes.txt", body.replace(b"\x00", b""))
    r = env.as_("view").get(env.url("r1", f"/artifacts/{art['id']}/preview"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["x-content-type-options"] == "nosniff"
    out = r.json()
    assert out["previewable"] and out["kind"] == "text" and out["render"] == "text-only"
    assert "<b>bold?</b>" in out["text"]  # a JSON string: shown as text, never parsed as markup
    assert "\x1b" not in out["text"]
    big = env.put("lead", "r1", "big.txt", b"y" * (artifacts.PREVIEW_CHARS + 10))
    out = env.as_("lead").get(env.url("r1", f"/artifacts/{big['id']}/preview")).json()
    assert out["truncated"] and len(out["text"]) == artifacts.PREVIEW_CHARS


def test_non_text_and_oversized_files_are_download_only(env, monkeypatch):
    png = env.put("lead", "r1", "pic.png", PNG)
    pdf = env.put("lead", "r1", "doc.pdf", PDF)
    for a in (png, pdf):
        out = env.as_("lead").get(env.url("r1", f"/artifacts/{a['id']}/preview")).json()
        assert out["previewable"] is False and out["reason"] == "NOT_TEXT" and "text" not in out
    monkeypatch.setattr(artifacts, "PREVIEW_SOURCE_MAX", 10)
    long = env.put("lead", "r1", "long.txt", b"z" * 50)
    out = env.as_("lead").get(env.url("r1", f"/artifacts/{long['id']}/preview")).json()
    assert out["previewable"] is False and out["reason"] == "TOO_LARGE"


# ----------------------------------------------------------- access control, no oracle


def test_foreign_and_unknown_ids_answer_the_same_404_everywhere(env):
    art = env.put("lead", "r1", "a.txt", b"secret")
    aid = art["id"]
    calls = [
        ("get", "/artifacts", None),
        ("get", f"/artifacts/{aid}", None),
        ("get", f"/artifacts/{aid}/content", None),
        ("get", f"/artifacts/{aid}/preview", None),
        ("delete", f"/artifacts/{aid}", None),
    ]
    for method, tail, _ in calls:
        foreign = getattr(env.as_("evil"), method)(env.url("r1", tail))  # other team's run
        unknown = getattr(env.as_("evil"), method)(env.url("nope", tail))
        assert foreign.status_code == unknown.status_code == 404
        assert foreign.json() == unknown.json()
    # a team-b caller on its own run asking for team-a's artifact id, or for an unknown id
    for tail in ("", "/content", "/preview"):
        foreign = env.as_("evil").get(env.url("r3", f"/artifacts/{aid}{tail}"))
        unknown = env.as_("evil").get(env.url("r3", f"/artifacts/{'0' * 36}{tail}"))
        assert foreign.status_code == unknown.status_code == 404
        assert foreign.json()["detail"] == unknown.json()["detail"] == NOT_FOUND
        # and the same team but the wrong run
        wrong_run = env.as_("lead").get(env.url("r2", f"/artifacts/{aid}{tail}"))
        assert wrong_run.status_code == 404 and wrong_run.json()["detail"] == NOT_FOUND
    err(
        env.as_("evil").post(env.url("r1", "/artifacts"), params={"name": "x.txt"}, content=b"x"),
        404,
        "NOT_FOUND",
    )
    assert env.q("SELECT COUNT(*) AS n FROM artifacts")[0].n == 1


def test_read_only_roles_may_read_but_not_write_and_the_denial_is_audited(env):
    art = env.put("lead", "r1", "a.txt", b"x")
    for who in ("view", "guest"):
        err(env.upload(who, "r1", "b.txt", b"x"), 403, "ROLE_DENIED")
        err(env.as_(who).delete(env.url("r1", f"/artifacts/{art['id']}")), 403, "ROLE_DENIED")
        assert env.as_(who).get(env.url("r1", f"/artifacts/{art['id']}")).status_code == 200
    assert len(env.audit("authz.denied")) >= 4


def test_delete_is_for_the_uploader_or_an_administer_holder_and_removes_the_bytes(env):
    art = env.put("lead", "r1", "a.txt", b"bytes")
    url = env.url("r1", f"/artifacts/{art['id']}")
    err(env.as_("eng2").delete(url), 403, "NOT_ARTIFACT_OWNER")
    assert len(env.blob_files()) == 1
    gone = env.as_("lead").delete(url).json()["artifact"]
    assert gone["state"] == "deleted" and env.blob_files() == []
    assert env.as_("lead").delete(url).status_code == 200  # idempotent
    err(env.as_("lead").get(url + "/content"), 410, "ARTIFACT_DELETED")
    assert env.as_("lead").get(env.url("r1", "/artifacts")).json()["items"] == []
    shown = env.as_("lead").get(env.url("r1", "/artifacts"), params={"state": "deleted"})
    assert [i["id"] for i in shown.json()["items"]] == [art["id"]]  # tombstone kept
    other = env.put("lead", "r1", "b.txt", b"other")
    assert env.as_("boss").delete(env.url("r1", f"/artifacts/{other['id']}")).status_code == 200
    assert len(env.audit("artifact.deleted")) == 2


# ---------------------------------------------------------------------------- retention


def test_retention_expires_reads_and_the_sweep_removes_bytes(env):
    short = env.put("lead", "r1", "short.txt", b"short", retentionDays=1)
    default = env.put("lead", "r1", "long.txt", b"long")
    assert short["retentionUntil"].startswith("2030-01-02")
    assert default["retentionUntil"].startswith("2030-04-01")  # 90 days
    env.tick(days=2)
    got = env.as_("lead").get(env.url("r1", f"/artifacts/{short['id']}")).json()["artifact"]
    assert got["state"] == "expired" and got["download"] == "unavailable"
    err(
        env.as_("lead").get(env.url("r1", f"/artifacts/{short['id']}/content")),
        410,
        "ARTIFACT_EXPIRED",
    )
    assert len(env.blob_files()) == 2  # expiry alone does not touch bytes
    assert asyncio.run(artifacts.purge_expired(env.factory)) == 1
    assert [p.name for p in env.blob_files()] == [sha(b"long")]
    purged = env.audit("artifact.deleted")
    assert len(purged) == 1 and json.loads(purged[0].detail)["reason"] == "retention"
    assert asyncio.run(artifacts.purge_expired(env.factory)) == 0


# --------------------------------------------------------------------- audit, lineage


def test_audit_is_ids_only_and_uses_only_reserved_names(env):
    art = env.put("lead", "r1", "customer-secrets-name.txt", b"TOP SECRET FILE CONTENT")
    env.as_("lead").get(env.url("r1", f"/artifacts/{art['id']}/content"))
    env.put("lead", "r1", "evil-tool.exe", b"x")
    rows = env.audit()
    names = {r.action for r in rows}
    assert names <= RESERVED, names
    assert {"artifact.uploaded", "artifact.downloaded", "artifact.quarantined"} <= names
    for r in rows:
        blob = r.detail
        assert "TOP SECRET" not in blob and "customer-secrets-name" not in blob
        assert "evil-tool" not in blob
        d = json.loads(blob)
        assert d["teamId"] == "team-a" and d["userId"] == "lead"


def test_derived_from_and_change_references_must_belong_to_the_run(env):
    base = env.put("lead", "r1", "base.txt", b"base")
    other_run = env.put("lead", "r2", "other.txt", b"other")
    foreign = env.put("evil", "r3", "foreign.txt", b"foreign")
    child = env.put("lead", "r1", "child.txt", b"child", derivedFrom=base["id"])
    for bad in (other_run["id"], foreign["id"], "nope"):
        err(env.upload("lead", "r1", "x.txt", b"x", derivedFrom=bad), 422, "BAD_REFERENCE")
    err(env.upload("lead", "r1", "x.txt", b"x", changeId="nope"), 422, "BAD_REFERENCE")
    edge = env.q("SELECT from_id, to_id, relation FROM artifact_lineage")
    assert [(e.from_id, e.to_id, e.relation) for e in edge] == [
        (child["id"], base["id"], "derived_from")
    ]


def test_a_failing_policy_engine_denies_instead_of_allowing(env, monkeypatch):
    from harness_contracts import policy_enforce

    art = env.put("lead", "r1", "tool.bin", ELF)

    async def org(db, team):
        return "org-1"

    async def boom(*a, **k):
        raise RuntimeError("policy store exploded")

    monkeypatch.setattr(policy_enforce, "team_org", org)
    monkeypatch.setattr(policy_enforce, "decide_request", boom)
    err(
        env.as_("boss").get(env.url("r1", f"/artifacts/{art['id']}/content")),
        403,
        "ARTIFACT_QUARANTINED",
    )


def test_unwired_database_is_a_clean_503(env, monkeypatch):
    monkeypatch.setattr(artifacts, "_factory", lambda: None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(artifacts._handled(_raise_unwired()))
    assert e.value.status_code == 503


async def _raise_unwired():
    artifacts._need_factory()
