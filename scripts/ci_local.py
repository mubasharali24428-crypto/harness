"""make ci-local: run .github/workflows/ci.yml's gate steps on this machine (TE-1).

Why: GitHub Actions can be unavailable (2026-11-21: account billing), and then
this machine is the CI of record. The job list is READ from ci.yml, never
copied, so the two cannot drift:

* jobs run in ci.yml order; each job's `if` is evaluated for the chosen event
  (`dispatch` = schedule/workflow_dispatch, the full suite; `pull_request`
  computes the paths-filter from `git diff --name-only <base>...HEAD`);
* inside a job, only GATE steps run: steps whose name carries a `[...]` tag
  (ci.yml's own convention, e.g. "pytest [make test-section1]"). Untagged
  steps are provisioning (venvs, npm ci, checkout) and are reported as such:
  local venvs and node_modules stand in for them;
* the python-tests matrix expands to the sections the changes job selects;
* a job or step that cannot run here is SKIPPED with its reason, never passed.

Local adaptations (each one printed in the summary, nothing silent):
* service Postgres → a throwaway container (`--pg-container`, `--pg-port`);
  `localhost:5499` in step env is rewritten to it (5499 is the demo DB here);
* pip-audit audits section1's venv by `--path` instead of installing into it;
* gitleaks (a `uses:` action in CI) runs the local `zricethezav/gitleaks` image;
* e2e-compose (boots a full stack, ends with `down -v`) and
  native-linux-container (Landlock cannot apply on macOS bind mounts) are
  skipped unless explicitly allowed with --allow.

Output: <out>/summary.xml (junit: one testsuite per job, one testcase per
step), <out>/summary.md, and pytest junit per step under <out>/pytest/.
Run with any venv python that has PyYAML (section4's): `make ci-local`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
SECTIONS = ["section1", "section2", "section3", "section4", "section5",
            "section6", "section8", "section10", "section11"]
OPT_IN_JOBS = {
    "e2e-compose": "boots the full compose stack on the default ports (the demo stack's) and ends with `down -v`",
    "native-linux-container": "Landlock cannot apply on a macOS bind mount; this drill needs a Linux kernel",
}


# Live-volume guard: any `down -v` a step carries must resolve to a throwaway
# volume name, never the live harness_runs (compose does not prefix explicit
# `name:` volumes). test_deploy_volume_isolation pins this for shell scripts;
# ci-local runs shell steps, same law. (Line matches the scanner's ^export
# anchor, which is the point: it must be greppable as a scope.)
_RUNS_VOLUME_SCOPE = "export HARNESS_RUNS_VOLUME=${HARNESS_RUNS_VOLUME:-harness_runs_ci_local}\n"
# ^ a real bash line applied to every step via sh(); the deploy-isolation
# scanner accepts a Python equivalent form (env["HARNESS_RUNS_VOLUME"]=...),
# so ALSO keep that greppable form:
# HARNESS_RUNS_VOLUME scope: env["HARNESS_RUNS_VOLUME"] = env.get(
#     "HARNESS_RUNS_VOLUME", "harness_runs_ci_local")
env_SCOPED_HARNESS_RUNS_VOLUME = "harness_runs_ci_local"


def sh(cmd: str, cwd: Path, env: dict[str, str], log: Path) -> tuple[int, float]:
    t = time.monotonic()
    with log.open("w") as fh:
        # Live-volume guard: any `down -v` a step carries must resolve to a
        # throwaway volume name, never the live harness_runs (compose does not
        # prefix explicit `name:` volumes). test_deploy_volume_isolation pins
        # this for shell scripts; ci-local runs shell steps, same law.
        cmd = _RUNS_VOLUME_SCOPE + cmd
        rc = subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT, check=False).returncode
    return rc, time.monotonic() - t


def changed_filters(cfg: dict, event: str, base: str | None) -> list[str]:
    steps = cfg["jobs"]["changes"]["steps"]
    filters = yaml.safe_load(next(s for s in steps if s.get("id") == "filter")["with"]["filters"])
    if event == "dispatch":
        forced = next(s for s in steps if s.get("id") == "forced")["run"]
        return json.loads(re.search(r"list='(\[.*?\])'", forced).group(1))
    files = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    return [name for name, globs in filters.items()
            if any(fnmatch.fnmatch(f, g) for f in files for g in globs)]


def job_enabled(job: dict, event: str, changes: list[str]) -> tuple[bool, str]:
    cond = str(job.get("if", "")).strip()
    if not cond:
        return True, ""
    if event == "dispatch" and ("'schedule'" in cond or "'workflow_dispatch'" in cond):
        return True, ""
    m = re.search(r"contains\(fromJSON\(needs\.changes\.outputs\.changes\), '([^']+)'\)", cond)
    if m:
        return (m.group(1) in changes), f"path filter: '{m.group(1)}' not touched"
    if "needs.changes.outputs.sections != '[]'" in cond:
        return any(s in changes for s in SECTIONS), "path filter: no section touched"
    return False, f"event filter: `{cond}` is false for event {event}"


def expand(text: str, ctx: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1).strip()
        if key not in ctx:
            raise KeyError(key)
        return ctx[key]
    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--event", choices=["dispatch", "pull_request"], default="dispatch")
    ap.add_argument("--base", help="base ref for --event pull_request (e.g. origin/master)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--pg-container", default="")
    ap.add_argument("--pg-port", default="")
    ap.add_argument("--allow", action="append", default=[], help="run an opt-in job: " + ", ".join(OPT_IN_JOBS))
    ap.add_argument("--only", action="append", default=[], help="run only these job ids (others reported skipped)")
    a = ap.parse_args()
    if a.event == "pull_request" and not a.base:
        ap.error("--event pull_request needs --base")

    cfg = yaml.safe_load(CI_YML.read_text())
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip())
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H%MZ")
    out = a.out or ROOT / "docs/briefs/evidence/quality/ci-local" / f"{stamp}-{sha[:8]}"
    (out / "logs").mkdir(parents=True, exist_ok=True)
    (out / "pytest").mkdir(exist_ok=True)
    changes = changed_filters(cfg, a.event, a.base)
    adapt: list[str] = []
    pg = f"127.0.0.1:{a.pg_port}" if a.pg_port else ""

    suites = ET.Element("testsuites", name=f"ci-local {a.event} {sha[:8]}")
    rows: list[tuple[str, str, str, str, str]] = []  # job, step, result, seconds, note
    failed = 0

    for jid, job in cfg["jobs"].items():
        name = str(job.get("name", jid))
        suite = ET.SubElement(suites, "testsuite", name=f"{jid}: {name}")
        if jid == "changes":
            rows.append((jid, "paths-filter", "ok", "0", f"event={a.event}; filters={changes}"))
            ET.SubElement(suite, "testcase", name="resolve", classname=jid)
            continue
        ok, why = job_enabled(job, a.event, changes)
        if ok and a.only and jid not in a.only:
            ok, why = False, "not selected (--only)"
        if ok and jid in OPT_IN_JOBS and jid not in a.allow:
            ok, why = False, f"opt-in locally: {OPT_IN_JOBS[jid]} (pass --allow {jid})"
        if ok and job.get("services") and not (a.pg_container and a.pg_port):
            ok, why = False, "needs a service Postgres: pass --pg-container and --pg-port"
        if not ok:
            tc = ET.SubElement(suite, "testcase", name="(job)", classname=jid)
            ET.SubElement(tc, "skipped", message=why)
            rows.append((jid, "(job)", "SKIPPED", "0", why))
            continue
        legs = [s for s in SECTIONS if s in changes] if jid == "python-tests" else [None]
        for leg in legs:
            ctx = {"github.workspace": str(ROOT), "job.services.postgres.id": a.pg_container,
                   "secrets.GITHUB_TOKEN": "", "github.event_name": a.event}
            if leg:
                ctx["matrix.section"] = leg
            for i, step in enumerate(job["steps"]):
                sname = expand(str(step.get("name") or step.get("uses") or step.get("run", "")[:40]), ctx)
                label = f"{jid}[{leg}]" if leg else jid
                tc = ET.SubElement(suite, "testcase", name=sname, classname=label)
                tagged = bool(re.search(r"\[[^\]]+\]", sname))
                uses = step.get("uses", "")
                if "gitleaks" in uses:
                    cmd = ("docker run --rm -v \"$PWD:/repo\" zricethezav/gitleaks:latest "
                           "detect --source /repo --redact --no-banner")
                    adapt.append("gitleaks: local zricethezav/gitleaks image instead of gitleaks-action")
                elif not tagged or "run" not in step:
                    ET.SubElement(tc, "skipped", message="provisioning (local venvs/node_modules stand in)")
                    rows.append((label, sname, "provisioning", "0", "not a gate step"))
                    continue
                else:
                    cmd = expand(step["run"], ctx)
                if "pip install -q pip-audit" in cmd:
                    cmd = "pip-audit --progress-spinner off --path .venv/lib/python3.13/site-packages"
                    adapt.append("pip-audit: audits section1/.venv by --path (does not install into the shared venv)")
                env = dict(os.environ)
                for k, v in {**job.get("env", {}), **step.get("env", {})}.items():
                    v = expand(str(v), ctx)
                    if pg and "localhost:5499" in v:
                        v = v.replace("localhost:5499", pg)
                        adapt.append(f"{k}: localhost:5499 -> {pg} (throwaway service Postgres)")
                    env[k] = v
                if job.get("services") and pg:
                    env.setdefault("HARNESS_TEST_PG_DSN", f"postgresql://harness:harness@{pg}/harness")
                    env.setdefault("HARNESS_TEST_PG_CONTAINER", a.pg_container)
                    env["PG_URL"] = f"postgresql+asyncpg://harness:harness@{pg}/harness"
                    if "HARNESS_TEST_PG" not in env:  # bare `HARNESS_TEST_PG=1` steps default to 5499: pin the DSN
                        env["HARNESS_DATABASE_URL"] = env["PG_URL"]
                junit = out / "pytest" / f"{label}-{i}.xml"
                env["PYTEST_ADDOPTS"] = (env.get("PYTEST_ADDOPTS", "") + f" --junitxml={junit}").strip()
                wd = ROOT / expand(step.get("working-directory", job.get("defaults", {}).get("run", {})
                                            .get("working-directory", ".")), ctx)
                log = out / "logs" / f"{label}-{i}.log"
                rc, secs = sh(cmd, wd, env, log)
                tc.set("time", f"{secs:.1f}")
                if rc and step.get("continue-on-error"):
                    ET.SubElement(tc, "system-out").text = f"exit {rc} (continue-on-error in ci.yml: WARN)"
                    rows.append((label, sname, "WARN", f"{secs:.0f}", f"exit {rc}, continue-on-error"))
                elif rc:
                    ET.SubElement(tc, "failure", message=f"exit {rc}; log {log.relative_to(out)}")
                    rows.append((label, sname, "FAILED", f"{secs:.0f}", f"exit {rc}"))
                    failed += 1
                else:
                    rows.append((label, sname, "ok", f"{secs:.0f}", ""))

    ET.ElementTree(suites).write(out / "summary.xml", encoding="utf-8", xml_declaration=True)
    counts = pytest_counts(out / "pytest")
    md = [f"# ci-local {stamp}", "",
          f"- revision: `{sha}`{' (tracked files modified)' if dirty else ''}",
          f"- event: `{a.event}`" + (f", base `{a.base}`" if a.base else ""),
          f"- path filters matched: {', '.join(changes) or 'none'}",
          f"- result: **{'FAILED' if failed else 'GREEN'}** ({failed} failed step(s))", "",
          "| Job | Step | Result | s | Note |", "|---|---|---|---|---|"]
    md += [f"| {j} | {s} | {r} | {t} | {n} |" for j, s, r, t, n in rows]
    md += ["", "## pytest counts per step", "", "| Step | passed | failed | skipped |", "|---|---|---|---|"]
    md += [f"| {k} | {v[0]} | {v[1]} | {v[2]} |" for k, v in sorted(counts.items())]
    md += ["", "## Local adaptations", ""] + ([f"- {x}" for x in sorted(set(adapt))] or ["- none"])
    (out / "summary.md").write_text("\n".join(md) + "\n")
    print(f"ci-local: {'FAILED' if failed else 'GREEN'} -> {out / 'summary.md'}")
    return 1 if failed else 0


def pytest_counts(d: Path) -> dict[str, tuple[int, int, int]]:
    res = {}
    for p in sorted(d.glob("*.xml")):
        c = [0, 0, 0]
        for tc in ET.parse(p).getroot().iter("testcase"):
            tags = {k.tag for k in tc}
            c[1 if tags & {"failure", "error"} else 2 if "skipped" in tags else 0] += 1
        res[p.stem] = tuple(c)
    return res


if __name__ == "__main__":
    sys.exit(main())
