#!/usr/bin/env python3
"""Live gates: a multi-file refactor, and a regression test the agent writes.

Two model-driven tasks on small generated Python repositories. The verdict is the
disk, never the agent's answer: a verifier that only uses the standard library
runs inside the orchestrator container after the run and inspects a scratch copy
of the workspace.

* ``refactor`` -- rename ``shop.pricing.calc_price`` to ``line_total`` and make its
  discount keyword-only, across four modules (one imports it under an alias), while
  the vendored lookalike under ``vendor/`` and ``tests/`` stay byte-identical. A
  verifier-owned check that the agent never sees pins the new API.
* ``tests-written`` -- a planted ``page_count`` bug. The agent's new test must fail
  on the original source with an assertion (not an import error), pass on the
  agent's fix, and the fix must satisfy a verifier-owned reference check.

    python3 scripts/eval/tasks/code_change_task.py --task refactor|tests-written
    python3 scripts/eval/tasks/code_change_task.py --selftest   # offline, no stack
"""

from __future__ import annotations

# --- verifier block begin: stdlib only. Its text is shipped into the orchestrator container.
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SHOP = {
    "README.md": (
        "# shop\n\nPricing lives in `shop/pricing.py`: `calc_price(qty, unit_price, "
        "discount_pct)` prices one line.\n`vendor/` is third-party code and is not "
        "ours to edit.\n"),
    "shop/__init__.py": '"""Tiny shop library."""\n',
    "shop/pricing.py": '''"""Line pricing."""


def calc_price(qty, unit_price, discount_pct=0):
    """Total for one order line, rounded to cents. discount_pct is 0-100."""
    return round(qty * unit_price * (1 - discount_pct / 100), 2)
''',
    "shop/cart.py": '''"""Cart totals."""

from shop.pricing import calc_price


def cart_total(lines):
    """Sum of (qty, unit_price, discount_pct) lines."""
    return round(sum(calc_price(qty, unit, disc) for qty, unit, disc in lines), 2)
''',
    "shop/invoice.py": '''"""Invoice lines."""

import shop.pricing as pricing


def invoice_lines(lines):
    """One printable row per line: (label, amount)."""
    rows = []
    for index, (qty, unit, disc) in enumerate(lines, start=1):
        rows.append((f"line {index}", pricing.calc_price(qty, unit, disc)))
    return rows
''',
    "shop/report.py": '''"""Sales report."""

from shop.pricing import calc_price as price


def biggest_line(lines):
    """Largest single line amount, or 0 when there are no lines."""
    return max((price(qty, unit, discount_pct=disc) for qty, unit, disc in lines),
               default=0)
''',
    "vendor/__init__.py": '"""Vendored third-party code."""\n',
    "vendor/legacy_pricing.py": '''# Vendored from a third-party package. Do not edit.


def calc_price(qty, unit_price, discount_pct=0):
    return qty * unit_price * (1 - discount_pct / 100)
''',
    "tests/test_shop.py": '''import unittest

from shop.cart import cart_total
from shop.invoice import invoice_lines
from shop.report import biggest_line

LINES = [(2, 10.0, 0), (1, 20.0, 50), (3, 4.0, 25)]


class ShopTests(unittest.TestCase):
    def test_cart_total(self):
        self.assertEqual(cart_total(LINES), 39.0)

    def test_invoice_lines(self):
        self.assertEqual(invoice_lines(LINES),
                         [("line 1", 20.0), ("line 2", 10.0), ("line 3", 9.0)])

    def test_biggest_line(self):
        self.assertEqual(biggest_line(LINES), 20.0)

    def test_biggest_line_empty(self):
        self.assertEqual(biggest_line([]), 0)
''',
}

PAGER = {
    "pager/__init__.py": '"""Pagination helpers."""\n',
    "pager/paging.py": '''"""Pagination helpers."""


def page_count(total, size):
    """Number of pages needed for `total` items at `size` items per page."""
    if size <= 0:
        raise ValueError("size must be positive")
    return total // size


def page_slice(items, page, size):
    """The items on 1-based `page`."""
    if size <= 0 or page < 1:
        raise ValueError("size and page must be positive")
    start = (page - 1) * size
    return list(items[start:start + size])


def all_pages(items, size):
    """Every page in order; concatenated they equal `items`."""
    return [page_slice(items, n, size) for n in range(1, page_count(len(items), size) + 1)]
''',
    "tests/test_paging.py": '''import unittest

from pager.paging import all_pages, page_count, page_slice


class PagingTests(unittest.TestCase):
    def test_page_slice_middle(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5, 6], 2, 2), [3, 4])

    def test_page_slice_rejects_bad_size(self):
        with self.assertRaises(ValueError):
            page_slice([1], 1, 0)

    def test_exact_multiple(self):
        self.assertEqual(page_count(10, 5), 2)
        self.assertEqual(all_pages([1, 2, 3, 4], 2), [[1, 2], [3, 4]])

    def test_empty(self):
        self.assertEqual(page_count(0, 3), 0)
''',
}

HIDDEN_REFACTOR = '''
import inspect
import unittest

import shop.pricing as pricing
from shop.cart import cart_total
from shop.invoice import invoice_lines
from shop.report import biggest_line

LINES = [(1, 99.99, 5), (7, 0.1, 0), (2, 50.0, 100)]


class Hidden(unittest.TestCase):
    def test_new_name_and_values(self):
        self.assertEqual(pricing.line_total(3, 10.0, discount_pct=10), 27.0)
        self.assertEqual(pricing.line_total(2, 5.5), 11.0)

    def test_discount_is_keyword_only(self):
        with self.assertRaises(TypeError):
            pricing.line_total(3, 10.0, 10)
        param = inspect.signature(pricing.line_total).parameters["discount_pct"]
        self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(param.default, 0)

    def test_old_name_gone(self):
        self.assertFalse(hasattr(pricing, "calc_price"))

    def test_callers_price_the_same(self):
        self.assertEqual(cart_total(LINES), 95.69)
        self.assertEqual(invoice_lines(LINES),
                         [("line 1", 94.99), ("line 2", 0.7), ("line 3", 0.0)])
        self.assertEqual(biggest_line(LINES), 94.99)
'''

HIDDEN_PAGER = '''
import math
import unittest

from pager.paging import all_pages, page_count, page_slice


class Hidden(unittest.TestCase):
    def test_page_count_is_a_ceiling(self):
        for total in range(0, 26):
            for size in range(1, 13):
                self.assertEqual(page_count(total, size), math.ceil(total / size), (total, size))

    def test_pages_rebuild_the_input(self):
        for total in range(0, 26):
            for size in range(1, 13):
                items = list(range(total))
                pages = all_pages(items, size)
                self.assertEqual([x for page in pages for x in page], items, (total, size))
                self.assertTrue(all(len(page) == size for page in pages[:-1]), (total, size))

    def test_slice_and_errors_unchanged(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5, 6], 2, 2), [3, 4])
        self.assertEqual(page_slice([1, 2, 3], 5, 2), [])
        for bad in ((1, 0), (0, 2)):
            with self.assertRaises(ValueError):
                page_slice([1], *bad)
        with self.assertRaises(ValueError):
            page_count(3, 0)
'''

REFACTOR_ALLOWED = {"README.md", "shop/pricing.py", "shop/cart.py", "shop/invoice.py",
                    "shop/report.py"}
TESTS_ALLOWED = {"pager/paging.py", "tests/test_paging.py"}
BAD_FAILURE = re.compile(r"ImportError|ModuleNotFoundError|SyntaxError|NameError|AttributeError")


def snapshot(root: str) -> dict[str, bytes]:
    """Every file under root except bytecode caches: posix relative path -> bytes."""
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".pyc"):
                path = os.path.join(base, name)
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return out


def compare(root: str, seed: dict[str, str]) -> dict:
    now = snapshot(root)
    changed = sorted(r for r, t in seed.items()
                     if r in now and hashlib.sha256(now[r]).digest()
                     != hashlib.sha256(t.encode()).digest())
    diff = "".join(
        "".join(difflib.unified_diff(seed[r].splitlines(True),
                                     now[r].decode("utf-8", "replace").splitlines(True),
                                     f"a/{r}", f"b/{r}"))
        for r in changed)
    return {"changed": changed, "deleted": sorted(r for r in seed if r not in now),
            "added": sorted(r for r in now if r not in seed), "now": now, "diff": diff[:8000]}


def run_in_copy(root: str, overlay: dict[str, str], *args: str) -> tuple[int, str]:
    """Run ``python -m unittest ARGS`` in a scratch copy of root with overlay files."""
    with tempfile.TemporaryDirectory(prefix="verify-") as tmp:
        dest = os.path.join(tmp, "ws")
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for rel, text in overlay.items():
            path = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        try:
            proc = subprocess.run([sys.executable, "-m", "unittest", *args], cwd=dest,
                                  capture_output=True, text=True, timeout=120, check=False,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired:  # an agent's test may loop forever
            return 124, "TIMEOUT"
        return proc.returncode, proc.stdout + proc.stderr


def touched(cmp: dict, prefix: str) -> list[str]:
    return [r for r in cmp["changed"] + cmp["deleted"] + cmp["added"] if r.startswith(prefix)]


def verify_refactor(root: str) -> dict:
    cmp = compare(root, SHOP)
    rc_tests, out_tests = run_in_copy(root, {}, "discover", "-s", "tests")
    rc_hidden, out_hidden = run_in_copy(root, {"_hidden_check.py": HIDDEN_REFACTOR},
                                        "_hidden_check")
    stale = sorted(r for r, blob in cmp["now"].items()
                   if not r.startswith("vendor/") and b"calc_price" in blob)
    checks = {
        "shippedTestsPass": rc_tests == 0,
        "hiddenCheckPasses": rc_hidden == 0,
        "oldNameGoneOutsideVendor": not stale,
        "vendorUntouched": not touched(cmp, "vendor/"),
        "testsUntouched": not touched(cmp, "tests/"),
        "onlyAllowedFilesChanged": set(cmp["changed"]) <= REFACTOR_ALLOWED,
        "noSeededFileDeleted": not cmp["deleted"],
    }
    return {"checks": checks, "changed": cmp["changed"], "added": cmp["added"][:20],
            "stillMentionsOldName": stale, "diff": cmp["diff"],
            "shippedTestsTail": out_tests[-500:], "hiddenCheckTail": out_hidden[-500:]}


def verify_tests(root: str) -> dict:
    cmp = compare(root, PAGER)
    tests_touched = [r for r in cmp["changed"] + cmp["added"]
                     if r.startswith("tests/") and r.endswith(".py")]
    rc_bug, out_bug = run_in_copy(root, {"pager/paging.py": PAGER["pager/paging.py"]},
                                  "discover", "-s", "tests")
    rc_fix, out_fix = run_in_copy(root, {}, "discover", "-s", "tests")
    rc_ref, out_ref = run_in_copy(root, {"_hidden_check.py": HIDDEN_PAGER}, "_hidden_check")
    checks = {
        "testAddedOrChanged": bool(tests_touched),
        "failsOnOriginalSource": (rc_bug != 0 and "AssertionError" in out_bug
                                  and not BAD_FAILURE.search(out_bug)),
        "passesOnAgentSource": rc_fix == 0,
        "fixPassesReferenceCheck": rc_ref == 0,
        "sourceChanged": "pager/paging.py" in cmp["changed"],
        "onlyTestsAndSourceChanged": (set(cmp["changed"]) <= TESTS_ALLOWED
                                      and not [r for r in cmp["added"] if r.startswith("pager/")]),
        "noSeededFileDeleted": not cmp["deleted"],
    }
    return {"checks": checks, "changed": cmp["changed"], "added": cmp["added"][:20],
            "testFiles": tests_touched, "diff": cmp["diff"],
            "originalSourceTail": out_bug[-500:], "agentSourceTail": out_fix[-500:],
            "referenceTail": out_ref[-500:]}
# --- verifier block end

import argparse
import importlib.util
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"

REFACTOR_GOAL = """In this repository, rename shop.pricing.calc_price to line_total and make
its discount_pct parameter keyword-only, so the signature reads
    def line_total(qty, unit_price, *, discount_pct=0)
Update every caller in shop/ and every mention outside vendor/, so the old name
calc_price appears nowhere except inside vendor/. Behaviour must not change.
vendor/ is third-party code: do not edit it. Do not edit anything under tests/.
Run `python3 -m unittest discover -s tests` before you finish.
Reply with exactly one line:
DONE: <number of files you changed>
"""

TESTS_GOAL = """Bug report: all_pages([1, 2, 3, 4, 5], 2) in pager/paging.py silently drops the
last item, because page_count(5, 2) answers 2 instead of 3 (a partial last page
is still a page).
Do two things:
1. Add a regression test with the standard library unittest under tests/ that fails
   on the current code because of this bug (an assertion failure, not an import
   error or a typo).
2. Fix the bug in pager/paging.py so that your test and the existing tests pass.
Run `python3 -m unittest discover -s tests` to check. Do not edit any other file.
Reply with exactly one line:
TEST: <path of the test file you added or changed>
"""

TASKS = {
    "refactor": {"kind": "code-change-refactor", "files": SHOP, "goal": REFACTOR_GOAL,
                 "verify": "verify_refactor", "fn": verify_refactor, "max_calls": 40},
    "tests-written": {"kind": "code-change-tests-written", "files": PAGER, "goal": TESTS_GOAL,
                      "verify": "verify_tests", "fn": verify_tests, "max_calls": 40},
}


def reference_refactor() -> dict[str, str]:
    """A correct solution, built by editing the fixture text (selftest only)."""
    out = {rel: text.replace("calc_price", "line_total")
           for rel, text in SHOP.items() if not rel.startswith("vendor/") and "calc_price" in text}
    for rel, old, new in (
            ("shop/pricing.py", "def line_total(qty, unit_price, discount_pct=0)",
             "def line_total(qty, unit_price, *, discount_pct=0)"),
            ("shop/cart.py", "line_total(qty, unit, disc)", "line_total(qty, unit, discount_pct=disc)"),
            ("shop/invoice.py", "pricing.line_total(qty, unit, disc)",
             "pricing.line_total(qty, unit, discount_pct=disc)")):
        assert old in out[rel], (rel, old)
        out[rel] = out[rel].replace(old, new)
    return out


FIX = {"pager/paging.py": PAGER["pager/paging.py"].replace("return total // size",
                                                           "return -(-total // size)")}
GOOD_TEST = {"tests/test_regression.py": '''import unittest

from pager.paging import all_pages, page_count


class Regression(unittest.TestCase):
    def test_partial_last_page_is_a_page(self):
        self.assertEqual(page_count(5, 2), 3)
        self.assertEqual(all_pages([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
'''}


def judged(verify, files: dict[str, str], edits: dict[str, str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="selftest-") as tmp:
        for rel, text in {**files, **edits}.items():
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return verify(tmp)["checks"]


def failed(checks: dict) -> set[str]:
    return {k for k, v in checks.items() if not v}


def selftest() -> int:
    """The verifiers accept a correct solution and reject each plausible wrong one."""
    assert FIX["pager/paging.py"] != PAGER["pager/paging.py"]
    ref = reference_refactor()
    base = judged(verify_refactor, SHOP, {})
    assert failed(base) == {"hiddenCheckPasses", "oldNameGoneOutsideVendor"}, base  # tests green
    assert not failed(judged(verify_refactor, SHOP, ref))
    naive = {r: t.replace("calc_price", "line_total") for r, t in SHOP.items() if "calc_price" in t}
    assert {"vendorUntouched", "hiddenCheckPasses"} <= failed(judged(verify_refactor, SHOP, naive))
    only_def = {"shop/pricing.py": ref["shop/pricing.py"]}
    assert "shippedTestsPass" in failed(judged(verify_refactor, SHOP, only_def))
    edited = {**ref, "tests/test_shop.py": SHOP["tests/test_shop.py"] + "# touched\n"}
    assert failed(judged(verify_refactor, SHOP, edited)) == {"testsUntouched",
                                                             "onlyAllowedFilesChanged"}
    alias = {**ref, "shop/pricing.py": ref["shop/pricing.py"] + "calc_price = line_total\n"}
    assert "oldNameGoneOutsideVendor" in failed(judged(verify_refactor, SHOP, alias))

    base = judged(verify_tests, PAGER, {})  # seeded tests are green on the buggy code
    assert failed(base) == {"testAddedOrChanged", "failsOnOriginalSource",
                            "sourceChanged", "fixPassesReferenceCheck"}, base
    assert not failed(judged(verify_tests, PAGER, {**FIX, **GOOD_TEST}))
    seeded_edit = {**FIX, "tests/test_paging.py": PAGER["tests/test_paging.py"] + (
        "\n    def test_partial(self):\n        self.assertEqual(page_count(5, 2), 3)\n")}
    assert not failed(judged(verify_tests, PAGER, seeded_edit))
    trivial = {"tests/test_t.py": "import unittest\n\n\nclass T(unittest.TestCase):\n"
               "    def test_ok(self):\n        self.assertTrue(True)\n"}
    assert "failsOnOriginalSource" in failed(judged(verify_tests, PAGER, {**FIX, **trivial}))
    no_fix = failed(judged(verify_tests, PAGER, GOOD_TEST))
    assert {"passesOnAgentSource", "fixPassesReferenceCheck"} <= no_fix, no_fix
    assert "failsOnOriginalSource" in failed(judged(verify_tests, PAGER, FIX))
    broken = {"tests/test_b.py": "import unittest\n\n\nclass B(unittest.TestCase):\n"
              "    def test_b(self):\n        self.assertEqual(undefined_name, 1)\n"}
    assert {"failsOnOriginalSource", "passesOnAgentSource"} <= failed(
        judged(verify_tests, PAGER, {**FIX, **broken}))
    extra = {**FIX, **GOOD_TEST, "pager/helpers.py": "X = 1\n"}
    assert failed(judged(verify_tests, PAGER, extra)) == {"onlyTestsAndSourceChanged"}
    print(json.dumps({"verdict": "PASS", "selftest": True}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(TASKS), default="refactor")
    ap.add_argument("--timeout", type=float, default=1200.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    spec = importlib.util.spec_from_file_location(
        "cs", REPO / "scripts" / "eval" / "tasks" / "code_search_task.py")
    cs = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(cs)
    w10, w11 = cs.w10, cs.w11
    task = TASKS[args.task]
    stamp = int(time.time())
    run_id = f"w20-{args.task}-{stamp}"
    token = w11.tpl.login(f"w20-{stamp}@example.com")
    status, project = w11.tpl.http("POST", f"{w11.tpl.IDENTITY}/api/v1/projects", token=token,
                                   body={"name": f"w20 {args.task} {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")

    requested_model = os.environ.get("HARNESS_EVAL_MODEL", "").strip()
    model_pref: dict = {"requested": requested_model or None, "applied": None}
    if requested_model:  # same seam as code_search_task: --compare-model sets the team pref
        st, prefs = w11.tpl.http("PUT", f"{cs.MODELS}/api/v1/models/prefs", token=token,
                                 body={"primaryModel": requested_model,
                                       "reviewerModel": requested_model})
        if st != 200 or not isinstance(prefs, dict):
            raise SystemExit(f"model pref update failed: {st} {prefs!r}")
        model_pref.update(status=st, applied=prefs.get("primaryModel"))

    cs.seed_tree(run_id, {rel: text.encode() for rel, text in task["files"].items()})
    started = time.monotonic()
    result = w11.run_goal(run_id, token, project["id"], task["goal"],
                          max_tool_calls=task["max_calls"], timeout=args.timeout)
    duration = round(time.monotonic() - started, 2)
    calls = [c for c in w11.tpl.audit_tool_events(run_id) if c.get("type") == "tool_call"]
    _, engine_body = w11.api("GET", f"/runs/{run_id}/engine", token=token)
    text = Path(__file__).read_text(encoding="utf-8")
    shipped = text[text.index("# --- verifier block begin"):text.index("# --- verifier block end")]
    root = f"{w11.WORKSPACE_ROOT}/{run_id}"
    raw = w10.in_orchestrator(f"{shipped}\nprint(json.dumps({task['verify']}({root!r})))\n")
    verified = json.loads(raw.strip().splitlines()[-1])

    checks = {"runSettled": result.get("state") in w10.SETTLED, **verified.pop("checks"),
              "requestedModelApplied": (model_pref["applied"] == requested_model
                                        if requested_model else True)}
    evidence = {
        "schema": 1, "kind": task["kind"], "takenAt": datetime.now(UTC).isoformat(),
        "runId": run_id, "goal": task["goal"], "model": requested_model or "the stack DSH_MODEL",
        "modelPref": model_pref, "state": result.get("state"), "states": result.get("states"),
        "durationSeconds": duration, "toolCalls": len(calls),
        "toolsUsed": sorted({c.get("tool") or c.get("name") for c in calls}),
        "usage": (engine_body or {}).get("usage") if isinstance(engine_body, dict) else None,
        "answer": (result.get("answer") or "")[:1200], "verification": verified,
        "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{task['kind']}-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"], "failed": evidence["failedChecks"],
                      "changed": verified.get("changed"), "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
