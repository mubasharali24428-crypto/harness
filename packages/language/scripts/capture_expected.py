"""Regenerate the golden expectation files next to the fixtures.

Goldens are generated only when a rule's message changes on purpose, and every
regenerated file must be read before it is committed: a golden you did not read
is a test that agrees with the code instead of checking it.

    ./.venv/bin/python scripts/capture_expected.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness_hx import check_source

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
TESTS = FIXTURES.parent
sys.path.insert(0, str(TESTS))

from support import records  # noqa: E402 - needs the tests dir on the path first


def modules_for(source_path: Path) -> dict[str, str] | None:
    """The `<name>.hx.modules.json` sidecar, if this fixture has one."""
    sidecar = source_path.with_suffix(".hx.modules.json")
    if not sidecar.exists():
        return None
    return dict(json.loads(sidecar.read_text(encoding="utf-8")))

# (directory, profile) — each fixture set is checked under the profile it targets.
TARGETS = (("invalid", "agent"), ("warnings", "agent"), ("core", "core"), ("valid", "agent"))


def main() -> int:
    for directory, profile in TARGETS:
        for source_path in sorted((FIXTURES / directory).glob("*.hx")):
            result = check_source(
                source_path.read_text(encoding="utf-8"),
                source_path.name,
                profile,
                modules=modules_for(source_path),
            )
            # Same shape the test compares against, from the same helper, so a golden
            # and an assertion can never disagree about what a record contains.
            expected = records(result)
            target = source_path.with_suffix(".expected.json")
            target.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
            print(f"{target.relative_to(FIXTURES)}: {len(expected)} record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
