"""Combined ops service: usage API, webhook registry, alerts, evidence export,
CI intake, chat actions, schedules. Same auth pattern as the S2/S4 surfaces —
``app.state.get_principal`` wired at deploy assembly, unwired = 503 fail
closed, non-admins pinned to their team.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import httpx
from harness_workflow_connectors.core import IntakeRejected
from harness_workflow_connectors.plan_gate import PlanGateError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .alerts import AlertEngine, evaluate_alerts
from .chat import ActionTokenError, SlackActionHandler
from .ci import CIIntake
from .evidence import build_evidence
from .schedule import ScheduleError, run_due_schedules, validate_spec
from .usage import (
    UsageWebhookError,
    dispatch_event,
    register_webhook,
    usage_snapshot,
)

_ADMIN_ROLES = frozenset({"admin", "owner"})


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def make_ops_app(
    *,
    store,
    records_provider,
    archive=None,
    ci_intake: CIIntake | None = None,
    ci_adapters: dict | None = None,
    slack_handler: SlackActionHandler | None = None,
    schedule_gate=None,
    http: httpx.AsyncClient | None = None,
    now_fn=None,
    get_principal_dep=None,
) -> Starlette:
    now_fn = now_fn or (lambda: datetime.now(UTC))
    alerts = AlertEngine(store)

    async def principal_or_error(request: Request):
        dep = getattr(request.app.state, "get_principal", None)
        if dep is None:
            return None, _error(503, "AUTH_NOT_CONFIGURED", "principal dependency not wired")
        principal = dep(request)
        if inspect.isawaitable(principal):
            principal = await principal
        if principal is None:
            return None, _error(401, "UNAUTHENTICATED", "no principal")
        return principal, None

    def team_scope(request: Request, principal):
        is_admin = principal.role in _ADMIN_ROLES
        requested = request.query_params.get("team")
        if requested is not None and not is_admin and requested != principal.teamId:
            return None, _error(403, "TEAM_FORBIDDEN", "cannot view another team")
        if requested is not None:
            return requested, None
        return (None if is_admin else principal.teamId), None

    # ------------------------------------------------------------------ usage

    async def usage(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        team, terr = team_scope(request, principal)
        if terr:
            return terr
        from_ = request.query_params.get("from")
        to = request.query_params.get("to")
        snapshot = usage_snapshot(
            records_provider(),
            team=team,
            from_=datetime.fromisoformat(from_).replace(tzinfo=UTC) if from_ else None,
            to=datetime.fromisoformat(to).replace(tzinfo=UTC) if to else None,
            as_of=now_fn(),
        )
        return JSONResponse(snapshot)

    async def webhooks(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        if request.method == "GET":
            if principal.role not in _ADMIN_ROLES:
                return _error(403, "ADMIN_REQUIRED", "webhook registry is admin-only")
            rows = [
                {"webhookId": w.webhookId, "url": w.url, "events": w.events,
                 "teamId": w.teamId, "active": w.active}
                for w in store.webhooks(active_only=False)
            ]
            return JSONResponse({"webhooks": rows})
        if principal.role not in _ADMIN_ROLES:
            return _error(403, "ADMIN_REQUIRED", "webhook registration is admin-only")
        body = await request.json()
        try:
            row, secret = register_webhook(
                store, url=body.get("url", ""), events=body.get("events", []),
                team_id=body.get("teamId"), created_by=principal.userId, now=now_fn(),
                allow_private=body.get("allowPrivate", False) is True,
            )
        except UsageWebhookError as exc:
            return _error(exc.status_code, exc.code, exc.message)
        return JSONResponse(
            {"webhookId": row.webhookId, "secret": secret,
             "note": "secret shown once; store it in the receiver config"},
            status_code=201,
        )

    # ----------------------------------------------------------------- alerts

    async def alert_list(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        team, terr = team_scope(request, principal)
        if terr:
            return terr
        rows = [
            a for a in store.alerts()
            if team is None
            or a["subject"].get("teamId") == team
            or a["subject"].get("teamId") is None
        ]
        return JSONResponse({"alerts": rows})

    async def alert_evaluate(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        if principal.role not in _ADMIN_ROLES:
            return _error(403, "ADMIN_REQUIRED", "alert evaluation is admin-only")
        now = now_fn()
        drafts = evaluate_alerts(records_provider(), as_of=now,
                                 thresholds=alerts.thresholds)
        sync = alerts.sync(drafts, now=now)
        delivered = 0
        notifications = sync["opened"] + sync["reminders"] + sync["resolved"]
        if notifications and http is not None:
            for notification in notifications:
                results = await dispatch_event(
                    store=store, event_type=notification["event"],
                    payload=notification, http=http, now=now,
                    team_id=notification["subject"].get("teamId"),
                )
                delivered += sum(1 for r in results if r.status == "delivered")
        return JSONResponse(
            {"opened": len(sync["opened"]), "reminders": len(sync["reminders"]),
             "suppressed": sync["suppressed"], "resolved": len(sync["resolved"]),
             "delivered": delivered, "active": len(store.alerts(status="active"))}
        )

    # --------------------------------------------------------------- evidence

    async def evidence_export(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        if archive is None:
            return _error(503, "EVIDENCE_UNAVAILABLE", "archive not configured")
        team, terr = team_scope(request, principal)
        if terr:
            return terr
        package = build_evidence(
            archive=archive,
            visible_teams=None if team is None else {team},
            generated_at=now_fn(),
        )
        return Response(
            package.to_zip(),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    "attachment; filename="
                    f"harness-evidence-{package.manifest()['packageId']}.zip"
                )
            },
        )

    # --------------------------------------------------------------------- CI

    async def ci_webhook(request: Request):
        if ci_intake is None or not ci_adapters:
            return _error(503, "CI_NOT_CONFIGURED", "no CI adapters configured")
        provider = request.path_params["provider"]
        raw = await request.body()
        try:
            outcome = await ci_intake.handle(
                provider, raw, dict(request.headers), now=int(now_fn().timestamp())
            )
        except IntakeRejected as exc:
            return _error(exc.status_code, exc.code, exc.message)
        except PlanGateError as exc:
            return _error(exc.status_code, exc.code, exc.message)
        if outcome.status == "duplicate":
            return JSONResponse({"status": "duplicate", "provider": outcome.provider},
                                status_code=200)
        return JSONResponse(
            {"status": "plan_proposed", "provider": outcome.provider,
             "repo": outcome.repo, "planId": outcome.planId, "awaitingApproval": True},
            status_code=202,
        )

    # ------------------------------------------------------------------- chat

    async def chat_action(request: Request):
        if slack_handler is None:
            return _error(503, "CHAT_NOT_CONFIGURED", "chat handler not wired")
        raw = await request.body()
        try:
            actor = request.headers.get("x-actor", "")
            roles = [r for r in request.headers.get("x-actor-roles", "").split(",") if r]
            result = await slack_handler.handle(
                raw, dict(request.headers), actor=actor, actor_roles=roles, now=now_fn()
            )
        except IntakeRejected as exc:
            return _error(exc.status_code, exc.code, exc.message)
        except ActionTokenError as exc:
            return _error(exc.status_code, exc.code, exc.message)
        except PlanGateError as exc:
            return _error(exc.status_code, exc.code, exc.message)
        return JSONResponse(result)

    # -------------------------------------------------------------- schedules

    async def schedules(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        if principal.role not in _ADMIN_ROLES:
            return _error(403, "ADMIN_REQUIRED", "schedules are admin-only")
        if request.method == "GET":
            rows = []
            for s in store.schedules():
                rows.append(
                    {"scheduleId": s.scheduleId, "name": s.name, "teamId": s.teamId,
                     "spec": s.spec, "tz": s.tz, "misfirePolicy": s.misfirePolicy,
                     "enabled": s.enabled, "lastRunAt": s.lastRunAt,
                     "runs": store.schedule_runs(s.scheduleId)}
                )
            return JSONResponse({"schedules": rows})
        body = await request.json()
        try:
            validate_spec(body.get("spec", {}), body.get("tz", ""))
        except ScheduleError as exc:
            return _error(exc.status_code, exc.code, exc.message)
        from .store import ScheduleRow

        now = now_fn()
        schedule_id = f"sch-{now.strftime('%Y%m%d%H%M%S')}-{len(store.schedules())}"
        row = ScheduleRow(
            scheduleId=schedule_id, name=body.get("name", schedule_id),
            teamId=body.get("teamId", principal.teamId),
            spec=body.get("spec", {}), tz=body["tz"],
            goalTemplate=body.get("goalTemplate", ""),
            misfirePolicy=body.get("misfirePolicy", "skip"),
            enabled=True, createdBy=principal.userId,
            createdAt=now.isoformat(), lastRunAt=now.isoformat(),
        )
        if row.misfirePolicy not in ("skip", "run_once"):
            return _error(400, "SPEC_INVALID", "misfirePolicy must be skip|run_once")
        store.insert_schedule(row)
        return JSONResponse({"scheduleId": schedule_id, "nextRunAt": _next(row)}, 201)

    def _next(row) -> str:
        from .schedule import next_occurrence

        return next_occurrence(row.spec, row.tz,
                               after=datetime.fromisoformat(row.lastRunAt)).isoformat()

    async def schedule_trigger(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        if principal.role not in _ADMIN_ROLES:
            return _error(403, "ADMIN_REQUIRED", "schedule trigger is admin-only")
        if schedule_gate is None:
            return _error(503, "PLAN_GATE_UNAVAILABLE", "no plan gate configured")
        schedule = store.schedule(request.path_params["scheduleId"])
        if schedule is None:
            return _error(404, "SCHEDULE_NOT_FOUND", "unknown schedule")
        try:
            results = await run_due_schedules(store=store, gate=schedule_gate,
                                              now=now_fn())
        except PlanGateError as exc:
            return _error(exc.status_code, exc.code, exc.message)
        mine = [r for r in results if r["scheduleId"] == schedule.scheduleId]
        return JSONResponse({"results": mine})

    async def schedule_disable(request: Request):
        principal, err = await principal_or_error(request)
        if err:
            return err
        if principal.role not in _ADMIN_ROLES:
            return _error(403, "ADMIN_REQUIRED", "schedule disable is admin-only")
        action = request.path_params["action"]
        if action not in ("enable", "disable"):
            return _error(404, "ROUTE_NOT_FOUND", f"unknown action {action!r}")
        enabled = action == "enable"
        if not store.set_schedule_enabled(request.path_params["scheduleId"], enabled):
            return _error(404, "SCHEDULE_NOT_FOUND", "unknown schedule")
        return JSONResponse({"scheduleId": request.path_params["scheduleId"],
                             "enabled": enabled})

    async def healthz(request: Request):
        return JSONResponse({"status": "ok"})

    routes = [
        Route("/healthz", healthz),
        Route("/api/v1/ops/usage", usage),
        Route("/api/v1/ops/usage/webhooks", webhooks, methods=["GET", "POST"]),
        Route("/api/v1/ops/alerts", alert_list),
        Route("/api/v1/ops/alerts/evaluate", alert_evaluate, methods=["POST"]),
        Route("/api/v1/ops/evidence/export", evidence_export),
        Route("/api/v1/ops/ci/{provider}", ci_webhook, methods=["POST"]),
        Route("/api/v1/ops/chat/slack/actions", chat_action, methods=["POST"]),
        Route("/api/v1/ops/schedules", schedules, methods=["GET", "POST"]),
        Route("/api/v1/ops/schedules/{scheduleId}/trigger", schedule_trigger,
              methods=["POST"]),
        Route("/api/v1/ops/schedules/{scheduleId}/{action}",
              schedule_disable, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    if get_principal_dep is not None:
        app.state.get_principal = get_principal_dep
    return app


def app_factory():  # pragma: no cover - deployment entrypoint
    import os

    from harness_telemetry import Archive, build_records, fixtures, parse_cost, parse_stream

    from .store import OpsStore

    store = OpsStore(os.environ.get("OPS_DB", "ops.sqlite"))
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    records = build_records(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    archive = Archive(os.environ.get("OPS_ARCHIVE_DB", "ops-archive.sqlite"))
    if os.environ.get("OPS_FIXTURES") == "1":
        archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    return make_ops_app(
        store=store, records_provider=lambda: records, archive=archive,
        now_fn=lambda: datetime.now(UTC),
    )
