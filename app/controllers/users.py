from fastapi import Depends
from openg2p_fastapi_common.controller import BaseController
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import OPERATOR, VIEWER, Principal, require
from app.db import get_session
from app.envelope import RbacError, ok
from app.keycloak import keycloak
from app.models import Application
from app.schemas import UserOut
from app.services import users as users_svc
from app.services.common import get_client


class UsersController(BaseController):
    """Users are read from and written to Keycloak; `realm` selects which one."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["users"]
        self.router.prefix = "/users"
        self.router.add_api_route("/create", self.create_user, methods=["POST"])
        self.router.add_api_route("/bulk-create", self.bulk_create, methods=["POST"])
        self.router.add_api_route("/list", self.list_users, methods=["GET"])
        self.router.add_api_route("/{user_id}/roles", self.user_roles, methods=["GET"])
        self.router.add_api_route("/{user_id}/disable", self.disable, methods=["POST"])
        self.router.add_api_route("/{user_id}/enable", self.enable, methods=["POST"])
        self.router.add_api_route("/{user_id}/roles/grant", self.grant, methods=["POST"])
        self.router.add_api_route("/{user_id}/roles/revoke", self.revoke, methods=["POST"])

    async def create_user(self, body: schemas.UserIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        return ok(await users_svc.create_user(db, p.username, body.model_dump()))

    async def bulk_create(self, body: schemas.BulkUsersIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Per-row result; one bad row never aborts the rest. Re-running is safe (Keycloak 409 -> existing user)."""
        results = []
        for row in body.users:
            try:
                results.append({"username": row.username, "status": "ok", "user": await users_svc.create_user(db, p.username, row.model_dump())})
            except RbacError as e:
                await db.rollback()
                results.append({"username": row.username, "status": "error", "error_code": e.code, "error": e.message})
        return ok(results)

    async def list_users(self, realm: str, search: str | None = None, first: int = 0, max: int = 50, _: Principal = Depends(require(VIEWER, OPERATOR))):
        params = {"first": first, "max": min(max, 200), **({"search": search} if search else {})}
        reps = (await keycloak.request("GET", f"/{realm}/users", params=params)).json()
        return ok([UserOut.of(u) for u in reps])

    async def user_roles(self, user_id: str, realm: str, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
        """Client roles the user holds in each managed application, read live from Keycloak."""
        out = []
        for app in await db.scalars(select(Application).where(Application.realm == realm)):
            client_uuid = (await get_client(app))["id"]
            held = (await keycloak.request("GET", f"/{realm}/users/{user_id}/role-mappings/clients/{client_uuid}")).json()
            if held:
                out.append({"application": app.mnemonic, "application_id": app.id, "roles": sorted(r["name"] for r in held)})
        return ok(out)

    async def disable(self, user_id: str, realm: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        return ok(await users_svc.set_status(db, p.username, realm, user_id, False))

    async def enable(self, user_id: str, realm: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        return ok(await users_svc.set_status(db, p.username, realm, user_id, True))

    async def grant(self, user_id: str, realm: str, body: schemas.RoleAssignIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        return ok(await users_svc.assign_role(db, p.username, realm, user_id, body.model_dump(), True))

    async def revoke(self, user_id: str, realm: str, body: schemas.RoleAssignIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        return ok(await users_svc.assign_role(db, p.username, realm, user_id, body.model_dump(), False))
