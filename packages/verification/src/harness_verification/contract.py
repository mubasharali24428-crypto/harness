"""F048 (extension): contract conformance beyond the frozen-contract golden tests.

* OpenAPI drift: a committed spec vs the spec a live FastAPI app actually serves
  (paths, methods, response status codes).
* Golden fixtures: every JSON fixture must validate against its model/validator callable,
  and must be byte-stable under a round trip (so a silent field rename is caught).
Existing contract tests are not replaced; these checks are additional and callable alone.
"""
from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

HTTP = {"get", "post", "put", "patch", "delete", "head", "options"}


def _ops(spec: dict) -> dict[tuple[str, str], set[str]]:
    out = {}
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method in HTTP:
                out[(method.upper(), path)] = set(op.get("responses", {}))
    return out


@dataclass
class Drift:
    undocumented: list[str] = field(default_factory=list)    # served but not in committed spec
    stale: list[str] = field(default_factory=list)           # in committed spec but not served
    status_changes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.undocumented or self.stale or self.status_changes)


def diff_specs(committed: dict, live: dict) -> Drift:
    a, b = _ops(committed), _ops(live)
    d = Drift()
    d.undocumented = sorted(f"{m} {p}" for (m, p) in b.keys() - a.keys())
    d.stale = sorted(f"{m} {p}" for (m, p) in a.keys() - b.keys())
    for k in a.keys() & b.keys():
        if a[k] != b[k]:
            d.status_changes.append(f"{k[0]} {k[1]}: {sorted(a[k])} -> {sorted(b[k])}")
    d.status_changes.sort()
    return d


def live_spec(app_ref: str, extra_paths: list[str] | None = None) -> dict:
    """``module:attr`` -> ``attr.openapi()`` (attr may be an app, a router, or a factory)."""
    for p in extra_paths or []:
        if p not in sys.path:
            sys.path.insert(0, p)
    mod, _, attr = app_ref.partition(":")
    obj = getattr(importlib.import_module(mod), attr)
    if callable(obj) and not hasattr(obj, "openapi") and not hasattr(obj, "routes"):
        obj = obj()
    if not hasattr(obj, "openapi"):                     # a bare APIRouter
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(obj)
        obj = app
    return obj.openapi()


def check_openapi(committed_path: Path, app_ref: str, extra_paths: list[str] | None = None) -> Drift:
    return diff_specs(json.loads(committed_path.read_text()), live_spec(app_ref, extra_paths))


@dataclass
class GoldenResult:
    checked: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def check_golden(fixture_dir: Path, validators: dict[str, Callable[[dict], object]]) -> GoldenResult:
    """Fixture ``<kind>.<anything>.json`` is validated by ``validators[kind]``."""
    res = GoldenResult()
    for f in sorted(fixture_dir.glob("*.json")):
        kind = f.name.split(".")[0]
        if kind not in validators:
            res.failures.append(f"{f.name}: no validator registered for kind '{kind}'")
            continue
        res.checked += 1
        try:
            raw = json.loads(f.read_text())
            out = validators[kind](raw)
            dumped = out.model_dump(mode="json", by_alias=True, exclude_none=True) \
                if hasattr(out, "model_dump") else None
            if dumped is not None and _strip(dumped) != _strip(raw):
                res.failures.append(f"{f.name}: round trip changed the document")
        except Exception as e:  # noqa: BLE001 - any validation error is a finding
            res.failures.append(f"{f.name}: {type(e).__name__}: {str(e)[:160]}")
    return res


def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v
