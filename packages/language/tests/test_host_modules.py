"""M8: programs made of more than one file, and the two ways to compute a value.

Three additions, and the boundary between them is the point of the milestone:

* `use notes` brings another file's **types and pure functions** into this one under
  a module-qualified name. The host supplies the bytes, the manifest pins a digest
  for every file, and swapping a helper is as refused as swapping the workflow.
* `for … accumulate` binds a value per pass. It is a binding, not an assignment, so
  the loop's termination story (the list bounds the passes, the budget bounds the
  spend) is exactly the story M5 told.
* `base with { field: value }` yields a new record. Nothing is edited in place, so a
  helper handed a record cannot be holding a different one afterwards.

Deliberately absent, and tested absent: assignment, `while`, nested modules, dynamic
loading, arithmetic that the language never had. `+` still only joins Strings, which
is why the accumulator below builds a report rather than counting rows.
"""

from __future__ import annotations

import hashlib
import re

from harness_hx.api import check_file, check_source
from harness_hx.manifest import build_manifest
from harness_hx_host import FakeHostBridge, run_source

# ------------------------------------------------------------------- files

NOTES = """module notes

schema Summary {
  text: String
  level: Level
}

enum Level {
  low
  high
}

fn shout(base: Summary) -> Summary {
  return base with { text: "!" }
}

fn loudest(a: Summary, b: Summary) -> Summary {
  if a.level == Level.high {
    return a
  }
  return b
}
"""

MAIN = """module main

use notes

schema RepoFile {
  path: String
  text: String
}

workflow run() -> notes.Summary {
  requires repo.read
  budget { steps: 50; tool_calls: 4; duration: 1m }
  let file = await repo.read("README.md")
  let seed = notes.Summary(text: file.text, level: notes.Level.high)
  return notes.shout(seed)
}
"""

ALL = ("repo.read",)
SHA = re.compile(r"^[0-9a-f]{64}$")


def run(main: str = MAIN, modules=..., **kwargs) -> object:
    bridge = kwargs.pop("bridge", FakeHostBridge())
    return run_source(
        main,
        path=kwargs.pop("path", "main.hx"),
        bridge=bridge,
        granted=kwargs.pop("granted", ALL),
        inputs=kwargs.pop("inputs", {}),
        modules={"notes": NOTES} if modules is ... else modules,
        **kwargs,
    )


def errors(result) -> list[str]:
    return [record.code for record in result.diagnostics if record.kind == "error"]


def one(result, code: str):
    return next(record for record in result.diagnostics if record.code == code)


def manifest_of(source: str, modules=None, profile: str = "agent") -> dict:
    checked = check_source(source, "main.hx", profile, modules=modules)
    return build_manifest(checked, profile)


# --------------------------------------------------------------- module use


def test_a_module_type_and_function_are_reachable_by_qualified_name():
    checked = check_source(MAIN, "main.hx", "agent", modules={"notes": NOTES})
    assert checked.ok, [(r.path, r.code, r.message) for r in checked.diagnostics]
    assert [use.module for use in checked.program.uses] == ["notes"]
    assert checked.modules[0].name == "notes"
    # The module's decls arrive renamed, so one name means one thing in the reader.
    assert {decl.name for decl in checked.modules[0].program.decls} == {
        "notes.Summary",
        "notes.Level",
        "notes.shout",
        "notes.loudest",
    }


def test_a_module_call_runs_and_the_value_keeps_the_modules_type_name():
    result = run()
    assert result.status == "ok", result.error
    # `notes.Summary` is the type on both sides of the boundary: a look-alike record
    # declared in main.hx cannot be passed to a helper that wants the module's one.
    assert result.output.name == "notes.Summary"
    assert result.output.to_json() == {"text": "!", "level": "high"}


def test_the_helper_runs_the_same_way_in_its_own_file():
    standalone = NOTES.replace(
        "fn shout(base: Summary) -> Summary {",
        "fn shout(base: Summary) -> Summary {",
    ) + """
workflow own() -> Summary {
  budget { steps: 10 }
  return shout(Summary(text: "x", level: Level.low))
}
"""
    result = run_source(
        standalone, path="notes.hx", bridge=FakeHostBridge(), granted=(), inputs={}
    )
    assert result.status == "ok", result.error
    # In its own file the name is the bare one; only readers see the prefix.
    assert result.output.name == "Summary"
    assert result.output.to_json() == {"text": "!", "level": "low"}


def test_a_local_declaration_that_looks_like_a_module_record_is_not_accepted():
    lookalike = MAIN.replace(
        "  let seed = notes.Summary(text: file.text, level: notes.Level.high)",
        "  let seed = Summary(text: file.text, level: \"high\")\n"
        "  ensure shout(seed).text == \"!\"",
    )
    checked = check_source(lookalike, "main.hx", "agent", modules={"notes": NOTES})
    assert not checked.ok
    assert "HX3001" in errors(checked) or "HX3005" in errors(checked), errors(checked)


# ------------------------------------------------------- digest binding (M8)


def test_the_manifest_pins_every_file_the_program_was_built_from():
    built = manifest_of(MAIN, modules={"notes": NOTES})
    listed = {entry["path"]: entry["sha256"] for entry in built["sources"]}
    assert listed == {
        "main.hx": hashlib.sha256(MAIN.encode("utf-8")).hexdigest(),
        "notes.hx": hashlib.sha256(NOTES.encode("utf-8")).hexdigest(),
    }
    assert SHA.match(built["program_sha256"])


def test_the_program_digest_moves_when_only_a_helper_changes():
    before = manifest_of(MAIN, modules={"notes": NOTES})["program_sha256"]
    after = manifest_of(
        MAIN, modules={"notes": NOTES.replace('text: "!"', 'text: "?"')}
    )["program_sha256"]
    assert before != after, "a helper edit must be visible in the program identity"


def test_a_helper_that_the_run_cannot_resolve_is_refused():
    """A manifest naming files this run cannot honour is not a runnable program."""
    approved = manifest_of(MAIN, modules={"notes": NOTES})["program_sha256"]
    outcome = run(modules=None, expected_program_digest=approved)
    assert outcome.status == "refused"
    assert outcome.error.code in ("HXH101", "HXH103"), outcome.error.code


def test_swapping_a_module_after_approval_is_refused_by_name():
    """The M2-era digest lock, extended from one file to the whole program set."""
    approved = manifest_of(MAIN, modules={"notes": NOTES})["program_sha256"]
    mutated = run(
        modules={"notes": NOTES.replace('text: "!"', 'text: "?"')},
        expected_program_digest=approved,
    )
    assert mutated.status == "refused"
    assert mutated.error.code == "HXH103"
    assert mutated.error.detail["paths"] == ["main.hx", "notes.hx"]
    unchanged = run(expected_program_digest=approved)
    assert unchanged.status == "ok", unchanged.error


def test_a_module_named_by_the_program_but_never_supplied_is_refused():
    outcome = run(modules=None)
    assert outcome.status == "refused"
    assert "HX3015" in [record.code for record in outcome.diagnostics]


def test_a_file_that_uses_nothing_needs_no_module_source():
    """The single-file path is untouched: no `use`, no loader in the way."""
    single = MAIN.replace("use notes\n\n", "")
    checked = check_source(single, "single.hx", "agent")
    assert "notes.hx" not in [
        entry["path"] for entry in build_manifest(checked, "agent")["sources"]
    ]


def test_checking_a_file_on_disk_resolves_its_neighbours(tmp_path):
    (tmp_path / "notes.hx").write_text(NOTES, encoding="utf-8")
    entry = tmp_path / "main.hx"
    entry.write_text(MAIN, encoding="utf-8")
    checked = check_file(entry, "agent")
    assert checked.ok, [(r.code, r.message) for r in checked.diagnostics]
    assert checked.modules[0].path == "notes.hx"


def test_a_record_points_at_the_file_its_position_sits_in():
    """Which file a diagnostic blames is part of the diagnostic, not a detail.

    A `use` clause that cannot be honoured is the reader's mistake and is reported in
    the reader's file, at the clause. A module that arrived and is wrong inside is
    reported in the module's file, where the offending line actually is. Pointing at
    `notes.hx` for a file this check never received sends an editor to nothing, and
    pointing at `main.hx` for a bad line in a helper makes the helper unfixable.
    """
    missing = check_source(MAIN, "main.hx", "agent")
    unresolvable = one(missing, "HX3015")
    assert unresolvable.path == "main.hx", unresolvable.text

    renamed = check_source(
        MAIN, "main.hx", "agent", modules={"notes": NOTES.replace("module notes", "module other")}
    )
    assert one(renamed, "HX3016").path == "main.hx"

    with_entry = check_source(
        MAIN, "main.hx", "agent", modules={"notes": NOTES + "\nworkflow e() -> Int {\n}\n"}
    )
    in_module = one(with_entry, "HX3019")
    assert in_module.path == "notes.hx", in_module.text
    assert in_module.line <= len(NOTES.splitlines()) + 2


def test_a_module_that_uses_another_module_is_refused():
    """One level deep, on purpose. A helper pulling in helpers needs a namespace
    story this language does not have, so the loader says so instead of guessing."""
    deep = NOTES.replace("module notes", "module notes\nuse helper")
    checked = check_source(
        MAIN,
        "main.hx",
        "agent",
        modules={"notes": deep, "helper": NOTES.replace("notes", "helper")},
    )
    assert "HX3017" in errors(checked)
    assert "one level deep" in one(checked, "HX3017").message


def test_a_module_declaring_a_workflow_is_refused():
    with_entry = NOTES + """
workflow entry() -> Int {
  budget { steps: 5 }
  return 1
}
"""
    checked = check_source(MAIN, "main.hx", "agent", modules={"notes": with_entry})
    assert "HX3019" in errors(checked)
    assert "never an entry point" in one(checked, "HX3019").message


def test_a_file_used_under_a_name_it_does_not_declare_is_refused():
    renamed = NOTES.replace("module notes", "module other")
    checked = check_source(MAIN, "main.hx", "agent", modules={"notes": renamed})
    assert "HX3016" in errors(checked)
    assert "declares module 'other'" in one(checked, "HX3016").message


def test_a_qualified_type_without_the_use_is_refused():
    lonely = """module lonely

schema Local {
  text: String
}

workflow one() -> notes.Summary {
  return Local(text: "x")
}
"""
    checked = check_source(lonely, "lonely.hx", "agent", modules={"notes": NOTES})
    assert "HX3020" in errors(checked)
    assert "add 'use notes'" in one(checked, "HX3020").message


def test_a_binding_may_not_shadow_a_used_module_name():
    """`let notes = 1` would leave one identifier meaning both a value and a
    namespace, and the module rename that makes `use` work depends on it not."""
    clash = MAIN.replace("  let file = await", "  let notes = 1\n  let file = await")
    checked = check_source(clash, "main.hx", "agent", modules={"notes": NOTES})
    assert "HX3025" in errors(checked)


def test_a_file_cannot_use_the_module_it_declares():
    """`use main` inside `module main` would give one name two files.

    The manifest keys each per-file digest by the module the file declares, so a name
    used for both this file and a helper would put two entries under one key — and a
    swap could hide behind the other one.
    """
    selfish = MAIN.replace("use notes", "use main")
    checked = check_source(selfish, "main.hx", "agent", modules={"notes": NOTES, "main": NOTES})
    assert "HX3017" in errors(checked)
    assert "cannot take itself in" in one(checked, "HX3017").message


def test_a_manifest_entry_names_the_file_and_its_own_digest():
    """The per-file record is what a runner verifies, so its shape is worth pinning.

    `module` is the identity the digest is taken over; `path` is for the human reading
    it, and `bytes` lets a reviewer see a helper grow without opening it.
    """
    built = manifest_of(MAIN, modules={"notes": NOTES})
    assert sorted(built["sources"][0]) == ["bytes", "module", "path", "sha256"]
    assert [entry["module"] for entry in built["sources"]] == ["main", "notes"]
    assert [entry["path"] for entry in built["sources"]] == ["main.hx", "notes.hx"]
    single = manifest_of(MAIN.replace("use notes\n\n", "").replace("notes.", ""))
    assert [entry["module"] for entry in single["sources"]] == ["main"]


def test_a_use_clause_after_a_declaration_is_refused():
    """Which files a program is built from is answered at the top of it."""
    late = MAIN.replace("use notes\n\n", "").replace(
        "workflow run() -> notes.Summary {",
        "use notes\n\nworkflow run() -> notes.Summary {",
    )
    checked = check_source(late, "main.hx", "agent", modules={"notes": NOTES})
    assert "HX2006" in errors(checked)


def test_a_module_name_cannot_reach_outside_the_root(tmp_path):
    (tmp_path / "main.hx").write_text(
        MAIN.replace("use notes", "use ../outside"), encoding="utf-8"
    )
    checked = check_file(tmp_path / "main.hx", "agent")
    assert not checked.ok
    assert "HX2001" in errors(checked) or "HX3015" in errors(checked)


# --------------------------------------------------------- with / accumulate


def test_a_with_expression_leaves_the_original_record_untouched():
    source = """module update

schema Counter {
  total: Int
  label: String
}

workflow bump() -> Counter {
  budget { steps: 20 }
  let original = Counter(total: 1, label: "one")
  let copy = original with { total: 2 }
  ensure original.total == 1
  ensure copy.total == 2
  ensure copy.label == "one"
  return copy
}
"""
    result = run_source(source, path="update.hx", bridge=FakeHostBridge(), inputs={})
    assert result.status == "ok", result.error
    assert result.output.to_json() == {"total": 2, "label": "one"}


def test_with_cannot_add_a_field_or_update_a_non_record():
    missing = """module wrong

schema Counter {
  total: Int
}

workflow bump() -> Counter {
  budget { steps: 20 }
  return Counter(total: 1) with { other: 2 }
}
"""
    not_a_record = """module wrong2

workflow bump() -> Int {
  budget { steps: 20 }
  let n = 1
  return n with { total: 2 }
}
"""
    assert "HX3023" in errors(check_source(missing, "wrong.hx", "agent"))
    assert "no field 'other'" in one(check_source(missing, "wrong.hx", "agent"), "HX3023").message
    assert "HX3022" in errors(check_source(not_a_record, "wrong2.hx", "agent"))


def test_a_loop_can_carry_a_value_without_assignment():
    """The accumulator is a binding the loop creates, not a variable it edits."""
    source = """module report

schema RepoFile {
  path: String
  text: String
}

workflow build() -> String {
  requires repo.read
  budget { steps: 50; tool_calls: 1; duration: 1m }
  let file = await repo.read("README.md")
  for part in [file.text, "# map\\n", "tail"] accumulate body: String = "" {
    body + part
  }
  ensure body == "# demo\\n# map\\ntail"
  return body
}
"""
    result = run_source(source, bridge=FakeHostBridge(), granted=("repo.read",), inputs={})
    assert result.status == "ok", result.error
    assert result.output == "# demo\n# map\ntail"
    passes = [event for event in result.events if event["kind"] == "loop.iteration"]
    # The value is recorded on the way in, so a reviewer sees what each pass carried.
    assert [event["value"] for event in passes] == ["", "# demo\n", "# demo\n# map\n"]
    assert all(event["accumulator"] == "body" for event in passes)


def test_the_accumulator_type_is_checked_against_the_block():
    source = """module bad

workflow one() -> Int {
  budget { steps: 50 }
  for n in [1, 2] accumulate total: Int = 0 {
    "not a number"
  }
  return total
}
"""
    checked = check_source(source, "bad.hx", "agent")
    assert "HX3024" in errors(checked)


def test_an_accumulate_loop_without_a_step_budget_is_refused():
    """M5's rule applies to the value-carrying form too: no steps, no loop."""
    source = """module bad

workflow one() -> String {
  budget { steps: 50 }
  for n in ["a"] accumulate total: String = "" {
    total + n
  }
  return total
}
"""
    checked = check_source(source.replace("steps: 50", "duration: 1m"), "bad.hx", "agent")
    assert "HX5006" in errors(checked)


def _accumulate_with(steps: int):
    source = f"""module many

workflow one() -> String {{
  budget {{ steps: {steps} }}
  for part in ["a", "b", "c", "d", "e"] accumulate body: String = "" {{
    body + part
  }}
  return body
}}
"""
    return run_source(source, bridge=FakeHostBridge(), inputs={})


def test_the_growing_accumulator_still_burns_its_budget():
    """Termination is the list and the budget, never a mutated condition.

    The same program under two ceilings: the log carries the value each pass
    started with, so a stopped accumulate loop shows how far it got.
    """
    stopped = _accumulate_with(3)
    assert stopped.status == "budget"
    assert stopped.error.code == "HXH501"
    assert [event["value"] for event in stopped.events if event["kind"] == "loop.iteration"] == [""]
    assert stopped.budget["used"]["steps"] == 4

    finished = _accumulate_with(20)
    assert finished.status == "ok", finished.error
    assert finished.output == "abcde"
    assert [event["value"] for event in finished.events if event["kind"] == "loop.iteration"] == [
        "", "a", "ab", "abc", "abcd",
    ]


def test_the_language_still_has_no_assignment_and_no_while():
    """What did not arrive with M8, tested so it cannot arrive by accident."""
    for source, why in (
        ("module a\nworkflow w() -> Int {\n  budget { steps: 5 }\n  let n = 1\n  n = 2\n"
         "  return n\n}\n", "assignment"),
        ("module a\nworkflow w() -> Int {\n  budget { steps: 5 }\n  while true { }\n"
         "  return 1\n}\n", "while"),
        ("module a\nimport x\nworkflow w() -> Int {\n  budget { steps: 5 }\n  return 1\n}\n",
         "import"),
    ):
        checked = check_source(source, "a.hx", "agent")
        assert not checked.ok, why
        assert "HX3025" not in errors(checked), why  # it is a syntax refusal, not a shadow
