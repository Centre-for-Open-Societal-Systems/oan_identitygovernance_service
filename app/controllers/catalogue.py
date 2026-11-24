from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from openg2p_fastapi_common.controller import BaseController

from app.auth import ADMIN, OPERATOR, VIEWER, Principal, require
from app.db import get_session
from app.envelope import RbacError, ok
from app.models import AllowedPermission, Application, Realm, Role
from app.services import applications as apps
from app.services import roles as roles_svc
from app.services.audit import audit


def _app(a: Application) -> dict:
    return {
        c: getattr(a, c)
        for c in (
            "id",
            "realm",
            "mnemonic",
            "description",
            "keycloak_client_uuid",
            "service_url",
            "admin_api_url",
            "owner",
            "auto_repair",
            "status",
        )
    }


def _role(r: Role) -> dict:
    return {
        c: getattr(r, c)
        for c in (
            "id",
            "application_id",
            "name",
            "description",
            "keycloak_role_id",
            "status",
            "error",
            "permissions_recorded",
        )
    }


# --- realms -------------------------------------------------------------------


class CatalogueController(BaseController):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["catalogue"]

        self.router.add_api_route("/realms/list", self.list_realms, methods=["GET"])
        self.router.add_api_route(
            "/realms/register", self.register_realm, methods=["POST"]
        )
        self.router.add_api_route(
            "/applications/create", self.create_application, methods=["POST"]
        )
        self.router.add_api_route(
            "/applications/list", self.list_applications, methods=["GET"]
        )
        self.router.add_api_route(
            "/applications/{app_id}/disable", self.disable_application, methods=["POST"]
        )
        self.router.add_api_route(
            "/applications/{app_id}/allow-list", self.set_allow_list, methods=["PUT"]
        )
        self.router.add_api_route("/roles/create", self.create_role, methods=["POST"])
        self.router.add_api_route("/roles/list", self.list_roles, methods=["GET"])
        self.router.add_api_route(
            "/roles/{role_id}/permissions", self.set_role_permissions, methods=["POST"]
        )
        self.router.add_api_route(
            "/roles/{role_id}/accept", self.accept_role, methods=["POST"]
        )
        self.router.add_api_route(
            "/roles/{role_id}/disable", self.disable_role, methods=["POST"]
        )

    async def list_realms(
        self,
        db: AsyncSession = Depends(get_session),
        _: Principal = Depends(require(VIEWER, OPERATOR)),
    ):
        return ok(
            [
                {"name": r.name, "status": r.status}
                for r in await db.scalars(select(Realm))
            ]
        )

    async def register_realm(
        self,
        body: schemas.RealmIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(ADMIN)),
    ):
        if await db.get(Realm, body.name):
            raise RbacError("REALM_EXISTS", "Realm already registered", 409)
        db.add(Realm(name=body.name))
        audit(db, p.username, "realm.register", body.name)
        await db.commit()
        return ok({"name": body.name})

    async def create_application(
        self,
        body: schemas.ApplicationIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        app, secret = await apps.create_application(db, p.username, body.model_dump())
        return ok(
            {
                **_app(app),
                "client_secret": secret,
                "client_secret_note": "Shown once. Not stored.",
            }
        )

    async def list_applications(
        self,
        realm: str | None = None,
        db: AsyncSession = Depends(get_session),
        _: Principal = Depends(require(VIEWER, OPERATOR)),
    ):
        q = select(Application)
        if realm:
            q = q.where(Application.realm == realm)
        return ok([_app(a) for a in await db.scalars(q)])

    async def disable_application(
        self,
        app_id: str,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(_app(await apps.disable_application(db, p.username, app_id)))

    async def set_allow_list(
        self,
        app_id: str,
        body: schemas.AllowListIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(ADMIN)),
    ):
        app = await apps.get_app(db, app_id)
        for row in await db.scalars(
            select(AllowedPermission).where(AllowedPermission.application_id == app.id)
        ):
            await db.delete(row)
        for pair in {(x.resource, x.action) for x in body.permissions}:
            db.add(
                AllowedPermission(
                    application_id=app.id, resource=pair[0], action=pair[1]
                )
            )
        audit(
            db,
            p.username,
            "allow_list.set",
            f"{app.realm}/{app.mnemonic}",
            after={"count": len(body.permissions)},
        )
        await db.commit()
        return ok({"count": len(body.permissions)})

    async def create_role(
        self,
        body: schemas.RoleIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(
            _role(
                await roles_svc.create_role(
                    db, p.username, body.application_id, body.name, body.description
                )
            )
        )

    async def list_roles(
        self,
        application_id: str | None = None,
        status: str | None = None,
        db: AsyncSession = Depends(get_session),
        _: Principal = Depends(require(VIEWER, OPERATOR)),
    ):
        q = select(Role)
        if application_id:
            q = q.where(Role.application_id == application_id)
        if status:
            q = q.where(Role.status == status)
        return ok([_role(r) for r in await db.scalars(q)])

    async def set_role_permissions(
        self,
        role_id: str,
        body: schemas.RolePermissionsIn,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(
            _role(
                await roles_svc.set_permissions(
                    db, p.username, role_id, [x.model_dump() for x in body.permissions]
                )
            )
        )

    async def accept_role(
        self,
        role_id: str,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(_role(await roles_svc.accept_imported(db, p.username, role_id)))

    async def disable_role(
        self,
        role_id: str,
        db: AsyncSession = Depends(get_session),
        p: Principal = Depends(require(OPERATOR)),
    ):
        return ok(_role(await roles_svc.disable_role(db, p.username, role_id)))
