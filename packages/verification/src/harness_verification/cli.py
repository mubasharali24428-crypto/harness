"""harness-verify: every tool is callable on its own (no other session/agent needed)."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import contract, discovery, mutation, repair, scorecard, testgen


def _changed(root: Path, explicit: list[str] | None, base: str | None) -> list[str]:
    if explicit:
        return explicit
    if base:
        import subprocess
        out = subprocess.run(["git", "diff", "--name-only", base], cwd=root, capture_output=True,
                             text=True, check=True).stdout
        return [x for x in out.splitlines() if x]
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness-verify")
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "scorecard"):
        p = sub.add_parser(name)
        p.add_argument("--changed", nargs="*")
        p.add_argument("--base")
    sub.choices["scorecard"].add_argument("--no-mutation", action="store_true")
    m = sub.add_parser("mutate")
    m.add_argument("--sources", nargs="+", required=True)
    m.add_argument("--tests", nargs="+", required=True)
    m.add_argument("--max-mutants", type=int, default=30)
    g = sub.add_parser("gen-tests")
    g.add_argument("--source", required=True)
    g.add_argument("--tests", nargs="*", default=[])
    w = sub.add_parser("weakening")
    w.add_argument("before")
    w.add_argument("after")
    c = sub.add_parser("openapi-drift")
    c.add_argument("--spec", required=True)
    c.add_argument("--app", required=True)
    c.add_argument("--path", action="append", default=[])
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    if a.cmd == "discover":
        r = discovery.discover(root, _changed(root, a.changed, a.base))
        print(json.dumps(asdict(r), indent=2))
        return 0
    if a.cmd == "mutate":
        r = mutation.run_mutation(root, a.sources, a.tests, max_mutants=a.max_mutants)
        print(json.dumps({"status": r.status, "score": r.score, "note": r.note, "survivors": [
            asdict(x.mutant) for x in r.survived]}, indent=2))
        return 0 if r.status == "executed" else 2
    if a.cmd == "scorecard":
        s = scorecard.build_scorecard(root, _changed(root, a.changed, a.base),
                                      run_mutation_check=not a.no_mutation)
        print(s.to_json())
        return {"pass": 0, "pass_weak": 0, "insufficient_evidence": 3, "fail": 1}[s.verdict]
    if a.cmd == "gen-tests":
        gen = testgen.CharacterizationGenerator()
        out = []
        for fn in testgen.list_functions(root, a.source):
            for prop in gen.propose(root, fn):
                ev = testgen.evaluate(root, prop, a.tests)
                out.append({"proposal": asdict(prop), "evaluation": asdict(ev)})
        print(json.dumps(out, indent=2))
        return 0
    if a.cmd == "weakening":
        v = repair.weakening_violations(Path(a.before).read_text(), Path(a.after).read_text())
        print(json.dumps({"violations": v}))
        return 1 if v else 0
    if a.cmd == "openapi-drift":
        spec = Path(a.spec) if Path(a.spec).is_absolute() else root / a.spec
        d = contract.check_openapi(spec, a.app, a.path)
        print(json.dumps(asdict(d), indent=2))
        return 0 if d.clean else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
