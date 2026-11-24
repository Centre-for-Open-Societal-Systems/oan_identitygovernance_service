from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openg2p_fastapi_common.controller import BaseController

from app.auth import OPERATOR, VIEWER, Principal, require
from app.db import get_session
from app.envelope import ok
from app.models import SyncRun
from app.services.applications import get_app
from app.services.sync import first_sync


def _run(r: SyncRun) -> dict:
    return {
        c: getattr(r, c)
        for c in ("id", "realm", "direction", "started", "finished", "counts", "errors")
    }


class SyncController(BaseController):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["sync"]
        self.router.prefix = "/sync"
        self.router.add_api_route("/run/{app_id}", self.run, methods=["POST"])
        self.router.add_api_route("/status", self.status, methods=["GET"])

    async def run(
        self,
        app_id: str,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        """On-demand Keycloak -> central RBAC import for one application. Scheduled runs and drift report come next."""
        return ok(_run(await first_sync(db, p.username, await get_app(db, app_id))))

    async def status(
        self,
        realm: str | None = None,
        db: AsyncSession = Depends(get_session),
        _: Principal = Depends(require(VIEWER, OPERATOR)),
    ):
        q = select(SyncRun).order_by(SyncRun.started.desc()).limit(50)
        if realm:
            q = q.where(SyncRun.realm == realm)
        return ok([_run(r) for r in await db.scalars(q)])
