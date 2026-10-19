"""Part 44 (lane PL), directives 001 and 002: the task statement and plan versions. Sqlite with the
REAL migration 0047, a fake clock, a principal override. Behaviour: docs/plans.md."""

import asyncio
import hashlib
import json

from harness_orchestrator import plans

from . import test_plans_support as support
from .test_plans_support import CONTENT, GOAL, runp
from .test_plans_support import err as err

env = support.env  # the shared fixture

TASK = {
    "statement": "Make uploads reliable",
    "successCriteria": ["no lost files"],
    "constraints": ["no new deps"],
    "ownerUserId": "eng2",
    "expectedVersion": 0,
}


def race(env, *calls):
    """Run the callables (db) -> coroutine at the same time, each in its own session."""

    async def one(fn):
        async with env.factory() as db:
            try:
                return await fn(db)
            except plans.PlansError as e:
                return e

    async def go():
        return await asyncio.gather(*(one(c) for c in calls))

    return asyncio.run(go())


# ------------------------------------------------------------------ task statement


def test_an_unset_task_reads_as_version_zero(env):
    body = env.as_("lead").get(runp("r1", "/task")).json()
    assert body["exists"] is False and body["version"] == 0 and body["owner"] is None


def test_task_create_update_history_and_audit(env):
    r = env.as_("lead").put(runp("r1", "/task"), json=TASK)
    assert r.status_code == 200, r.text
    v1 = r.json()
    assert v1["version"] == 1 and v1["owner"] == {"userId": "eng2", "name": "Name eng2"}
    assert v1["successCriteria"] == ["no lost files"] and v1["constraints"] == ["no new deps"]
    env.tick(minutes=1)
    r = env.as_("eng2").put(
        runp("r1", "/task"),
        json={**TASK, "statement": "Make uploads reliable and fast", "expectedVersion": 1},
    )
    assert r.json()["version"] == 2 and r.json()["updatedBy"] == "eng2"
    old = env.as_("view").get(runp("r1", "/task"), params={"version": 1}).json()
    assert old["statement"] == "Make uploads reliable" and old["version"] == 1
    err(env.as_("lead").get(runp("r1", "/task"), params={"version": 9}), 404, "VERSION_NOT_FOUND")
    rows = env.audit("task.updated")
    assert len(rows) == 2
    detail = json.loads(rows[1].detail)
    assert detail["version"] == 2 and detail["changed"] == ["statement"]
    assert detail["before"] == {"version": 1} and detail["after"] == {"version": 2}
    assert "uploads" not in rows[0].detail + rows[1].detail  # ids and states only, no text
    assert env.kinds().count("task.updated") == 2


def test_a_stale_expected_version_is_refused_and_nothing_changes(env):
    env.as_("lead").put(runp("r1", "/task"), json=TASK)
    r = env.as_("eng2").put(runp("r1", "/task"), json={**TASK, "statement": "other"})
    assert err(r, 409, "VERSION_CONFLICT")["currentVersion"] == 1
    assert env.as_("lead").get(runp("r1", "/task")).json()["statement"] == TASK["statement"]


def test_saving_the_same_task_again_is_not_a_new_version(env):
    env.as_("lead").put(runp("r1", "/task"), json=TASK)
    r = env.as_("lead").put(runp("r1", "/task"), json={**TASK, "expectedVersion": 1})
    assert r.status_code == 200 and r.json()["version"] == 1
    assert len(env.audit("task.updated")) == 1


def test_the_owner_must_be_a_human_member_of_the_team(env):
    for owner in ("evil", "nobody", "bot"):
        r = env.as_("lead").put(runp("r1", "/task"), json={**TASK, "ownerUserId": owner})
        err(r, 422, "OWNER_NOT_IN_TEAM")
    r = env.as_("lead").put(runp("r1", "/task"), json={**TASK, "ownerUserId": None})
    assert r.json()["owner"] is None


def test_task_bounds_are_enforced(env):
    c = env.as_("lead")
    assert c.put(runp("r1", "/task"), json={**TASK, "statement": ""}).status_code == 422
    assert c.put(runp("r1", "/task"), json={**TASK, "statement": "x" * 4001}).status_code == 422
    assert (
        c.put(runp("r1", "/task"), json={**TASK, "successCriteria": ["c"] * 21}).status_code == 422
    )
    assert c.put(runp("r1", "/task"), json={**TASK, "constraints": ["c" * 501]}).status_code == 422
    assert c.put(runp("r1", "/task"), json={**TASK, "surprise": 1}).status_code == 422
    assert c.put(runp("r1", "/task"), json={**TASK, "expectedVersion": -1}).status_code == 422


def test_two_simultaneous_first_saves_make_exactly_one_version(env):
    p1, p2 = env.principal("lead"), env.principal("eng2")
    body = plans.TaskBody(**TASK)
    out = race(
        env,
        lambda db: plans.put_task(db, p1, "r1", body),
        lambda db: plans.put_task(db, p2, "r1", body),
    )
    errors = [o for o in out if isinstance(o, plans.PlansError)]
    assert len(errors) == 1 and errors[0].code == "VERSION_CONFLICT"
    assert env.q("SELECT version FROM task_specs")[0].version == 1
    assert len(env.q("SELECT 1 FROM task_spec_revisions")) == 1


def test_two_simultaneous_updates_of_one_version_make_exactly_one_new_version(env):
    env.as_("lead").put(runp("r1", "/task"), json=TASK)
    p1, p2 = env.principal("lead"), env.principal("eng2")
    mk = lambda s: plans.TaskBody(**{**TASK, "statement": s, "expectedVersion": 1})  # noqa: E731
    out = race(
        env,
        lambda db: plans.put_task(db, p1, "r1", mk("one")),
        lambda db: plans.put_task(db, p2, "r1", mk("two")),
    )
    errors = [o for o in out if isinstance(o, plans.PlansError)]
    assert len(errors) == 1 and errors[0].code == "VERSION_CONFLICT"
    assert env.q("SELECT version FROM task_specs")[0].version == 2
    assert len(env.q("SELECT 1 FROM task_spec_revisions")) == 2


def test_task_roles(env):
    assert env.as_("view").get(runp("r1", "/task")).status_code == 200
    for user in ("view", "guest"):
        err(env.as_(user).put(runp("r1", "/task"), json=TASK), 403, "ROLE_DENIED")
    assert len(env.audit("authz.denied")) == 2
    assert env.q("SELECT 1 FROM task_specs") == []


# ------------------------------------------------------- isolation, no oracle


def test_foreign_and_unknown_runs_answer_the_same_404_everywhere(env):
    v = {"content": CONTENT, "expectedVersion": 0}
    calls = [
        ("get", runp("{}", "/task"), None),
        ("put", runp("{}", "/task"), TASK),
        ("get", runp("{}", "/plan/versions"), None),
        ("post", runp("{}", "/plan/versions"), v),
        ("get", runp("{}", "/plan/versions/1"), None),
        ("get", runp("{}", "/plan/versions/1/diff"), None),
        ("get", runp("{}", "/plan/versions/1/comments"), None),
        ("post", runp("{}", "/plan/versions/1/comments"), {"body": "x"}),
        ("get", runp("{}", "/plan/versions/1/edits"), None),
        (
            "post",
            runp("{}", "/plan/versions/1/edits"),
            {"patch": [{"op": "replace", "path": "/goal", "value": "y"}]},
        ),
        ("post", runp("{}", "/plan/versions/1/edits/e/decide"), {"decision": "accept"}),
    ]
    for method, url, body in calls:
        seen = []
        for run in ("r3", "nope"):  # r3 belongs to team-b
            kw = {"json": body} if body else {}
            r = getattr(env.as_("lead"), method)(url.format(run), **kw)
            assert r.status_code == 404, (method, url, r.text)
            seen.append(r.json())
        assert seen[0] == seen[1], (method, url)
    assert env.q("SELECT 1 FROM plan_versions") == [] and env.q("SELECT 1 FROM task_specs") == []


def test_the_other_teams_data_never_shows_through_a_run_it_cannot_reach(env):
    env.as_("evil").put(runp("r3", "/task"), json={**TASK, "ownerUserId": "evil"})
    env.new_version("evil", "r3")
    assert env.as_("lead").get(runp("r1", "/task")).json()["exists"] is False
    assert env.versions("lead", "r1")["versions"] == []
    err(env.as_("lead").get(runp("r1", "/plan/versions/1")), 404, "VERSION_NOT_FOUND")


# --------------------------------------------------------------- plan versions


def test_a_run_with_no_plan_lists_nothing(env):
    out = env.versions()
    assert out["versions"] == [] and out["latestVersion"] == 0 and out["locked"] is False


def test_versions_are_numbered_digested_and_the_old_ones_are_immutable(env):
    v1 = env.new_version(expected=0)
    assert v1["version"] == 1 and v1["state"] == "proposed" and v1["source"] == "native"
    canon = json.dumps(CONTENT, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert v1["contentSha256"] == hashlib.sha256(canon.encode()).hexdigest()
    assert v1["content"] == CONTENT and v1["author"] == {"kind": "user", "id": "lead"}
    env.tick(minutes=1)
    changed = {**CONTENT, "goal": "A different goal", "steps": ["only step"]}
    v2 = env.new_version("eng2", expected=1, content=changed)
    assert v2["version"] == 2 and v2["parentVersion"] == 1 and v2["state"] == "proposed"
    assert v2["contentSha256"] != v1["contentSha256"]
    again = env.as_("view").get(runp("r1", "/plan/versions/1")).json()
    assert again["content"] == CONTENT and again["contentSha256"] == v1["contentSha256"]
    assert again["state"] == "superseded"
    stored = env.q("SELECT content, content_sha256 FROM plan_versions WHERE version = 1")[0]
    assert hashlib.sha256(stored.content.encode()).hexdigest() == stored.content_sha256
    listing = env.versions()
    assert [v["version"] for v in listing["versions"]] == [1, 2]
    assert "content" not in listing["versions"][0] and listing["latestVersion"] == 2
    assert env.kinds().count("plan.version_created") == 2
    audit = env.audit("plan.version_created")
    assert len(audit) == 2 and GOAL not in audit[0].detail and "only step" not in audit[1].detail


def test_the_same_content_always_has_the_same_digest(env):
    a = env.new_version(expected=0)
    b = env.new_version(expected=1, content=dict(reversed(list(CONTENT.items()))))
    assert a["contentSha256"] == b["contentSha256"]


def test_a_stale_expected_version_cannot_create_a_version(env):
    env.new_version(expected=0)
    r = env.as_("eng2").post(
        runp("r1", "/plan/versions"), json={"content": CONTENT, "expectedVersion": 0}
    )
    assert err(r, 409, "VERSION_CONFLICT")["currentVersion"] == 1
    assert len(env.versions()["versions"]) == 1


def test_a_retried_create_with_the_same_request_id_is_one_version(env):
    a = env.new_version(expected=0, requestId="req-1")
    r = env.as_("lead").post(
        runp("r1", "/plan/versions"),
        json={"content": CONTENT, "expectedVersion": 0, "requestId": "req-1"},
    )
    assert r.status_code == 200 and r.json()["id"] == a["id"]
    assert len(env.versions()["versions"]) == 1 and len(env.audit("plan.version_created")) == 1


def test_two_simultaneous_creates_make_exactly_one_next_version(env):
    p1, p2 = env.principal("lead"), env.principal("eng2")
    body = plans.VersionBody(content=plans.PlanContent(**CONTENT), expectedVersion=0)
    out = race(
        env,
        lambda db: plans.create_version(db, p1, "r1", body),
        lambda db: plans.create_version(db, p2, "r1", body),
    )
    errors = [o for o in out if isinstance(o, plans.PlansError)]
    assert len(errors) == 1 and errors[0].code == "VERSION_CONFLICT"
    assert len(env.q("SELECT 1 FROM plan_versions")) == 1


def test_plan_content_is_validated_and_bounded(env):
    def post(**over):
        content = {**CONTENT, **over}
        return env.as_("lead").post(
            runp("r1", "/plan/versions"), json={"content": content, "expectedVersion": 0}
        )

    assert post(goal="").status_code == 422
    assert post(surprise=1).status_code == 422
    assert post(steps=["s"] * 101).status_code == 422
    assert post(steps=["x" * 2001]).status_code == 422
    assert post(estimatedCostUsd=-1).status_code == 422
    for bad in ("/etc/passwd", "../x", "a/../b", "a\\b", ""):
        assert post(filesLikelyToChange=[bad]).status_code == 422, bad
        assert post(forbiddenPaths=[bad]).status_code == 422, bad
    big = ["y" * 1900] * 50 + ["z" * 900] * 50  # each item fits, the whole plan does not
    err(
        post(steps=big[:100], tests=["t" * 900] * 50, risks=["r" * 900] * 50), 422, "PLAN_TOO_LARGE"
    )
    assert env.q("SELECT 1 FROM plan_versions") == []


def test_a_run_keeps_a_bounded_number_of_versions(env, monkeypatch):
    monkeypatch.setattr(plans, "MAX_VERSIONS", 2)
    env.new_version(expected=0)
    env.new_version(expected=1, content={**CONTENT, "goal": "g2"})
    r = env.as_("lead").post(
        runp("r1", "/plan/versions"),
        json={"content": {**CONTENT, "goal": "g3"}, "expectedVersion": 2},
    )
    err(r, 409, "TOO_MANY_VERSIONS")


def test_version_roles(env):
    assert env.as_("view").get(runp("r1", "/plan/versions")).status_code == 200
    for user in ("view", "guest"):
        r = env.as_(user).post(
            runp("r1", "/plan/versions"), json={"content": CONTENT, "expectedVersion": 0}
        )
        err(r, 403, "ROLE_DENIED")
    assert env.q("SELECT 1 FROM plan_versions") == []


def test_unknown_version_is_a_404(env):
    env.new_version(expected=0)
    err(env.as_("lead").get(runp("r1", "/plan/versions/7")), 404, "VERSION_NOT_FOUND")
    err(env.as_("lead").get(runp("r1", "/plan/versions/7/diff")), 404, "VERSION_NOT_FOUND")
    err(
        env.as_("lead").get(runp("r1", "/plan/versions/1/diff"), params={"against": 5}),
        404,
        "VERSION_NOT_FOUND",
    )


# ------------------------------------------------------------- the legacy plan


def test_the_legacy_plan_appears_as_version_one_exactly_once(env):
    env.legacy()
    for _ in range(2):
        out = env.versions()
        assert [v["version"] for v in out["versions"]] == [1]
    v1 = env.as_("view").get(runp("r1", "/plan/versions/1")).json()
    assert v1["source"] == "legacy" and v1["legacyPlanId"] == "lp1" and v1["state"] == "proposed"
    assert v1["author"] == {"kind": "agent", "id": "planner"}
    assert v1["content"] == {
        "goal": "Legacy goal",
        "steps": ["a", "b"],
        "tests": ["t"],
        "risks": [],
        "filesLikelyToChange": ["x.py"],
        "forbiddenPaths": [],
        "expectedResult": "done",
        "estimatedCostUsd": 0.5,
    }
    legacy = env.q("SELECT status, version, goal FROM agent_plans")
    assert len(legacy) == 1 and legacy[0].status == "proposed" and legacy[0].version == 0


def test_two_simultaneous_first_reads_mirror_the_legacy_plan_once(env):
    env.legacy()
    p = env.principal("lead")
    race(
        env,
        lambda db: plans.list_versions(db, p, "r1"),
        lambda db: plans.list_versions(db, p, "r1"),
    )
    assert len(env.q("SELECT 1 FROM plan_versions")) == 1


def test_a_new_version_builds_on_the_mirrored_one(env):
    env.legacy()
    err(
        env.as_("lead").post(
            runp("r1", "/plan/versions"), json={"content": CONTENT, "expectedVersion": 0}
        ),
        409,
        "VERSION_CONFLICT",
    )
    v2 = env.new_version(expected=1)
    assert v2["version"] == 2 and v2["parentVersion"] == 1
    assert env.as_("lead").get(runp("r1", "/plan/versions/1")).json()["state"] == "superseded"


def test_a_legacy_approval_locks_the_plan_and_records_the_digest(env):
    env.legacy()
    env.new_version(expected=1)  # v2 proposed
    env.set_legacy("approved")
    out = env.versions()
    assert out["locked"] is True and out["approvedVersion"] == 1
    v1 = env.as_("lead").get(runp("r1", "/plan/versions/1")).json()
    assert v1["state"] == "approved" and v1["approvedSha256"] == v1["contentSha256"]
    assert v1["approvedAt"] is not None
    assert env.as_("lead").get(runp("r1", "/plan/versions/2")).json()["state"] == "superseded"
    r = env.as_("lead").post(
        runp("r1", "/plan/versions"), json={"content": CONTENT, "expectedVersion": 2}
    )
    assert err(r, 409, "PLAN_LOCKED")["approvedVersion"] == 1
    assert len(env.versions()["versions"]) == 2


def test_a_legacy_rejection_does_not_lock_and_a_flip_back_unlocks(env):
    env.legacy()
    env.set_legacy("approved")
    assert env.versions()["locked"] is True
    env.set_legacy("rejected")  # the legacy reject route flips an approved plan back
    out = env.versions()
    assert out["locked"] is False and out["versions"][0]["state"] == "rejected"
    assert out["versions"][0]["approvedSha256"] is None
    assert env.new_version(expected=1)["version"] == 2


def test_a_later_legacy_plan_becomes_the_next_version(env):
    env.legacy()
    env.versions()
    env.tick(minutes=5)
    env.legacy("lp2", goal="Second legacy")
    out = env.versions()
    assert [v["version"] for v in out["versions"]] == [1, 2]
    assert [v["state"] for v in out["versions"]] == ["superseded", "proposed"]


# ------------------------------------------------------------------------ diff


def test_diff_shows_scalar_and_list_changes_between_versions(env):
    env.new_version(expected=0)
    changed = {
        **CONTENT,
        "goal": "Retry with backoff",
        "steps": [
            "Read uploader.py",
            "Add retry loop with backoff",
            "Run the tests",
            "Update docs",
        ],
        "estimatedCostUsd": 0.5,
    }
    env.new_version(expected=1, content=changed)
    d = env.as_("view").get(runp("r1", "/plan/versions/2/diff")).json()  # default: previous
    assert d["from"]["version"] == 1 and d["to"]["version"] == 2 and d["identical"] is False
    assert d["changedFields"] == ["goal", "steps", "estimatedCostUsd"]
    by = {f["field"]: f for f in d["fields"]}
    assert by["goal"] == {
        "field": "goal",
        "kind": "scalar",
        "changed": True,
        "before": GOAL,
        "after": "Retry with backoff",
    }
    ops = {o["op"]: o for o in by["steps"]["ops"]}
    assert ops["replace"]["before"] == [{"index": 1, "value": "Add retry loop"}]
    assert ops["replace"]["after"] == [{"index": 1, "value": "Add retry loop with backoff"}]
    assert ops["insert"]["after"] == [{"index": 3, "value": "Update docs"}]
    assert by["tests"]["changed"] is False and by["tests"]["ops"] == []
    back = env.as_("view").get(runp("r1", "/plan/versions/1/diff"), params={"against": 2}).json()
    assert back["from"]["version"] == 2 and "delete" in {o["op"] for o in back["fields"][1]["ops"]}


def test_diff_of_version_one_needs_an_explicit_base_and_identical_plans_say_so(env):
    env.new_version(expected=0)
    env.new_version(expected=1, content=CONTENT)  # same content, new version
    err(env.as_("lead").get(runp("r1", "/plan/versions/1/diff")), 422, "NO_BASE")
    same = env.as_("lead").get(runp("r1", "/plan/versions/2/diff")).json()
    assert same["identical"] is True and same["changedFields"] == []
