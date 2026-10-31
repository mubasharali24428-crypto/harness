"""M3: the Hx driver's host operations through Section 5's REAL executor.

Everything else in the language suite runs against a fake host. This file runs
against the shipped ``LocalSandboxRunner``: a real task directory, a real
confinement mode (Seatbelt on macOS, Landlock on Linux), a real ``cat``, and a
real test suite whose exit code decides the result.

Two host operations are exercised, because they are genuinely different shapes:

* ``repo.read`` — content. It crosses Section 5's streaming ``exec`` and comes
  back as text, capped, with the path checked before the command exists.
* ``verify.tests`` — a verdict. It crosses Section 5's settled
  ``exec_structured``, which is the only shape that can honestly become
  Evidence: a status, an exit code and counts, not a pile of lines.

If the host has no OS-level boundary, ``auto`` refuses to run unconfined — and
so does this test: it skips with the runner's own reason rather than proving
something weaker than the product claims. That skip is the only one, and it is
loud.

The language package is imported by path: Section 5's venv installs the
sandbox, not the driver, and this test is about the seam between them.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

LANGUAGE_SRC = Path(__file__).resolve().parents[3] / "packages" / "language" / "src"
if str(LANGUAGE_SRC) not in sys.path:
    sys.path.insert(0, str(LANGUAGE_SRC))

from harness_hx_host import (  # noqa: E402
    ConfinedCommandRunner,
    ConfinedReader,
    SandboxHostBridge,
    run_source,
)
from harness_hx_host.errors import InvalidHostResult  # noqa: E402

from harness_sandbox.local_runner import LocalSandboxRunner  # noqa: E402
from harness_sandbox.policy import SecurityPolicyViolationError  # noqa: E402

PY = "python3" if shutil.which("python3") else "python"

READ_ONE = """\
module read_one
schema RepoFile {
  path: String
  text: String
}
workflow read_one() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let readme = await repo.read("README.md")
  ensure readme.text != ""
  return readme
}
"""

TESTS_ONE = """\
module tests_one
schema Evidence {
  passed: Bool
  detail: String
}
workflow tests_one() -> Evidence {
  requires tool.test
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let evidence = await verify.tests("origin/main", suite: "unit")
  return evidence
}
"""

UNITTEST_OK = """\
import unittest


class Probe(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(1 + 1, 2)

    def test_strings(self):
        self.assertEqual("hx".upper(), "HX")


if __name__ == "__main__":
    unittest.main()
"""

UNITTEST_FAILS = """\
import unittest


class Probe(unittest.TestCase):
    def test_that_must_fail(self):
        self.assertEqual(1 + 1, 3)


if __name__ == "__main__":
    unittest.main()
"""


class LiveTask:
    """One real sandboxed task, torn down even when the assertions blow up."""

    def __init__(self, storage_root: Path, forbidden: list[str] | None = None) -> None:
        self.runner = LocalSandboxRunner(storage_root=storage_root, confinement="auto")
        try:
            self.handle = self.runner.create_task(
                "hx-live-1", "main", "main", forbidden_paths=forbidden or [".env"]
            )
        except SecurityPolicyViolationError as exc:  # no usable OS boundary here
            pytest.skip(f"no OS-level sandbox boundary on this host: {exc}")
        self.workspace = Path(self.runner.active_tasks["hx-live-1"]["workspace"])
        self.mode = self.runner.active_tasks["hx-live-1"]["confinement"]

    def write(self, name: str, text: str) -> None:
        self.workspace.joinpath(name).write_text(text, encoding="utf-8")

    def bridge(self, test_commands: dict[str, str] | None = None) -> SandboxHostBridge:
        return SandboxHostBridge(
            confined=ConfinedCommandRunner(self.runner, self.handle),
            reader=ConfinedReader(self.runner, self.handle),
            project_id="proj-live",
            test_commands=test_commands or {},
        )

    def close(self) -> None:
        self.runner.destroy(self.handle)


@pytest.fixture
def live_task(tmp_path):
    task = LiveTask(tmp_path / "sandbox")
    yield task
    task.close()


def test_a_real_read_comes_back_through_the_confined_sandbox(live_task):
    live_task.write("README.md", "# live repo\nsecond line\n")
    bridge = live_task.bridge()

    result = run_source(READ_ONE, bridge=bridge, granted=["repo.read"])

    assert result.status == "ok", result.evidence()
    assert result.output.to_json() == {
        "path": "README.md",
        "text": "# live repo\nsecond line\n",
    }
    kinds = [event["kind"] for event in result.events]
    assert kinds == ["run.started", "call.start", "call.finished", "ensure.passed"]
    assert result.events[1]["operation"] == "repo.read"
    # The confinement that actually ran, recorded in the evidence a reviewer keeps.
    assert live_task.mode in ("seatbelt", "landlock")


def test_a_real_suite_decides_the_result_by_its_exit_code(live_task):
    live_task.write("test_probe.py", UNITTEST_OK)
    live_task.write("test_broken.py", UNITTEST_FAILS)
    bridge = live_task.bridge(
        {
            "unit": f"{PY} -m unittest -q test_probe",
            "broken": f"{PY} -m unittest -q test_broken",
        }
    )

    passing = run_source(TESTS_ONE, bridge=bridge, granted=["tool.test"])
    assert passing.status == "ok", passing.evidence()
    assert passing.output.to_json()["passed"] is True
    assert "exit 0" in passing.output.to_json()["detail"]

    # The same program, a suite that really fails: the verdict flips because the
    # exit code was 1, not because this test told it to.
    failing_bridge = live_task.bridge(
        {
            "unit": f"{PY} -m unittest -q test_broken",
            "broken": f"{PY} -m unittest -q test_broken",
        }
    )
    failing = run_source(TESTS_ONE, bridge=failing_bridge, granted=["tool.test"])
    assert failing.status == "ok", failing.evidence()
    assert failing.output.to_json()["passed"] is False
    assert "exit 1" in failing.output.to_json()["detail"]


def test_a_suite_the_host_has_not_allow_listed_is_refused_not_guessed(live_task):
    live_task.write("test_probe.py", UNITTEST_OK)
    bridge = live_task.bridge({"unit": f"{PY} -m unittest -q test_probe"})

    result = run_source(TESTS_ONE, bridge=bridge, granted=["tool.test"])

    assert result.status == "ok"
    assert result.output.to_json()["passed"] is True
    # Same program, suite name the host did not map: a refusal with a code.
    unknown = TESTS_ONE.replace('suite: "unit"', 'suite: "nightly"')
    refused = run_source(unknown, bridge=bridge, granted=["tool.test"])
    assert refused.status == "error"
    assert "no allow-listed test command" in refused.evidence()["outcome"]["error"]["message"]


def test_a_forbidden_path_is_refused_before_the_command_exists(live_task):
    live_task.write(".env", "HARNESS_MASTER_KEY=do-not-read\n")

    with pytest.raises(InvalidHostResult, match="forbidden path"):
        live_task.bridge().read_file(".env")
    with pytest.raises(InvalidHostResult, match="workspace-relative"):
        live_task.bridge().read_file("/etc/passwd")
    with pytest.raises(InvalidHostResult, match="forbidden path"):
        live_task.bridge().read_file("docs/../../escape.txt")
