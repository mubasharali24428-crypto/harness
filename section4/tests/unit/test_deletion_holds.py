"""Part 31/58: an authored-content deletion leaves content under a run retention hold untouched.

Same sqlite env and real migrations as test_exports_deletion.py; the hold is a row in
run_retention_holds (migration 0056). Behaviour: docs/search-and-export.md."""

from pathlib import Path

from harness_orchestrator import exports

from .test_exports_deletion import approve, env, request_deletion, seed_author  # noqa: F401
from .test_search_support import P


def hold(e, run="r1"):
    e.x(
        "INSERT INTO run_retention_holds (run_id, held_by, reason, created_at)"
        " VALUES (:r, 'boss', 'case 42', :t)",
        r=run, t=e.plus(),
    )


def test_held_run_discussion_reactions_and_exports_survive_while_the_rest_is_deleted(env):  # noqa: F811
    mine, kept, other = seed_author(env)  # eng2: r1 plain (held), r2 sensitive (not held)
    in_session = env.post("eng2", "session", "s1", "eng2 note on the session")["id"]
    held_export = env.export("eng2", kind="run_history", scopeId="r1")
    free_export = env.export("eng2", kind="run_history", scopeId="r2")
    hold(env)  # r1 is linked to session s1, so s1's discussion is held too
    out = approve(env, request_deletion(env, "eng2")["id"])
    counts = out["result"]["deleted"]
    assert counts["commentsHeld"] == 2 and counts["commentsTombstoned"] == 1
    assert counts["exportFiles"] == 1 and counts["exportFilesHeld"] == 1
    assert counts["reactions"] == 0 and counts["mentions"] == 0  # both sit on held r1 comments
    states = {r.id: r for r in env.q("SELECT id, state, body, author_name_snapshot FROM comments")}
    for cid in (mine, in_session):  # untouched: text, state and the display name
        assert states[cid].state == "visible" and states[cid].body != ""
        assert states[cid].author_name_snapshot != "deleted user"
    assert states[kept].state == "deleted" and states[kept].body == ""  # r2 is not held
    assert env.q("SELECT 1 FROM comment_reactions WHERE user_id = 'eng2'") != []
    assert env.q("SELECT 1 FROM comment_mentions WHERE mentioned_user_id = 'eng2'") != []
    store = Path(exports.store_dir())
    assert [p.name for p in store.iterdir()] == [f"{held_export['id']}.json"]
    assert env.as_("boss").get(f"{P}/exports/{held_export['id']}").json()["state"] == "completed"
    assert env.as_("boss").get(f"{P}/exports/{free_export['id']}").json()["state"] == "deleted"
    assert "content under a run retention hold" in {r["store"] for r in out["result"]["retained"]}


def test_releasing_the_hold_lets_a_new_request_delete_what_was_held(env):  # noqa: F811
    mine, _, _ = seed_author(env)
    hold(env)
    approve(env, request_deletion(env, "eng2")["id"])
    env.x("DELETE FROM run_retention_holds WHERE run_id = 'r1'")
    out = approve(env, request_deletion(env, "eng2")["id"])
    assert out["result"]["deleted"]["commentsHeld"] == 0
    assert env.q("SELECT state, body FROM comments WHERE id = :i", i=mine)[0].state == "deleted"
