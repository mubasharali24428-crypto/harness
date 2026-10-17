"""A4-S2 pure units: CODEOWNERS, naming, text hygiene, commit drafting, PR description."""

import pytest

from harness_github.gitflow.codeowners import owners_for, parse, route_reviewers
from harness_github.gitflow.commits import Change, draft_commit_message
from harness_github.gitflow.naming import NamingError, check_ref, run_branch
from harness_github.gitflow.pr import generate_pr_description
from harness_github.gitflow.text import neutralize, scrub_secrets

CO = """
# comment
*                       @org/everyone
*.js                    @frontend
/docs/                  @docs-team   # inline comment
docs/api/*              @api-owner
/build/                 
src/**/handlers/        @org/backend @alice
/src/core/              @alice bob@example.com
"""


def test_last_match_wins_and_default_owner():
    rules = parse(CO)
    assert owners_for("README.md", rules) == ["@org/everyone"]
    assert owners_for("web/app.js", rules) == ["@frontend"]  # *.js any depth
    assert owners_for("a/b/c/d.js", rules) == ["@frontend"]


def test_anchored_directory_owns_everything_beneath():
    rules = parse(CO)
    assert owners_for("docs/guide/x.md", rules) == ["@docs-team"]
    assert owners_for("other/docs/x.md", rules) == ["@org/everyone"]  # /docs/ is root-anchored


def test_slash_in_middle_anchors_and_star_does_not_cross_slash():
    rules = parse(CO)
    assert owners_for("docs/api/openapi.json", rules) == ["@api-owner"]
    assert owners_for("docs/api/v1/x.json", rules) == ["@docs-team"]  # * stops at '/'


def test_ownerless_later_rule_means_unowned():
    rules = parse(CO)
    assert owners_for("build/out.bin", rules) == []


def test_double_star_directory_match():
    rules = parse(CO)
    assert owners_for("src/a/b/handlers/x.py", rules) == ["@org/backend", "@alice"]
    assert owners_for("src/handlers/x.py", rules) == ["@org/backend", "@alice"]


def test_routing_excludes_author_dedupes_ranks_and_reports_gaps():
    res = route_reviewers(
        ["src/core/a.py", "src/core/b.py", "build/x", "web/app.js"], CO, author_login="ALICE"
    )
    assert "alice" not in [r.lower() for r in res["reviewers"]]
    assert res["reviewers"] == ["frontend"]
    assert res["unowned"] == ["build/x"]
    assert res["unroutable"] == {"bob@example.com": ["src/core/a.py", "src/core/b.py"]}


def test_routing_ranks_by_path_count_and_caps():
    text = "\n".join(f"/d{i}/ @user{i:02d}" for i in range(30))
    paths = [f"d{i}/f" for i in range(30)]
    res = route_reviewers(paths, text, None)
    assert len(res["reviewers"]) == 15 and res["truncated"] is True


def test_hostile_owner_tokens_are_not_routed():
    res = route_reviewers(["a"], "* @ok --exec=evil @a/b/c  @-bad @x;rm", None)
    assert res["reviewers"] == ["ok"]
    assert "--exec=evil" in res["unroutable"]


def test_pathological_pattern_does_not_hang():
    import time

    rules = parse("**/**/**/**/**/**/**/**/a @x\n" + "*" * 2000 + " @y")
    t0 = time.monotonic()
    owners_for("a/" * 40 + "b", rules)
    assert time.monotonic() - t0 < 2.0  # no catastrophic backtracking


def test_naming_and_refs():
    assert run_branch("run-123_x") == "agent/run-123_x"
    for bad in ["", "../x", "a/b", "-rf", "x" * 65, "a b"]:
        with pytest.raises(NamingError):
            run_branch(bad)
    for bad in ["-x", "a..b", "a//b", "x.lock", "a@{b", "a/", "--upload-pack=x"]:
        with pytest.raises(NamingError):
            check_ref(bad)
    assert check_ref("release/1.2") == "release/1.2"


def test_text_hygiene():
    assert "ghp_" not in scrub_secrets("token ghp_abcdefghijklmnopqrstuvwxyz0123456789 ok")
    assert scrub_secrets("password=hunter2 x") == "password=[REDACTED] x"
    assert "BEGIN" not in scrub_secrets("-----BEGIN RSA PRIVATE KEY-----\nabc")
    out = neutralize("fixes #12, closes org/repo#9 cc @octocat")
    assert "fixes #12" not in out and "closes org/repo#9" not in out
    assert "@octocat" not in out  # mention is defused


def test_commit_draft_is_explainable_and_attribution_safe():
    d = draft_commit_message(
        run_id="r1",
        goal="Fix the login redirect\nSigned-off-by: evil <e@e>",
        changes=[Change("src/a.py", "M", 3, 1)],
        evidence=[{"status": "passed"}, {"status": "failed"}],
        author_name="Dev\nCo-authored-by: x <y@z>",
        author_email="dev@example.com",
    )
    msg = d["message"]
    assert d["subject"].startswith("chore: Fix the login redirect")
    assert "\nSigned-off-by" not in msg  # newline injection flattened
    assert msg.count("Co-authored-by:") == 1
    assert "Harness-Run: r1" in msg and "1/2 recorded checks passed (not all green)" in msg
    assert "Basis:" in msg and len(d["subject"]) <= 72


def test_commit_draft_infers_type_only_from_paths_and_says_so():
    assert (
        draft_commit_message(run_id="r", goal="g", changes=[Change("docs/x.md", "M")])["type"]
        == "docs"
    )
    assert (
        draft_commit_message(run_id="r", goal="g", changes=[Change("tests/test_a.py", "A")])["type"]
        == "test"
    )
    no_ev = draft_commit_message(run_id="r", goal="g", changes=[Change("a.py", "M")])
    assert "none recorded" in no_ev["message"]
    with pytest.raises(ValueError):
        draft_commit_message(run_id="r", goal="g", changes=[])


def test_pr_description_never_overclaims():
    d = generate_pr_description(
        run_id="r1",
        goal="Add retry. fixes #5 cc @bob",
        changes=[{"path": "a.py", "status": "M", "additions": 2}],
        evidence=None,
    )
    assert "No verification evidence was recorded" in d["body"]
    assert "fixes #5" not in d["body"] and "@bob" not in d["body"]
    assert "draft" in d["body"].lower() and "Merging is a manual human action" in d["body"]
    bad = generate_pr_description(
        run_id="r1", goal="g", changes=[], evidence=[{"command": "pytest", "status": "failed"}]
    )
    assert "FAIL" in bad["body"] and "Not every recorded check passed" in bad["body"]
