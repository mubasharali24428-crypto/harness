"""W18 slice 2 — migrate a team's existing agent config without leaking it.

The first question on an evaluation call is "what happens to our CLAUDE.md /
AGENTS.md / Cursor rules?". The importer answers it by writing the team's own
instructions into the project's single rules document through the public API. So
the laws that matter are: nothing is sent without `--apply`, nothing secret-shaped
is ever forwarded, a re-run updates instead of duplicating, and an oversized
corpus is truncated at a boundary and SAID OUT LOUD rather than silently cut.

The tool lives in `scripts/migrate_agent_config.py` (it is a migration utility,
not a server surface); these tests load it the same way the PG suite loads
`deploy/runners/migrate.py`.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate_agent_config.py"

_spec = importlib.util.spec_from_file_location("migrate_agent_config", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # dataclasses resolve annotations via __module__
_spec.loader.exec_module(mod)

MARK_BEGIN = "<!-- harness:migrated-agent-rules -->"
MARK_END = "<!-- /harness:migrated-agent-rules -->"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    _write(tmp_path / "CLAUDE.md", "# Claude\n\nAlways run pytest before proposing a merge.\n")
    _write(tmp_path / "AGENTS.md", "Use tabs for Go, spaces for Python.\n")
    _write(
        tmp_path / ".cursor/rules/style.mdc",
        "---\ndescription: style\n---\nPrefer small diffs.\n",
    )
    return tmp_path


def test_every_known_source_is_found_in_a_stable_order(repo):
    sources = mod.collect_sources(repo)
    assert [s.label for s in sources] == ["AGENTS.md", "CLAUDE.md", ".cursor/rules/style.mdc"]
    assert "Prefer small diffs." in sources[-1].text


def test_a_repo_with_nothing_to_import_is_not_an_error(tmp_path):
    assert mod.collect_sources(tmp_path) == []


def test_secret_shaped_lines_are_dropped_and_counted(tmp_path):
    _write(
        tmp_path / "CLAUDE.md",
        "\n".join(
            [
                "Run the tests with make test-section1.",
                "OPENAI_API_KEY=sk-live-abcdefghijklmnop",
                "export GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz",
                "token: AKIAIOSFODNN7EXAMPLE",
                "-----BEGIN PRIVATE KEY-----",
                "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
                "-----END PRIVATE KEY-----",
                "Never merge without approval.",
            ]
        ),
    )
    text, dropped = mod.scrub((tmp_path / "CLAUDE.md").read_text())
    assert "sk-live" not in text and "ghp_" not in text and "AKIA" not in text
    assert "PRIVATE KEY" not in text and "MIIEvQ" not in text
    assert "Run the tests with make test-section1." in text
    assert "Never merge without approval." in text
    assert dropped >= 5


def test_the_merged_document_carries_provenance_for_every_source(repo):
    doc = mod.build_document(mod.collect_sources(repo), existing="")
    assert doc.text.startswith(MARK_BEGIN) and doc.text.rstrip().endswith(MARK_END)
    for heading in ("## From AGENTS.md", "## From CLAUDE.md", "## From .cursor/rules/style.mdc"):
        assert heading in doc.text
    assert doc.replaced is False and doc.truncated_at is None


def test_a_second_run_replaces_its_own_block_instead_of_duplicating(repo):
    first = mod.build_document(mod.collect_sources(repo), existing="")
    # The team's own rules are outside the block and must survive untouched.
    existing = first.text + "\n\n## Team house rules\nMerge on Fridays only with an approver.\n"
    second = mod.build_document(mod.collect_sources(repo), existing=existing)
    assert second.text.count(MARK_BEGIN) == 1 and second.text.count(MARK_END) == 1
    assert second.replaced is True
    assert "Merge on Fridays only with an approver." in second.text
    assert second.text.count("Always run pytest before proposing a merge.") == 1


def test_a_huge_corpus_is_cut_at_a_section_boundary_and_reported(tmp_path):
    _write(tmp_path / "AGENTS.md", "A" * 40_000 + "\n")
    _write(tmp_path / "CLAUDE.md", "B" * 40_000 + "\n")
    doc = mod.build_document(mod.collect_sources(tmp_path), existing="")
    assert len(doc.text.encode()) <= mod.MAX_RULES_BYTES
    assert doc.truncated_at == "CLAUDE.md", "the section that did not fit is named"
    assert "B" * 100 not in doc.text, "a section is kept whole or not at all"
    assert "A" * 100 in doc.text


def test_one_oversized_source_is_refused_rather_than_silently_cut(tmp_path):
    _write(tmp_path / "AGENTS.md", "A" * (mod.MAX_RULES_BYTES + 1))
    with pytest.raises(mod.ImportTooLarge):
        mod.build_document(mod.collect_sources(tmp_path), existing="")


def test_dry_run_sends_nothing_and_shows_the_exact_put(repo, capsys):
    sent = []

    def never(url, method, token, body):  # pragma: no cover - must not run
        sent.append((url, method, body))
        raise AssertionError("dry-run performed a request")

    code = mod.main(
        ["--root", str(repo), "--project-id", "p1", "--base-url", "http://x"],
        http=never,
    )
    assert code == 0
    assert sent == []
    out = capsys.readouterr().out
    plan = json.loads(out[out.index("{"):])
    assert plan["action"] == "create"
    assert plan["sources"] == ["AGENTS.md", "CLAUDE.md", ".cursor/rules/style.mdc"]
    assert plan["method"] == "PUT" and plan["path"].endswith("/projects/p1/rules")


def test_apply_reads_then_writes_the_merged_document(repo, capsys):
    calls = []

    def fake_http(url, method, token, body=None):
        calls.append((method, url, token, body))
        if method == "GET":
            return 200, {"rulesText": "## Existing\nKeep me.\n"}
        return 200, {"rulesText": body["rulesText"]}

    code = mod.main(
        [
            "--root", str(repo),
            "--project-id", "p1",
            "--base-url", "http://x",
            "--apply",
            "--token-env", "HARNESS_MIGRATE_TOKEN",
        ],
        http=fake_http,
        environ={"HARNESS_MIGRATE_TOKEN": "tok"},
    )
    assert code == 0
    assert [(m, u.split("/api/v1")[1]) for m, u, _, _ in calls] == [
        ("GET", "/projects/p1/rules"),
        ("PUT", "/projects/p1/rules"),
    ]
    assert MARK_BEGIN in calls[1][3]["rulesText"]
    assert "Keep me." in calls[1][3]["rulesText"]
    assert all(token == "tok" for _, _, token, _ in calls)


def test_apply_without_a_token_refuses_before_any_request(repo):
    def never(url, method, token, body):  # pragma: no cover - must not run
        raise AssertionError("a request went out without a token")

    code = mod.main(
        ["--root", str(repo), "--project-id", "p1", "--apply", "--token-env", "NOPE"],
        http=never,
        environ={},
    )
    assert code == 2


def test_nothing_to_import_is_a_clean_no_op(tmp_path, capsys):
    code = mod.main(["--root", str(tmp_path), "--project-id", "p1"], http=None)
    assert code == 0
    assert "nothing to import" in capsys.readouterr().out
