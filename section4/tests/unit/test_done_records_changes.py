"""The done signal records the run's changes at once, not on the first read of the changes list.

Before this, a run whose review passed could sit with no recorded changes until someone read the
list, so per-file review had nothing to act on (the tool-calling run of 2026-11-01)."""

import asyncio

from harness_orchestrator import artifacts

from . import test_artifacts_support as support

env = support.env  # the shared fixture

NEW_FILES = (
    "diff --git a/wordstats/__init__.py b/wordstats/__init__.py\n"
    "new file mode 100644\n"
    "index 0000000..e69de29\n"
    "diff --git a/wordstats/stats.py b/wordstats/stats.py\n"
    "new file mode 100644\n"
    "index 0000000..7d4290a\n"
    "--- /dev/null\n"
    "+++ b/wordstats/stats.py\n"
    "@@ -0,0 +1 @@\n"
    "+x = 1\n"
)


def test_done_signal_records_changes_before_anyone_reads_the_list(env):
    env.set_diff("r1", NEW_FILES)
    assert env.q("SELECT path FROM run_changes WHERE run_id = 'r1'") == []

    asyncio.run(artifacts.record_done_changes("r1", env.principal("lead")))

    rows = env.q(
        "SELECT path, change_kind, review_state FROM run_changes WHERE run_id = 'r1' ORDER BY path"
    )
    assert [(r.path, r.change_kind, r.review_state) for r in rows] == [
        ("wordstats/__init__.py", "added", "pending"),
        ("wordstats/stats.py", "added", "pending"),
    ]


def test_recording_at_done_is_idempotent_with_the_list_read(env):
    env.set_diff("r1", NEW_FILES)
    asyncio.run(artifacts.record_done_changes("r1", env.principal("lead")))

    listed = env.changes("lead", "r1")["items"]
    assert sorted(item["path"] for item in listed) == [
        "wordstats/__init__.py",
        "wordstats/stats.py",
    ]
    assert env.q("SELECT COUNT(*) AS n FROM run_changes WHERE run_id = 'r1'")[0].n == 2


def test_done_signal_without_a_diff_records_nothing(env):
    asyncio.run(artifacts.record_done_changes("r1", env.principal("lead")))
    assert env.q("SELECT path FROM run_changes WHERE run_id = 'r1'") == []
