"""A4-S1 service tests: review integrity, path safety, roles, tenancy, verify/merge gates."""

from __future__ import annotations

from conftest import P, auth, new_session

ENG = "tok-eng-a"


async def put(client, sid, path, content, base=None, token=ENG):
    body = {"path": path, "content": content}
    if base is not None:
        body["baseSha256"] = base
    return await client.put(f"{P}/code-sessions/{sid}/file", json=body, headers=auth(token))


async def read(client, sid, path, token=ENG):
    return await client.get(
        f"{P}/code-sessions/{sid}/file", params={"path": path}, headers=auth(token)
    )


async def make_cs(client, sid, edits, token=ENG, title="t"):
    return await client.post(
        f"{P}/code-sessions/{sid}/change-sets",
        json={"title": title, "edits": edits},
        headers=auth(token),
    )


def numbered(n=40, repl=None):
    repl = repl or {}
    return "".join(f"{repl.get(i, f'line {i}')}\n" for i in range(1, n + 1))


# ---- auth + tenancy -------------------------------------------------------


async def test_requires_bearer(client):
    assert (await client.get(f"{P}/code-sessions")).status_code == 401
    r = await client.get(f"{P}/code-sessions", headers=auth("nope"))
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_cross_team_session_is_not_found(client):
    sid = await new_session(client)
    r = await read(client, sid, "README.md", token="tok-eng-b")
    assert r.status_code == 404 and r.json()["error"]["code"] == "SESSION_NOT_FOUND"
    # also project spoofing at create
    r = await client.post(
        f"{P}/code-sessions",
        json={"projectId": "proj-a", "repoRef": str(client.repo)},
        headers=auth("tok-eng-b"),
    )
    assert r.status_code == 404


async def test_session_listing_is_team_scoped(client):
    await new_session(client)
    r = await client.get(f"{P}/code-sessions", headers=auth("tok-eng-b"))
    assert r.json()["items"] == []


# ---- path safety ----------------------------------------------------------


async def test_traversal_and_absolute_denied(client):
    sid = await new_session(client)
    for bad in ["../x", "a/../../x", "/etc/passwd", "a\\b", "a/./b"]:
        r = await put(client, sid, bad, "x\n")
        assert r.status_code == 422, bad


async def test_forbidden_paths_hard_constraint_incl_case_variants(client):
    sid = await new_session(client, forbiddenPaths=["secrets"])
    assert (await read(client, sid, "secrets/key.txt")).status_code == 403
    assert (await put(client, sid, "secrets/new.txt", "x\n")).status_code == 403
    # macOS/Windows filesystems are case-insensitive: Secrets/ must not bypass.
    assert (await read(client, sid, "Secrets/key.txt")).status_code == 403
    assert (await put(client, sid, "SECRETS/x.txt", "x\n")).status_code == 403


async def test_git_internals_are_never_writable_or_readable(client):
    """.git/config core.fsmonitor / hooks would execute on the host's own git calls."""
    sid = await new_session(client)
    for p in [".git/config", ".git/hooks/pre-commit", ".GIT/config", "sub/.git/config"]:
        assert (
            await put(client, sid, p, "[core]\n\tfsmonitor = touch /tmp/pwn\n")
        ).status_code in (
            403,
            422,
        ), p
        assert (await read(client, sid, p)).status_code in (403, 404, 422), p


async def test_control_char_0x1f_rejected(client):
    sid = await new_session(client)
    r = await put(client, sid, "a\x1fb.txt", "x\n")
    assert r.status_code == 422


async def test_symlink_escape_blocked(client):
    sid = await new_session(client)
    ws = client.service.workspaces.checkout(sid)
    (ws / "link").symlink_to("/etc")
    r = await read(client, sid, "link/hosts")
    assert r.status_code in (403, 404, 422)
    r = await put(client, sid, "link/pwn", "x\n")
    assert r.status_code in (403, 404, 422)


async def test_repo_ref_must_be_inside_allowed_roots(client, tmp_path):
    outside = tmp_path.parent / "outside-repo"
    outside.mkdir(exist_ok=True)
    r = await client.post(
        f"{P}/code-sessions",
        json={"projectId": "proj-a", "repoRef": str(outside)},
        headers=auth(ENG),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "WORKSPACE_ERROR"
    r = await client.post(
        f"{P}/code-sessions",
        json={"projectId": "proj-a", "repoRef": "https://example.com/x.git"},
        headers=auth(ENG),
    )
    assert r.status_code == 422


async def test_branch_name_cannot_inject_git_options(client):
    r = await client.post(
        f"{P}/code-sessions",
        json={"projectId": "proj-a", "repoRef": str(client.repo), "branch": "--orphan=x"},
        headers=auth(ENG),
    )
    assert r.status_code == 422


async def test_non_utf8_file_is_clean_error_not_500(client):
    sid = await new_session(client)
    ws = client.service.workspaces.checkout(sid)
    (ws / "bin.txt").write_bytes(b"\xff\xfe\xfa")
    r = await read(client, sid, "bin.txt")
    assert r.status_code in (404, 422) and "error" in r.json()


# ---- manual edit: stale base ---------------------------------------------


async def test_write_with_stale_base_conflicts_and_keeps_file(client):
    sid = await new_session(client)
    r = await read(client, sid, "README.md")
    sha = r.json()["sha256"]
    assert (await put(client, sid, "README.md", "one\n", base=sha)).status_code == 200
    r2 = await put(client, sid, "README.md", "two\n", base=sha)
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "STALE_BASE"
    assert (await read(client, sid, "README.md")).json()["content"] == "one\n"


# ---- roles ----------------------------------------------------------------


async def test_read_only_roles_cannot_mutate(client):
    sid = await new_session(client)
    for tok in ("tok-view-a", "tok-guest-a"):
        assert (await put(client, sid, "README.md", "x\n", token=tok)).status_code == 403
        assert (
            await make_cs(client, sid, [{"path": "README.md", "newContent": "x\n"}], token=tok)
        ).status_code == 403
        r = await client.post(
            f"{P}/code-sessions/{sid}/verify",
            json={"schemaVersion": "hap/1", "arguments": {"command": "pytest"}},
            headers=auth(tok),
        )
        assert r.json()["status"] == "denied"
        # reads stay allowed
        assert (await read(client, sid, "README.md", token=tok)).status_code == 200


async def test_viewer_cannot_decide_or_apply_but_can_comment_read(client):
    sid = await new_session(client)
    cs = (await make_cs(client, sid, [{"path": "README.md", "newContent": "hi\n"}])).json()
    cid = cs["changeSetId"]
    r = await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={"decisions": [{"path": "README.md", "hunkId": "h0", "decision": "accept"}]},
        headers=auth("tok-view-a"),
    )
    assert r.status_code == 403
    assert (
        await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth("tok-view-a"))
    ).status_code == 403
    assert (
        await client.get(f"{P}/change-sets/{cid}", headers=auth("tok-view-a"))
    ).status_code == 200


# ---- review integrity -----------------------------------------------------


async def test_wire_is_camelcase(client):
    sid = await new_session(client)
    cs = (await make_cs(client, sid, [{"path": "README.md", "newContent": "hi\n"}])).json()
    assert "changeSetId" in cs and "sessionId" in cs
    assert "cs_id" not in cs and "session_id" not in cs
    f = cs["files"][0]
    assert "baseSha256" in f and "base_sha256" not in f
    sess = (await client.get(f"{P}/code-sessions/{sid}", headers=auth(ENG))).json()
    assert "sessionId" in sess and "teamId" in sess and "team_id" not in sess


async def test_per_hunk_accept_reject_rejected_hunks_stay_intact(client):
    sid = await new_session(client)
    new = numbered(repl={2: "TWO", 38: "THIRTY-EIGHT"})
    cs = (await make_cs(client, sid, [{"path": "src/app.py", "newContent": new}])).json()
    assert len(cs["files"][0]["hunks"]) == 2
    cid = cs["changeSetId"]
    r = await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={
            "decisions": [
                {"path": "src/app.py", "hunkId": "h0", "decision": "accept"},
                {"path": "src/app.py", "hunkId": "h1", "decision": "reject", "comment": "no"},
            ]
        },
        headers=auth(ENG),
    )
    assert r.status_code == 200
    ap = await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert ap.status_code == 200, ap.text
    got = (await read(client, sid, "src/app.py")).json()["content"]
    assert got == numbered(repl={2: "TWO"})  # h1 rejected -> line 38 untouched
    assert ap.json()["files"][0]["acceptedHunks"] == 1


async def test_pending_hunks_are_never_applied(client):
    sid = await new_session(client)
    new = numbered(repl={2: "TWO", 38: "X"})
    cid = (await make_cs(client, sid, [{"path": "src/app.py", "newContent": new}])).json()[
        "changeSetId"
    ]
    await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={"decisions": [{"path": "src/app.py", "hunkId": "h1", "decision": "accept"}]},
        headers=auth(ENG),
    )
    await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert (await read(client, sid, "src/app.py")).json()["content"] == numbered(repl={38: "X"})


async def test_apply_with_nothing_accepted_is_refused(client):
    sid = await new_session(client)
    cid = (await make_cs(client, sid, [{"path": "README.md", "newContent": "hi\n"}])).json()[
        "changeSetId"
    ]
    r = await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_ACCEPTED_HUNKS"


async def test_stale_base_blocks_apply_and_changes_nothing(client):
    sid = await new_session(client)
    cid = (await make_cs(client, sid, [{"path": "README.md", "newContent": "mine\n"}])).json()[
        "changeSetId"
    ]
    await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={"decisions": [{"path": "README.md", "hunkId": "h0", "decision": "accept"}]},
        headers=auth(ENG),
    )
    await put(client, sid, "README.md", "someone else\n")  # workspace drifts
    cs = (await client.get(f"{P}/change-sets/{cid}", headers=auth(ENG))).json()
    assert cs["files"][0]["drift"] == "stale"
    r = await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_BASE"
    assert (await read(client, sid, "README.md")).json()["content"] == "someone else\n"


async def test_stale_compose_time_base_rejected(client):
    sid = await new_session(client)
    r = await make_cs(
        client, sid, [{"path": "README.md", "newContent": "x\n", "baseSha256": "0" * 64}]
    )
    assert r.status_code == 409


async def test_overlapping_edits_to_same_file_rejected(client):
    sid = await new_session(client)
    a = numbered(repl={5: "A"})
    b = numbered(repl={5: "B"})
    r = await make_cs(
        client,
        sid,
        [{"path": "src/app.py", "newContent": a}, {"path": "src/app.py", "newContent": b}],
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "OVERLAPPING_EDITS"


async def test_disjoint_edits_to_same_file_merge_keeping_every_hunk(client):
    """Regression: merge used accepted={0,1}, silently dropping any third+ hunk."""
    sid = await new_session(client)
    body = "".join(f"line {i}\n" for i in range(1, 41))
    e1 = body.replace("line 2\n", "TWO\n")
    e2 = body.replace("line 20\n", "TWENTY\n")
    e3 = body.replace("line 38\n", "THIRTY-EIGHT\n")
    r = await make_cs(
        client,
        sid,
        [
            {"path": "src/app.py", "newContent": e1},
            {"path": "src/app.py", "newContent": e2},
            {"path": "src/app.py", "newContent": e3},
        ],
    )
    assert r.status_code == 201, r.text
    cs = r.json()
    cid = cs["changeSetId"]
    assert len(cs["files"][0]["hunks"]) == 3
    await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={
            "decisions": [
                {"path": "src/app.py", "hunkId": f"h{i}", "decision": "accept"} for i in range(3)
            ]
        },
        headers=auth(ENG),
    )
    await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    got = (await read(client, sid, "src/app.py")).json()["content"]
    assert "TWO" in got and "TWENTY" in got and "THIRTY-EIGHT" in got


async def test_multi_file_change_set_new_file_and_idempotent_apply(client):
    sid = await new_session(client)
    cs = (
        await make_cs(
            client,
            sid,
            [
                {"path": "README.md", "newContent": "readme v2\n"},
                {"path": "docs/new.md", "newContent": "brand new\n"},
            ],
        )
    ).json()
    cid = cs["changeSetId"]
    await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={
            "decisions": [
                {"path": f["path"], "hunkId": "h0", "decision": "accept"} for f in cs["files"]
            ]
        },
        headers=auth(ENG),
    )
    a1 = await client.post(
        f"{P}/change-sets/{cid}/apply", json={"idempotencyKey": "k1"}, headers=auth(ENG)
    )
    assert a1.status_code == 200
    a2 = await client.post(
        f"{P}/change-sets/{cid}/apply", json={"idempotencyKey": "k1"}, headers=auth(ENG)
    )
    assert a2.status_code == 200 and a2.json() == a1.json()
    a3 = await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert a3.status_code == 409  # double apply without key
    assert (await read(client, sid, "docs/new.md")).json()["content"] == "brand new\n"


async def test_apply_failure_midway_rolls_back_earlier_files(client, monkeypatch):
    sid = await new_session(client)
    cs = (
        await make_cs(
            client,
            sid,
            [
                {"path": "README.md", "newContent": "v2\n"},
                {"path": "zeta.md", "newContent": "z\n"},
            ],
        )
    ).json()
    cid = cs["changeSetId"]
    await client.post(
        f"{P}/change-sets/{cid}/decisions",
        json={
            "decisions": [
                {"path": f["path"], "hunkId": "h0", "decision": "accept"} for f in cs["files"]
            ]
        },
        headers=auth(ENG),
    )
    wm = client.service.workspaces
    real = wm.write_file

    def boom(session_id, path, content):
        if path == "zeta.md":
            raise OSError("disk full")
        return real(session_id, path, content)

    monkeypatch.setattr(wm, "write_file", boom)
    r = await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert r.status_code >= 400
    monkeypatch.setattr(wm, "write_file", real)
    assert (await read(client, sid, "README.md")).json()["content"] == "hello\n"  # rolled back
    cs2 = (await client.get(f"{P}/change-sets/{cid}", headers=auth(ENG))).json()
    assert cs2["status"] == "open"


async def test_cancelled_change_set_cannot_apply(client):
    sid = await new_session(client)
    cid = (await make_cs(client, sid, [{"path": "README.md", "newContent": "x\n"}])).json()[
        "changeSetId"
    ]
    await client.post(f"{P}/change-sets/{cid}/cancel", headers=auth(ENG))
    r = await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    assert r.status_code == 409


async def test_forbidden_path_in_change_set_denied(client):
    sid = await new_session(client, forbiddenPaths=["secrets"])
    r = await make_cs(client, sid, [{"path": "secrets/key.txt", "newContent": "x\n"}])
    assert r.status_code == 403


async def test_decisions_validate_unknown_hunk_and_value(client):
    sid = await new_session(client)
    cid = (await make_cs(client, sid, [{"path": "README.md", "newContent": "x\n"}])).json()[
        "changeSetId"
    ]
    for d in (
        {"path": "README.md", "hunkId": "h9", "decision": "accept"},
        {"path": "nope", "hunkId": "h0", "decision": "accept"},
        {"path": "README.md", "hunkId": "h0", "decision": "maybe"},
    ):
        r = await client.post(
            f"{P}/change-sets/{cid}/decisions", json={"decisions": [d]}, headers=auth(ENG)
        )
        assert r.status_code == 422


async def test_decisions_closed_after_apply(client):
    sid = await new_session(client)
    cid = (await make_cs(client, sid, [{"path": "README.md", "newContent": "x\n"}])).json()[
        "changeSetId"
    ]
    dec = {"decisions": [{"path": "README.md", "hunkId": "h0", "decision": "accept"}]}
    await client.post(f"{P}/change-sets/{cid}/decisions", json=dec, headers=auth(ENG))
    await client.post(f"{P}/change-sets/{cid}/apply", json={}, headers=auth(ENG))
    dec["decisions"][0]["decision"] = "reject"
    r = await client.post(f"{P}/change-sets/{cid}/decisions", json=dec, headers=auth(ENG))
    assert r.status_code == 409


# ---- comments -------------------------------------------------------------


async def test_line_anchored_comments_drift_and_orphan(client):
    sid = await new_session(client)
    r = await client.post(
        f"{P}/code-sessions/{sid}/comments",
        json={"path": "README.md", "startLine": 1, "body": "why?"},
        headers=auth("tok-view-a"),
    )
    assert r.status_code == 201 and r.json()["anchorStatus"] == "anchored"
    await put(client, sid, "README.md", "changed\n")
    lst = (await client.get(f"{P}/code-sessions/{sid}/comments", headers=auth(ENG))).json()["items"]
    assert lst[0]["anchorStatus"] == "drifted"
    bad = await client.post(
        f"{P}/code-sessions/{sid}/comments",
        json={"path": "README.md", "startLine": 5, "endLine": 2, "body": "x"},
        headers=auth(ENG),
    )
    assert bad.status_code == 422


async def test_only_author_or_admin_deletes_comment(client):
    sid = await new_session(client)
    cm = (
        await client.post(
            f"{P}/code-sessions/{sid}/comments",
            json={"path": "README.md", "startLine": 1, "body": "x"},
            headers=auth(ENG),
        )
    ).json()["commentId"]
    assert (
        await client.delete(f"{P}/comments/{cm}", headers=auth("tok-view-a"))
    ).status_code == 403
    assert (await client.delete(f"{P}/comments/{cm}", headers=auth("tok-eng-b"))).status_code == 404
    assert (
        await client.delete(f"{P}/comments/{cm}", headers=auth("tok-admin-a"))
    ).status_code == 200


# ---- sync / verify / merge checks ----------------------------------------


async def test_sync_includes_new_files_in_new_directories(client, upstream):
    """Regression: `git status --porcelain` lists an untracked dir as 'dir/' only."""
    sid = await new_session(client)
    await put(client, sid, "brand/new/deep.py", "x = 1\n")
    r = await client.post(f"{P}/code-sessions/{sid}/sync", headers=auth(ENG))
    assert "brand/new/deep.py" in r.json()["synced"], r.json()
    assert [e["path"] for e in upstream.edits] == ["brand/new/deep.py"]


async def test_sync_blocks_forbidden_and_reports_s5_denial(client, upstream):
    sid = await new_session(client, forbiddenPaths=["secrets"])
    ws = client.service.workspaces.checkout(sid)
    (ws / "secrets" / "key.txt").write_text("tampered\n")  # out-of-band tamper
    await put(client, sid, "ok.txt", "fine\n")
    r = (await client.post(f"{P}/code-sessions/{sid}/sync", headers=auth(ENG))).json()
    assert "secrets/key.txt" in r["blocked"] and "ok.txt" in r["synced"]
    upstream.fail_edit_for = "ok2.txt"
    await put(client, sid, "ok2.txt", "denied by s5\n")
    r2 = (await client.post(f"{P}/code-sessions/{sid}/sync", headers=auth(ENG))).json()
    assert r2["failed"]["code"] == "FORBIDDEN_PATH"


async def test_verify_is_hap1_records_evidence_and_is_idempotent(client, upstream):
    sid = await new_session(client)
    await put(client, sid, "a.py", "print(1)\n")
    body = {
        "schemaVersion": "hap/1",
        "requestId": "r1",
        "arguments": {"command": "pytest -q"},
        "idempotencyKey": "v1",
    }
    r1 = (await client.post(f"{P}/code-sessions/{sid}/verify", json=body, headers=auth(ENG))).json()
    assert r1["status"] == "succeeded" and r1["requestId"] == "r1" and r1["artifacts"] == []
    r2 = (await client.post(f"{P}/code-sessions/{sid}/verify", json=body, headers=auth(ENG))).json()
    assert r2["result"]["evidenceId"] == r1["result"]["evidenceId"]
    assert upstream.execs == ["pytest -q"]
    bad = await client.post(
        f"{P}/code-sessions/{sid}/verify", json={"schemaVersion": "hap/2"}, headers=auth(ENG)
    )
    assert bad.status_code == 422


async def test_merge_checks_are_evidence_only_and_track_staleness(client, upstream):
    sid = await new_session(client)
    mc = (await client.get(f"{P}/code-sessions/{sid}/merge-checks", headers=auth(ENG))).json()
    assert mc["verdict"] == "gaps-found" and mc["mergeAction"] == "not-available-by-design"
    await put(client, sid, "a.py", "print(1)\n")
    await client.post(
        f"{P}/code-sessions/{sid}/verify",
        json={"schemaVersion": "hap/1", "arguments": {"command": "pytest"}},
        headers=auth(ENG),
    )
    mc = (await client.get(f"{P}/code-sessions/{sid}/merge-checks", headers=auth(ENG))).json()
    assert mc["verdict"] == "ready-for-human-review", mc
    await put(client, sid, "a.py", "print(2)\n")  # change after verification
    mc = (await client.get(f"{P}/code-sessions/{sid}/merge-checks", headers=auth(ENG))).json()
    assert mc["verdict"] == "gaps-found"
    upstream.exec_result = {"status": "failed", "summary": "x", "exit_code": 1, "diagnostics": []}
    await client.post(
        f"{P}/code-sessions/{sid}/verify",
        json={"schemaVersion": "hap/1", "arguments": {"command": "pytest"}},
        headers=auth(ENG),
    )
    mc = (await client.get(f"{P}/code-sessions/{sid}/merge-checks", headers=auth(ENG))).json()
    assert mc["verdict"] == "gaps-found"


async def test_no_merge_route_exists(client):
    routes = {r.path for r in client._transport.app.routes}  # type: ignore[attr-defined]
    assert not any("merge" in r and r.endswith("/merge") for r in routes)
    r = await client.post(f"{P}/code-sessions/x/merge", headers=auth(ENG))
    assert r.status_code in (404, 405)


async def test_unsupported_actions_fail_closed_with_integration_proposal(client):
    sid = await new_session(client)
    r = (
        await client.post(
            f"{P}/code-sessions/{sid}/actions",
            json={
                "schemaVersion": "hap/1",
                "operation": "explain",
                "arguments": {"path": "README.md", "startLine": 1},
            },
            headers=auth(ENG),
        )
    ).json()
    assert r["status"] == "unsupported" and r["error"]["code"] == "NEEDS_INTEGRATION"


async def test_action_bad_line_numbers_are_422_not_500(client):
    sid = await new_session(client)
    r = await client.post(
        f"{P}/code-sessions/{sid}/actions",
        json={
            "schemaVersion": "hap/1",
            "operation": "blame",
            "arguments": {"path": "README.md", "startLine": "abc"},
        },
        headers=auth(ENG),
    )
    assert r.status_code in (200, 422) and r.json().get("status", "failed") != "succeeded"


async def test_add_tests_action_creates_reviewable_change_set_not_a_write(client):
    sid = await new_session(client)
    r = (
        await client.post(
            f"{P}/code-sessions/{sid}/actions",
            json={
                "schemaVersion": "hap/1",
                "operation": "add-tests",
                "arguments": {"path": "src/app.py"},
            },
            headers=auth(ENG),
        )
    ).json()
    assert r["status"] == "succeeded"
    assert (await read(client, sid, r["result"]["testPath"])).status_code == 404  # not yet applied


async def test_viewer_cannot_run_add_tests_action(client):
    sid = await new_session(client)
    r = (
        await client.post(
            f"{P}/code-sessions/{sid}/actions",
            json={
                "schemaVersion": "hap/1",
                "operation": "add-tests",
                "arguments": {"path": "src/app.py"},
            },
            headers=auth("tok-view-a"),
        )
    ).json()
    assert r["status"] == "denied"


async def test_fingerprint_changes_with_untracked_content(client):
    sid = await new_session(client)
    wm = client.service.workspaces
    await put(client, sid, "d/x.txt", "1\n")
    f1 = wm.fingerprint(sid)
    await put(client, sid, "d/x.txt", "2\n")
    assert wm.fingerprint(sid) != f1
