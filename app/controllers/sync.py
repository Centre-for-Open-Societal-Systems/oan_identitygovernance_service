from fastapi import Depends
from openg2p_fastapi_common.controller import BaseController
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import OPERATOR, VIEWER, Principal, require
from app.db import get_session
from app.envelope import ok
from app.models import SyncRun
from app.schemas import SyncRunOut
from app.services.common import get_app, get_client
from app.services.sync import first_sync


class SyncController(BaseController):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["sync"]
        self.router.prefix = "/sync"
        self.router.add_api_route("/run/{app_id}", self.run, methods=["POST"])
        self.router.add_api_route("/status", self.status, methods=["GET"])

    async def run(self, app_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """On-demand Keycloak -> central RBAC import for one application. Scheduled runs and drift report come next."""
        app = await get_app(db, app_id)
        return ok(SyncRunOut.model_validate(await first_sync(db, p.username, app, (await get_client(app))["id"])))

    async def status(self, realm: str | None = None, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
        q = select(SyncRun).order_by(SyncRun.started.desc()).limit(50)
        if realm:
            q = q.where(SyncRun.realm == realm)
        return ok([SyncRunOut.model_validate(r) for r in await db.scalars(q)])
