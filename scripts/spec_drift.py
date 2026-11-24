"""P3.1: fail when the orchestrator's recorded OpenAPI specs or the UI drift from the code.

    section4/.venv/bin/python scripts/spec_drift.py           # --check (default), exit 1 on drift
    section4/.venv/bin/python scripts/spec_drift.py --write   # regenerate every recorded spec
(section3/5/6/8 fragments mirror the deployed router/sandbox/meeting/review apps; section10 the onboarding app.)

Source of truth is the FastAPI router in section4. Checked: (1) section4/openapi.json and
scripts/sdk-gen/specs/orchestrator.openapi.json list exactly the router's paths+methods;
(2) every /api/v1/orchestrator URL the UI (section7, non-test) calls matches a real route.
(3) Other services: every other /api/v1 URL the UI calls must appear in some recorded
sdk-gen spec, except KNOWN_UNSPECCED below (a ratchet: a NEW gap fails, and so does a gap
that got fixed but is still listed). Other services' specs are not regenerated from code
here (they need their own venvs); only the orchestrator's are.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "section4/src"), str(ROOT / "contracts/src"),
    str(ROOT / "section10/src"), str(ROOT / "section11/src"),
]
PREFIX = "/api/v1/orchestrator"
FRAGMENT = ROOT / "section4/openapi.json"
SDK_SPEC = ROOT / "scripts/sdk-gen/specs/orchestrator.openapi.json"


# UI-called URLs no code route serves. Empty: keep it that way (a new gap fails the gate).
KNOWN_UNSPECCED: set[str] = set()


def other_ui_calls() -> set[str]:
    found: set[str] = set()
    pat = re.compile(r"/api/v1/(?!orchestrator)[A-Za-z0-9_/\-${}().,]*")
    for f in (ROOT / "section7").rglob("*"):
        if f.suffix not in {".ts", ".tsx"} or set(f.parts) & {"node_modules", ".next", "e2e", "tests"}:
            continue
        if ".test." in f.name:
            continue
        for line in f.read_text(errors="ignore").splitlines():
            if line.lstrip().startswith(("//", "*", "/*")):
                continue  # URLs quoted in comments are not calls
            for m in pat.findall(line):
                url = re.sub(r"\$\{[^}]*\}", "{}", m)
                url = re.sub(r"\{[^}]*$", "", url).rstrip("$/.,)(")
                found.add(re.sub(r"(?<=[A-Za-z0-9])\{\}$", "", url))  # keys${qs} -> keys
    return found


def generated() -> dict:
    from fastapi import FastAPI
    from harness_orchestrator import (
        agent_tree, artifacts, assignment_plan, control, discussion, execution, exports, participation, plans,
        presence, search,
    )
    from harness_orchestrator.api import router
    from harness_orchestrator.demo import router as demo_router
    from harness_orchestrator.plan_gate import router as plan_gate_router
    from harness_orchestrator.runs_index import router as runs_index_router

    # The same routers deploy/runners/services.py app_orchestrator() mounts.
    app = FastAPI(title="harness-orchestrator", version="0.1.0")
    for r in (router, runs_index_router, plan_gate_router, demo_router, presence.router,
              participation.router, control.router, agent_tree.router, discussion.router,
              plans.router, execution.router, artifacts.router, search.router, exports.router,
              assignment_plan.router):
        app.include_router(r)
    return app.openapi()


# deploy app factory -> recorded sdk-gen spec. Built offline (engines are lazy, nothing connects).
SERVICES = {
    "identity": "identity", "github": "github", "models": "router",
    "sandbox": "sandbox", "meeting": "meeting", "review": "review",
}


ONBOARDING_SPEC = ROOT / "section10/openapi.json"
# section<N>/openapi.json fragment -> the deployed service (SERVICES key) it must mirror (paths+methods).
FRAGMENTS = {"section3": "router", "section5": "sandbox", "section6": "meeting", "section8": "review"}


def onboarding_generated() -> dict:
    from harness_onboarding.api import make_app

    return make_app().openapi()


def route_paths() -> set[str]:
    """Every path served by a deployed app (websockets included) or the section 10/11 routers."""
    from fastapi import FastAPI
    from harness_context.api import router as context_router
    from harness_onboarding.api import router as onboarding_router

    sys.path.insert(0, str(ROOT / "deploy/runners"))
    apps = [getattr(_services(), f"app_{n}")() for n in SERVICES]
    for r in (context_router, onboarding_router):
        a = FastAPI()
        a.include_router(r)
        apps.append(a)
    paths = {p for a in apps for p in a.openapi()["paths"]}
    # websockets are not in OpenAPI: take them from the identity router itself
    from fastapi.routing import APIWebSocketRoute
    from harness_identity.app import router as identity_router

    paths |= {r.path for r in identity_router.routes
              if isinstance(r, APIWebSocketRoute)}
    return paths


def _services():
    import os

    os.environ.setdefault("HARNESS_ALLOW_DEV_MASTER_KEY", "1")
    os.environ.setdefault("HARNESS_DATABASE_URL", "postgresql+asyncpg://x:x@127.0.0.1:1/x")
    sys.path.insert(0, str(ROOT / "deploy/runners"))
    import services

    return services


def generated_services() -> dict[str, dict]:
    import warnings

    services = _services()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {f: getattr(services, f"app_{n}")().openapi() for n, f in SERVICES.items()}


def ops(spec: dict, prefix: str = PREFIX) -> set[str]:
    return {
        f"{m.upper()} {p}"
        for p, item in spec["paths"].items()
        if p.startswith(prefix)
        for m in item
        if m in {"get", "post", "put", "patch", "delete"}
    }


def ui_calls() -> set[str]:
    found: set[str] = set()
    pat = re.compile(r"/api/v1/orchestrator[A-Za-z0-9_/\-${}().,]*")
    for f in (ROOT / "section7").rglob("*"):
        parts = set(f.parts)
        if f.suffix not in {".ts", ".tsx"} or parts & {"node_modules", ".next", "e2e", "tests"}:
            continue
        if ".test." in f.name:
            continue
        for m in pat.findall(f.read_text(errors="ignore")):
            url = re.sub(r"\$\{[^}]*\}", "{}", m)
            url = re.sub(r"\{[^}]*$", "", url).rstrip("$/.,)(")  # drop a cut-off ${...?query
            if url != PREFIX:
                found.add(url)
    return found


def matches(call: str, templates: set[str]) -> bool:
    segs = call.split("/")
    for t in templates:
        ts = t.split("/")
        if len(ts) == len(segs) and all(
            a == b or (a.startswith("{") and a.endswith("}")) or b == "{}"
            for a, b in zip(ts, segs, strict=True)
        ):
            return True
    return False


def dump(spec: dict) -> str:
    return json.dumps(spec, indent=1, sort_keys=True) + "\n"


def main() -> int:
    spec = generated()
    if "--write" in sys.argv:
        frag = json.loads(FRAGMENT.read_text())
        spec_frag = {**spec, "info": frag["info"]}
        FRAGMENT.write_text(dump(spec_frag))
        SDK_SPEC.write_text(dump(spec))
        old = json.loads(ONBOARDING_SPEC.read_text())
        ONBOARDING_SPEC.write_text(dump({**onboarding_generated(), "info": old["info"]}))
        for f, sp in generated_services().items():
            (ROOT / f"scripts/sdk-gen/specs/{f}.openapi.json").write_text(dump(sp))
        gen = generated_services()
        for sec, f in FRAGMENTS.items():
            p = ROOT / sec / "openapi.json"
            p.write_text(dump({**gen[f], "info": json.loads(p.read_text())["info"]}))
        print("rewrote section3/4/5/6/8/10 openapi.json and the sdk-gen specs (orchestrator + 6 services);")
        print("now run: python scripts/sdk-gen/generate.py [--target ts]")
        return 0
    want, bad = ops(spec), []
    for f in (FRAGMENT, SDK_SPEC):
        have = ops(json.loads(f.read_text()))
        for label, diff in (("missing", want - have), ("stale", have - want)):
            if diff:
                bad.append(f"{f.name} {label}: {sorted(diff)}")
    for f, sp in generated_services().items():
        rec = json.loads((ROOT / f"scripts/sdk-gen/specs/{f}.openapi.json").read_text())
        for label, diff in (("missing", ops(sp, "/") - ops(rec, "/")), ("stale", ops(rec, "/") - ops(sp, "/"))):
            if diff:
                bad.append(f"{f}.openapi.json {label}: {sorted(diff)}")
    gen = generated_services()
    for sec, f in FRAGMENTS.items():
        rec = ops(json.loads((ROOT / sec / "openapi.json").read_text()), "/")
        for label, diff in (("missing", ops(gen[f], "/") - rec), ("stale", rec - ops(gen[f], "/"))):
            if diff:
                bad.append(f"{sec}/openapi.json {label}: {sorted(diff)}")
    rec = ops(json.loads(ONBOARDING_SPEC.read_text()), "/")
    for label, diff in (("missing", ops(onboarding_generated(), "/") - rec),
                        ("stale", rec - ops(onboarding_generated(), "/"))):
        if diff:
            bad.append(f"section10/openapi.json {label}: {sorted(diff)}")
    templates = {p for p in spec["paths"] if p.startswith(PREFIX)}
    for call in sorted(ui_calls()):
        if not matches(call, templates):
            bad.append(f"UI calls a route that does not exist: {call}")
    recorded = route_paths()
    # a base-URL helper (`/api/v1/policy/orgs/${id}` + a subpath added at the call site) is the
    # parent of a recorded route; anything that is neither a route nor a route's parent is a gap
    parents = {t.rsplit("/", 1)[0] for t in recorded}
    unmatched = {u for u in other_ui_calls() if not matches(u, recorded | parents)}
    for u in sorted(unmatched - KNOWN_UNSPECCED):
        bad.append(f"UI calls a URL no recorded spec has (new gap): {u}")
    for u in sorted(KNOWN_UNSPECCED - unmatched):
        bad.append(f"KNOWN_UNSPECCED entry is now covered or unused, remove it: {u}")
    print("\n".join(bad) or "orchestrator specs and UI calls match the router.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
