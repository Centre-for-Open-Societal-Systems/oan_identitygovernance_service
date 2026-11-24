from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from openg2p_fastapi_common.controller import BaseController

from app.auth import OPERATOR, VIEWER, Principal, require
from app.db import get_session
from app.envelope import RbacError, ok
from app.models import User
from app.services import users as users_svc


def _user(u: User) -> dict:
    return {
        c: getattr(u, c)
        for c in (
            "id",
            "realm",
            "keycloak_user_id",
            "username",
            "email",
            "status",
            "groups",
            "location_codes",
        )
    }


class UsersController(BaseController):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["users"]
        self.router.prefix = "/users"
        self.router.add_api_route("/create", self.create_user, methods=["POST"])
        self.router.add_api_route("/bulk-create", self.bulk_create, methods=["POST"])
        self.router.add_api_route("/list", self.list_users, methods=["GET"])
        self.router.add_api_route("/{user_id}/disable", self.disable, methods=["POST"])
        self.router.add_api_route("/{user_id}/enable", self.enable, methods=["POST"])
        self.router.add_api_route(
            "/{user_id}/roles/grant", self.grant, methods=["POST"]
        )
        self.router.add_api_route(
            "/{user_id}/roles/revoke", self.revoke, methods=["POST"]
        )

    async def create_user(
        self,
        body: schemas.UserIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(_user(await users_svc.create_user(db, p.username, body.model_dump())))

    async def bulk_create(
        self,
        body: schemas.BulkUsersIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        """Per-row result; one bad row never aborts the rest. Re-running is safe (Keycloak 409 -> existing user)."""
        results = []
        for row in body.users:
            try:
                results.append(
                    {
                        "username": row.username,
                        "status": "ok",
                        "user": _user(
                            await users_svc.create_user(
                                db, p.username, row.model_dump()
                            )
                        ),
                    }
                )
            except RbacError as e:
                await db.rollback()
                results.append(
                    {
                        "username": row.username,
                        "status": "error",
                        "error_code": e.code,
                        "error": e.message,
                    }
                )
        return ok(results)

    async def list_users(
        self,
        realm: str | None = None,
        db: AsyncSession = Depends(get_session),
        _: Principal = Depends(require(VIEWER, OPERATOR)),
    ):
        q = select(User)
        if realm:
            q = q.where(User.realm == realm)
        return ok([_user(u) for u in await db.scalars(q)])

    async def disable(
        self,
        user_id: str,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(_user(await users_svc.set_status(db, p.username, user_id, False)))

    async def enable(
        self,
        user_id: str,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(_user(await users_svc.set_status(db, p.username, user_id, True)))

    async def grant(
        self,
        user_id: str,
        body: schemas.RoleAssignIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(
            _user(
                await users_svc.assign_role(db, p.username, user_id, body.role_id, True)
            )
        )

    async def revoke(
        self,
        user_id: str,
        body: schemas.RoleAssignIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(
            _user(
                await users_svc.assign_role(
                    db, p.username, user_id, body.role_id, False
                )
            )
        )
