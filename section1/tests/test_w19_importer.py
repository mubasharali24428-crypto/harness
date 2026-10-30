"""Importer regression checks use synthetic data, never real credentials."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/migrate_agent_config.py"
spec = importlib.util.spec_from_file_location("w19_migrate_agent_config", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


@pytest.mark.parametrize("ending", ["", "-----END RSA PRIVATE KEY-----\n"])
def test_unclosed_or_mismatched_private_key_never_reaches_preview(tmp_path, capsys, ending):
    (tmp_path / "AGENTS.md").write_text(
        "keep this\n-----BEGIN PRIVATE KEY-----\nSYNTHETIC-KEY-BODY\n" + ending
        + "SYNTHETIC-TRAILING-BODY\n"
    )
    assert mod.main(["--root", str(tmp_path), "--project-id", "p"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert "SYNTHETIC" not in preview["rulesText"]
    assert "keep this" in preview["rulesText"]


def test_closed_private_key_preserves_following_instructions():
    text, dropped = mod.scrub(
        "before\n-----BEGIN PRIVATE KEY-----\nfake-body\n"
        "-----END PRIVATE KEY-----\nafter\n"
    )
    assert text == "before\nafter"
    assert dropped == 3


def test_large_block_replacement_counts_actual_result():
    source = mod.Source("AGENTS.md", Path("AGENTS.md"), "x" * 40000, 0)
    existing = "preserved\n" + mod.MARK_BEGIN + "\n" + "y" * 40000 + "\n" + mod.MARK_END
    document = mod.build_document([source], existing=existing)
    assert document.replaced
    assert document.text.startswith("preserved\n")
    assert len(document.text.encode("utf-8")) <= mod.MAX_RULES_BYTES


@pytest.mark.parametrize("existing", [mod.MARK_BEGIN, mod.MARK_END,
                                      mod.MARK_END + mod.MARK_BEGIN,
                                      mod.MARK_BEGIN * 2 + mod.MARK_END])
def test_invalid_existing_markers_refuse_to_merge(existing):
    source = mod.Source("AGENTS.md", Path("AGENTS.md"), "rules", 0)
    with pytest.raises(ValueError, match="markers"):
        mod.build_document([source], existing=existing)
