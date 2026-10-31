"""The CLI side of M8: `--modules`, and the digest that pins more than one file.

A library API can be handed bytes; a person at a terminal has a directory. Both
commands therefore need to say where `use notes` is read from, and both need the
same answer by default — otherwise "`hxc` green, `hxr` refused" is a puzzle with no
cause in the program. `--modules none` is kept as the reviewer's probe: it answers
"what is this program without its helpers?".

The second half is the point. Since M8 a program is a *set* of files, so `--digest`
alone pins one of them and a helper can be swapped underneath it. `--program-digest`
pins the whole set the manifest was built from, and the last two tests are what
distinguish the two.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_host_modules import MAIN, NOTES

from harness_hx.cli import main as hxc
from harness_hx_host.cli import main as hxr
from harness_hx_host.run import digest_of

ALL = "repo.read"


def project(tmp_path, module_text=NOTES, entry=MAIN):
    """A two-file program on disk, plus the README its workflow reads."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "main.hx").write_text(entry, encoding="utf-8")
    if module_text is not None:
        (tmp_path / "notes.hx").write_text(module_text, encoding="utf-8")
    return tmp_path / "main.hx"


def run_args(entry, *extra):
    return [
        "run",
        str(entry),
        "--grant",
        ALL,
        "--root",
        str(entry.parent),
        "--inputs",
        "{}",
        "--json",
        *extra,
    ]


# ------------------------------------------------------------------- hxc


def test_hxc_resolves_a_neighbour_by_default(tmp_path, capsys):
    entry = project(tmp_path)
    assert hxc(["check", str(entry)]) == 0, capsys.readouterr().err


def test_hxc_with_no_module_source_blames_the_file_that_asked(tmp_path, capsys):
    project(tmp_path, module_text=None)
    entry = tmp_path / "main.hx"
    assert hxc(["check", "--json", "--modules", "none", str(entry)]) == 1
    records = json.loads(capsys.readouterr().err)
    assert [record["message"][1:7] for record in records] == [
        "HX3015",
        "HX3021",
        "HX3021",
        "HX3021",
        "HX3021",
    ]
    assert all(record["path"] == str(entry) for record in records), records


def test_hxc_reads_modules_from_a_directory_it_is_told_about(tmp_path, capsys):
    helpers = tmp_path / "lib"
    helpers.mkdir()
    (helpers / "notes.hx").write_text(NOTES, encoding="utf-8")
    entry = project(tmp_path, module_text=None)
    assert hxc(["check", "--modules", str(helpers), str(entry)]) == 0
    assert hxc(["check", str(entry)]) == 1


def test_the_manifest_lists_every_file_the_program_came_from(tmp_path, capsys):
    entry = project(tmp_path)
    assert hxc(["check", "--manifest", "-", str(entry)]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert [source["module"] for source in manifest["sources"]] == ["main", "notes"]
    assert [Path(source["path"]).name for source in manifest["sources"]] == [
        "main.hx",
        "notes.hx",
    ]
    assert len(manifest["program_sha256"]) == 64


def test_the_program_digest_is_the_same_program_in_two_directories(tmp_path, capsys):
    """A pin has to survive being checked out somewhere else.

    The path a file sits at is a fact about this machine; the program is a fact about
    the module names and their bytes. If the digest mixed paths in, the digest an
    approval recorded in Section 1 would not be recognisable by a runner mounting the
    same three files at a different mount point — and the refusal would look like
    tampering.
    """
    first = project(tmp_path / "one")
    second = project(tmp_path / "elsewhere" / "deep")
    digests = []
    for entry in (first, second):
        assert hxc(["check", "--manifest", "-", str(entry)]) == 0
        digests.append(json.loads(capsys.readouterr().out)["program_sha256"])
    assert digests[0] == digests[1]


# ------------------------------------------------------------------- hxr


def test_hxr_runs_a_two_file_program_from_the_terminal(tmp_path, capsys):
    entry = project(tmp_path)
    assert hxr(run_args(entry)) == 0, capsys.readouterr().err
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["outcome"]["output"]["text"] == "!"
    assert evidence["recording"]["origin"] == "live"


def test_hxr_plan_reports_the_module_set(tmp_path, capsys):
    entry = project(tmp_path)
    assert hxr(["plan", str(entry), "--grant", ALL, "--json"]) == 0
    manifest = json.loads(capsys.readouterr().out)["manifest"]
    assert [source["module"] for source in manifest["sources"]] == ["main", "notes"]


def test_a_swapped_helper_fails_the_program_digest_pin(tmp_path, capsys):
    entry = project(tmp_path)
    assert hxr(["plan", str(entry), "--grant", ALL, "--json"]) == 0
    approved = json.loads(capsys.readouterr().out)["manifest"]["program_sha256"]

    ok = hxr(run_args(entry, "--program-digest", approved))
    assert ok == 0, capsys.readouterr().err
    capsys.readouterr()  # the passing run's evidence is not the record under test

    (tmp_path / "notes.hx").write_text(NOTES.replace('text: "!"', 'text: "?"'), encoding="utf-8")
    assert hxr(run_args(entry, "--program-digest", approved)) == 1
    # The wire line on stderr is the frozen diagnostic shape, which carries the code
    # inside the message; the machine-readable code and detail live in the bundle.
    captured = capsys.readouterr()
    assert "[HXH103]" in captured.err
    failure = json.loads(captured.out)["outcome"]["error"]
    assert failure["code"] == "HXH103"
    assert failure["detail"]["expected"] == approved
    assert [Path(p).name for p in failure["detail"]["paths"]] == ["main.hx", "notes.hx"]
    assert failure["detail"]["actual"] != approved


def test_the_entry_digest_alone_lets_a_swapped_helper_run(tmp_path, capsys):
    """Why `--program-digest` exists: `--digest` is satisfied by an unchanged entry.

    With the old pin and a changed helper the run goes ahead and the output differs.
    That is the failure M8 is here to close, so it is pinned as a behaviour, not left
    as a comment: a flag that looks like a pin and is not one gets discovered during
    an incident, not before it.
    """
    entry = project(tmp_path)
    unchanged_entry = digest_of(MAIN)
    (tmp_path / "notes.hx").write_text(NOTES.replace('text: "!"', 'text: "?"'), encoding="utf-8")

    assert hxr(run_args(entry, "--digest", unchanged_entry)) == 0, capsys.readouterr().err
    output = json.loads(capsys.readouterr().out)["outcome"]["output"]
    assert output["text"] == "?"
