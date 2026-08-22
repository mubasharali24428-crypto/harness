"""Demo repository manifest — brief §11, §16 Q7, §23 Q18.

The demo repo itself lives in `section10/demo_repo/` (plain files, no secrets,
no network, no auth/payments code — low-risk by construction). This module
describes it and the suggested first task so the onboarding UI can present it
without filesystem access to the repo checkout.
"""

from pydantic import BaseModel


class DemoTask(BaseModel):
    title: str
    goal: str
    acceptanceCriteria: list[str]
    suggestedForbiddenPaths: list[str]


class DemoRepoManifest(BaseModel):
    name: str
    description: str
    language: str
    testCommand: str
    lintCommand: str
    task: DemoTask
    pushScript: str
    files: list[str]


MANIFEST = DemoRepoManifest(
    name="harness-demo-textstats",
    description=(
        "A tiny pure-Python text-statistics library with tests and lint config. "
        "Deliberately boring: no secrets, no network, no auth or payment code — "
        "safe for a first agent run."
    ),
    language="python",
    testCommand="python -m pytest -q",
    lintCommand="python -m ruff check .",
    task=DemoTask(
        title="Implement median_word_length",
        goal=(
            "textstats.stats.median_word_length currently raises NotImplementedError. "
            "Implement it: median length of whitespace-separated words, 0 for empty "
            "input. The failing test tests/test_stats.py::test_median_word_length "
            "already exists and must pass."
        ),
        acceptanceCriteria=[
            "python -m pytest -q exits 0",
            "python -m ruff check . is clean",
            "No files outside src/textstats/ are modified",
        ],
        suggestedForbiddenPaths=["tests/test_stats.py", "pyproject.toml"],
    ),
    pushScript="deploy/scripts/push_demo_to_github.sh",
    files=[
        "README.md",
        "pyproject.toml",
        "ruff.toml",
        "src/textstats/__init__.py",
        "src/textstats/stats.py",
        "tests/test_stats.py",
        ".harness/demo-task.json",
    ],
)


def manifest_payload() -> dict:
    return MANIFEST.model_dump()
