"""Independently hosted telemetry dashboard (F113/F114/F115/F117 surface).

A Starlette app serving JSON APIs + a dependency-free HTML page. Auth follows
the house pattern: ``app.state.get_principal`` must be wired by the deploy
assembly; unwired = 503 fail closed. Team scoping: non-admin principals see
only their own team; admins may request any team or org-wide. A non-admin
requesting someone else's team gets 403, never silent filtering.

Run standalone:
    uvicorn --factory harness_telemetry.dashboard:app_factory
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .archive import Archive
from .metrics import (
    RunRecord,
    build_records,
    cost_metrics,
    productivity_metrics,
    quality_metrics,
)

_ADMIN_ROLES = frozenset({"admin", "owner"})

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Harness telemetry</title>
<style>
 body{font-family:ui-monospace,Menlo,monospace;margin:2rem;background:#0e1116;color:#e6edf3}
 h1{font-size:1.2rem} table{border-collapse:collapse;margin:.6rem 0}
 td,th{border:1px solid #30363d;padding:.3rem .7rem;text-align:left}
 .tabs button{margin-right:.4rem;background:#21262d;color:#e6edf3;border:1px solid #30363d;
  padding:.4rem .8rem;cursor:pointer} .tabs button.active{background:#1f6feb}
 .defs{color:#8b949e;font-size:.8rem;white-space:pre-wrap;margin-top:.8rem}
</style></head><body>
<h1>Harness telemetry</h1>
<div class="tabs">
 <button data-p="quality" class="active">Quality</button>
 <button data-p="productivity">Productivity</button>
 <button data-p="cost">Cost</button>
 <button data-p="archive">Archive</button>
</div>
<div id="out">loading…</div>
<script>
const out = document.getElementById('out');
async function show(p){
  const r = await fetch(`/api/v1/telemetry/${p}`);
  const j = await r.json();
  let rows;
  if (p === 'archive') {
    rows = '<tr><th>runId</th><th>team</th><th>outcome</th><th>pr</th><th>cost</th><th>goal</th></tr>'
      + j.runs.map(x => `<tr><td>${x.runId}</td><td>${x.teamId ?? ''}</td><td>${x.outcome}</td>`
      + `<td>${x.prId ?? ''}</td><td>${x.costUsd}</td><td>${(x.goal||'').slice(0,60)}</td></tr>`).join('');
  } else {
    rows = Object.entries(j).filter(([k]) => k !== 'definitions')
      .map(([k,v]) => `<tr><td>${k}</td><td>${typeof v === 'object' ? JSON.stringify(v) : v}</td></tr>`).join('');
  }
  out.innerHTML = `<table>${rows}</table>`
    + (j.definitions ? `<div class="defs">${Object.values(j.definitions).join('\\n')}</div>` : '');
}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); show(b.dataset.p);
});
show('quality');
</script></body></html>
"""


def make_dashboard_app(
    *,
    archive: Archive,
    records: list[RunRecord],
    as_of: datetime | None = None,
    get_principal_dep=None,
) -> Starlette:
    fixed_as_of = as_of

    async def _principal_or_error(request: Request):
        dep = getattr(request.app.state, "get_principal", None)
        if dep is None:
            return None, _error(503, "AUTH_NOT_CONFIGURED", "principal dependency not wired")
        principal = dep(request)
        if inspect.isawaitable(principal):
            principal = await principal
        if principal is None:
            return None, _error(401, "UNAUTHENTICATED", "no principal")
        return principal, None

    def _team_scope(request: Request, principal) -> tuple[str | None, JSONResponse | None]:
        is_admin = principal.role in _ADMIN_ROLES
        requested = request.query_params.get("team")
        if requested is not None and not is_admin and requested != principal.teamId:
            return None, _error(403, "TEAM_FORBIDDEN", "cannot view another team")
        if requested is not None:
            return requested, None
        return (None if is_admin else principal.teamId), None

    def _window(request: Request):
        def parse(name: str):
            raw = request.query_params.get(name)
            return datetime.fromisoformat(raw).replace(tzinfo=UTC) if raw else None

        return parse("from"), parse("to")

    def _metrics_endpoint(compute):
        async def handler(request: Request):
            principal, err = await _principal_or_error(request)
            if err:
                return err
            team, terr = _team_scope(request, principal)
            if terr:
                return terr
            from_, to = _window(request)
            return JSONResponse(compute(team, from_, to))

        return handler

    async def archive_search(request: Request):
        principal, err = await _principal_or_error(request)
        if err:
            return err
        team, terr = _team_scope(request, principal)
        if terr:
            return terr
        is_admin = principal.role in _ADMIN_ROLES
        visible = None if (is_admin and team is None) else {team}
        from_, to = _window(request)
        return JSONResponse(
            archive.search(
                visible_teams=visible,
                q=request.query_params.get("q"),
                team=team,
                outcome=request.query_params.get("outcome"),
                from_=from_,
                to=to,
                limit=int(request.query_params.get("limit", 50)),
                offset=int(request.query_params.get("offset", 0)),
            )
        )

    async def run_detail(request: Request):
        principal, err = await _principal_or_error(request)
        if err:
            return err
        is_admin = principal.role in _ADMIN_ROLES
        visible = None if is_admin else {principal.teamId}
        run = archive.get_run(request.path_params["runId"], visible_teams=visible)
        if run is None:
            return _error(404, "RUN_NOT_FOUND", "unknown or not visible")
        return JSONResponse(run)

    async def healthz(request: Request):
        return JSONResponse({"status": "ok"})

    async def index(request: Request):
        return HTMLResponse(_PAGE)

    quality = _metrics_endpoint(
        lambda team, from_, to: quality_metrics(
            records, as_of=fixed_as_of or datetime.now(UTC), from_=from_, to=to, team=team
        )
    )
    productivity = _metrics_endpoint(
        lambda team, from_, to: productivity_metrics(records, from_=from_, to=to, team=team)
    )
    cost = _metrics_endpoint(
        lambda team, from_, to: cost_metrics(records, from_=from_, to=to, team=team)
    )

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/healthz", healthz),
            Route("/api/v1/telemetry/quality", quality),
            Route("/api/v1/telemetry/productivity", productivity),
            Route("/api/v1/telemetry/cost", cost),
            Route("/api/v1/telemetry/archive/search", archive_search),
            Route("/api/v1/telemetry/archive/runs/{runId}", run_detail),
        ]
    )
    if get_principal_dep is not None:
        app.state.get_principal = get_principal_dep
    return app


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def app_factory() -> Starlette:  # pragma: no cover - deployment entrypoint
    """Env-driven standalone boot: TELEMETRY_DB path + TELEMETRY_FIXTURES=1 demo."""
    import os

    from . import fixtures
    from .events import parse_cost, parse_stream

    archive = Archive(os.environ.get("TELEMETRY_DB", "telemetry-archive.sqlite"))
    records: list[RunRecord] = []
    if os.environ.get("TELEMETRY_FIXTURES") == "1":
        events = parse_stream(fixtures.events())
        costs = [parse_cost(c) for c in fixtures.costs()]
        archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
        records = build_records(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    return make_dashboard_app(archive=archive, records=records, as_of=fixtures.AS_OF)
