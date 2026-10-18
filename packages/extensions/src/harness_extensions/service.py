"""/api/v1/extensions. The team always comes from the authenticated principal, never the body."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .catalog import CatalogRejected, verify_catalog
from .config import Config
from .netguard import UrlRejected
from .registry import Invalid, NotFound, Registry
from .schema_guard import SchemaRejected
from .store import Store


@dataclass(frozen=True)
class Principal:
    userId: str  # noqa: N815 - wire shape matches section1's principal
    teamId: str  # noqa: N815
    role: str  # admin | engineer | viewer


async def no_principal(request: Request) -> Principal:
    """Default resolver: fail closed until wired to the S1 identity service (NEEDS INTEGRATION)."""
    raise HTTPException(401, "authentication is not configured")


class RegisterBody(BaseModel):
    kind: str
    name: str
    url: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)


class GrantBody(BaseModel):
    tool: str
    subjectKind: str  # noqa: N815
    subject: str


class AuthorizeBody(BaseModel):
    tool: str
    agent: str | None = None


class InstallBody(BaseModel):
    name: str


def create_app(
    cfg: Config, store: Store | None = None, principal_dep: Callable = no_principal
) -> FastAPI:
    reg = Registry(store or Store(cfg.data_dir / "extensions.db"), cfg)
    router = APIRouter(prefix="/api/v1/extensions")
    Who = Annotated[Principal, Depends(principal_dep)]  # noqa: N806

    def admin(p: Principal) -> Principal:
        if p.role != "admin":
            raise HTTPException(403, "admin only")
        return p

    def guarded(fn: Callable, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        except (Invalid, UrlRejected, SchemaRejected) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("")
    def list_ext(p: Who):
        return reg.list(p.teamId)

    @router.post("", status_code=201)
    def register(body: RegisterBody, p: Who):
        admin(p)
        return guarded(
            reg.register, p.teamId, p.userId, kind=body.kind, name=body.name, url=body.url,
            tools=body.tools,
        )  # fmt: skip

    # catalog routes are declared before /{ext_id} routes so the literal path wins
    def verified_entries() -> list[dict[str, Any]]:
        path = cfg.data_dir / "catalog.json"
        try:
            return verify_catalog(json.loads(path.read_text()), cfg.catalog_public_key)
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(503, "catalog unavailable") from exc
        except CatalogRejected as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/catalog")
    def catalog(p: Who):
        return verified_entries()

    @router.post("/catalog/install", status_code=201)
    def install(body: InstallBody, p: Who):
        admin(p)
        entry = next((e for e in verified_entries() if e.get("name") == body.name), None)
        if entry is None:
            raise HTTPException(404, "not in catalog")
        # Catalog entries still go through netguard + schema_guard like any registration.
        out = guarded(
            reg.register, p.teamId, p.userId, kind=entry.get("kind", ""), name=entry["name"],
            url=entry.get("url"), tools=entry.get("tools", []),
        )  # fmt: skip
        with reg.s.lock, reg.s.db:
            reg.s.audit(
                p.teamId, p.userId, "catalog.installed", str(out["id"]), {"name": body.name}
            )
        return out

    @router.get("/{ext_id}")
    def get_ext(ext_id: int, p: Who):
        return guarded(reg.get, p.teamId, ext_id)

    @router.post("/{ext_id}/disable")
    def disable(ext_id: int, p: Who):
        admin(p)
        return guarded(reg.disable, p.teamId, p.userId, ext_id)

    @router.get("/{ext_id}/grants")
    def grants(ext_id: int, p: Who):
        admin(p)
        return guarded(reg.grants, p.teamId, ext_id)

    @router.post("/{ext_id}/grants", status_code=201)
    def grant(ext_id: int, body: GrantBody, p: Who):
        admin(p)
        return guarded(
            reg.grant, p.teamId, p.userId, ext_id, tool=body.tool,
            subject_kind=body.subjectKind, subject=body.subject,
        )  # fmt: skip

    @router.delete("/{ext_id}/grants/{grant_id}", status_code=204)
    def revoke(ext_id: int, grant_id: int, p: Who):
        admin(p)
        guarded(reg.revoke, p.teamId, p.userId, ext_id, grant_id)

    @router.post("/{ext_id}/authorize")
    def authorize(ext_id: int, body: AuthorizeBody, p: Who):
        """Enforcement point for callers: the caller's role comes from auth, not the body."""
        ok = guarded(
            reg.authorize, p.teamId, p.userId, ext_id, body.tool, role=p.role, agent=body.agent
        )
        if not ok:
            raise HTTPException(403, "tool call not granted")
        return {"allowed": True}

    app = FastAPI(title="harness-extensions")
    app.include_router(router)
    return app
