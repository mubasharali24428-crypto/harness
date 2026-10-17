"""A4-S4 guided use: repo readiness (F132), tutorial project (F133), task templates (F134),
onboarding progress (F131). Pure functions over a checkout; nothing here runs repo code."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from harness_devworkflow.service import LaneError

# ---- F132 readiness ---------------------------------------------------------------------------

_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:ghp|gho|ghs)_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
)
_TEST_HINTS = ("tests", "test", "__tests__", "spec")
_CI_HINTS = (".github/workflows", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml")
_LOCKFILES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
)
_MAX_SCAN_FILES, _MAX_SCAN_BYTES = 400, 200_000


def _check(cid: str, ok: bool, label: str, fix: str, *, level: str = "warn") -> dict[str, Any]:
    return {
        "id": cid,
        "status": "pass" if ok else level,
        "label": label,
        "fix": None if ok else fix,
    }


def _tracked(root: Path) -> list[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": "/nonexistent",
    }
    out = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "ls-files", "-z"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return [p for p in out.stdout.split("\0") if p] if out.returncode == 0 else []


def readiness(root: Path) -> dict[str, Any]:
    """Static, read-only assessment of whether a repo is ready for agent work."""
    files = _tracked(root)
    names = set(files)
    tops = {f.split("/", 1)[0] for f in files}
    secret_hits: list[str] = []
    for rel in files[:_MAX_SCAN_FILES]:
        p = root / rel
        if p.is_symlink() or not p.is_file() or p.stat().st_size > _MAX_SCAN_BYTES:
            continue
        try:
            if _SECRET.search(p.read_text("utf-8")):
                secret_hits.append(rel)  # path only; never echo the secret
        except (UnicodeDecodeError, OSError):
            continue
    checks = [
        _check(
            "git-tracked",
            bool(files),
            "Repository has tracked files",
            "Commit at least one file.",
            level="fail",
        ),
        _check(
            "readme",
            any(n.lower().startswith("readme") for n in names),
            "README present",
            "Add a README describing build and test commands.",
        ),
        _check(
            "tests",
            bool(tops & set(_TEST_HINTS)) or any("test" in n.lower() for n in names),
            "Tests exist",
            "Add tests so agent changes can be verified.",
        ),
        _check(
            "ci",
            any(n.startswith(h) or n == h for h in _CI_HINTS for n in names),
            "CI configured",
            "Add CI so reviewers get independent verification.",
        ),
        _check(
            "lockfile",
            bool(names & set(_LOCKFILES)),
            "Dependency lockfile committed",
            "Commit a lockfile for reproducible installs.",
        ),
        _check(
            "gitignore",
            ".gitignore" in names,
            ".gitignore present",
            "Add .gitignore so build output is not proposed as changes.",
        ),
        _check(
            "codeowners",
            any(n in names for n in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")),
            "CODEOWNERS present",
            "Add CODEOWNERS so reviewer routing works.",
        ),
        _check(
            "no-secrets",
            not secret_hits,
            "No credential-looking text in scanned files",
            f"Remove and rotate credentials found in: {', '.join(secret_hits[:5])}",
            level="fail",
        ),
    ]
    score = sum(c["status"] == "pass" for c in checks)
    blocked = any(c["status"] == "fail" for c in checks)
    return {
        "checks": checks,
        "score": score,
        "total": len(checks),
        "verdict": "blocked"
        if blocked
        else ("ready" if score == len(checks) else "usable-with-gaps"),
        "scanned": min(len(files), _MAX_SCAN_FILES),
        "truncated": len(files) > _MAX_SCAN_FILES,
    }


# ---- F133 tutorial project --------------------------------------------------------------------

TUTORIAL_FILES = {
    "README.md": "# Tutorial project\n\n`python -m unittest` runs the tests. One test fails on purpose:\n"
    "fix `slugify` in `slug.py`, review the diff, then run verification.\n",
    "slug.py": 'def slugify(text):\n    return text.lower().replace(" ", "-")\n',
    "test_slug.py": "import unittest\n\nfrom slug import slugify\n\n\nclass T(unittest.TestCase):\n"
    "    def test_basic(self):\n        self.assertEqual(slugify('Hello World'), 'hello-world')\n\n"
    "    def test_punctuation_and_spaces(self):\n"
    "        self.assertEqual(slugify('  Hi, there!  '), 'hi-there')\n\n\nif __name__ == '__main__':\n    unittest.main()\n",
    ".gitignore": "__pycache__/\n",
    "CODEOWNERS": "* @tutorial-owner\n",
}


def create_tutorial(root: Path, team_id: str, user_id: str) -> Path:
    """Idempotent per (team,user); lives under the service-owned tutorial root."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{team_id}-{user_id}")[:80]
    repo = root / safe
    if (repo / ".git").exists():
        return repo
    repo.mkdir(parents=True, exist_ok=True)
    for rel, text in TUTORIAL_FILES.items():
        (repo / rel).write_text(text, "utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(repo),
        "GIT_AUTHOR_NAME": "Harness Tutorial",
        "GIT_AUTHOR_EMAIL": "tutorial@harness.invalid",
        "GIT_COMMITTER_NAME": "Harness Tutorial",
        "GIT_COMMITTER_EMAIL": "tutorial@harness.invalid",
    }
    for argv in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "Tutorial project"],
    ):
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *argv],
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
            timeout=20,
        )
    return repo


# ---- F134 task templates ----------------------------------------------------------------------

TEMPLATES: dict[str, dict[str, Any]] = {
    "fix-bug": {
        "title": "Fix a bug",
        "params": ["summary", "expected"],
        "goal": "Fix this bug: {summary}. Expected behaviour: {expected}. Add or update a test that fails before the fix.",
        "suggestedChecks": ["run the project's tests"],
    },
    "add-tests": {
        "title": "Add tests",
        "params": ["target"],
        "goal": "Add tests for {target}. Do not change production code unless a test reveals a defect; report any defect found.",
        "suggestedChecks": ["run the new tests", "run the full suite"],
    },
    "refactor": {
        "title": "Refactor safely",
        "params": ["target", "goal"],
        "goal": "Refactor {target} to {goal} without changing behaviour. Existing tests must keep passing.",
        "suggestedChecks": ["run the full suite before and after"],
    },
    "upgrade-dependency": {
        "title": "Upgrade a dependency",
        "params": ["package", "version"],
        "goal": "Upgrade {package} to {version}, fix resulting breakages and summarise the changelog risks.",
        "suggestedChecks": ["install from lockfile", "run the full suite"],
    },
    "write-docs": {
        "title": "Write documentation",
        "params": ["topic"],
        "goal": "Document {topic} for a new contributor. Verify every command you document.",
        "suggestedChecks": ["run each documented command"],
    },
}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def render_template(template_id: str, values: dict[str, Any]) -> dict[str, Any]:
    t = TEMPLATES.get(template_id)
    if t is None:
        raise LaneError("TEMPLATE_NOT_FOUND", "unknown task template", 404)
    clean: dict[str, str] = {}
    for name in t["params"]:
        raw = values.get(name)
        if not isinstance(raw, str) or not raw.strip():
            raise LaneError("INVALID_REQUEST", f"parameter '{name}' is required", 422)
        text = " ".join(_CTRL.sub(" ", raw).split())
        if len(text) > 300:
            raise LaneError("INVALID_REQUEST", f"parameter '{name}' exceeds 300 characters", 422)
        clean[name] = text
    extra = set(values) - set(t["params"])
    if extra:
        raise LaneError("INVALID_REQUEST", f"unknown parameters: {sorted(extra)}", 422)
    # Parameters are quoted data, never instructions: the template wraps them verbatim.
    return {
        "templateId": template_id,
        "title": t["title"],
        "goal": t["goal"].format(**clean),
        "suggestedChecks": t["suggestedChecks"],
        "startsRun": False,
    }


# ---- F131 onboarding (derived, not self-reported) ---------------------------------------------

STEPS = [
    ("tutorial", "Create the tutorial project"),
    ("session", "Open a code session"),
    ("readiness", "Check repository readiness"),
    ("review", "Review and apply a change"),
    ("verify", "Record verification evidence"),
]


def onboarding(facts: dict[str, bool]) -> dict[str, Any]:
    steps = [{"id": sid, "label": label, "done": bool(facts.get(sid))} for sid, label in STEPS]
    nxt = next((s["id"] for s in steps if not s["done"]), None)
    return {"steps": steps, "next": nxt, "complete": nxt is None}
