from fastapi import Depends
from openg2p_fastapi_common.controller import BaseController
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.adapters.service import ServiceAdapter
from app.auth import ADMIN, OPERATOR, VIEWER, Principal, require
from app.config import Settings
from app.db import get_session
from app.envelope import RbacError, ok
from app.keycloak import keycloak
from app.models import AllowedPermission, Application, Realm
from app.schemas import ApplicationCreatedOut, ApplicationOut, RoleOut
from app.services.common import audit, get_app, get_client

_config = Settings.get_config(strict=False)
CENTRAL_ADMIN_ROLE = "OAN Administrator"


class CatalogueController(BaseController):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["catalogue"]
        self.router.add_api_route("/realms/list", self.list_realms, methods=["GET"])
        self.router.add_api_route("/realms/register", self.register_realm, methods=["POST"])
        self.router.add_api_route("/applications/create", self.create_application, methods=["POST"])
        self.router.add_api_route("/applications/list", self.list_applications, methods=["GET"])
        self.router.add_api_route("/applications/{app_id}/disable", self.disable_application, methods=["POST"])
        self.router.add_api_route("/applications/{app_id}/allow-list", self.set_allow_list, methods=["PUT"])
        # Roles are the Keycloak client roles of the application, named in the path; nothing about them is stored.
        self.router.add_api_route("/applications/{app_id}/roles/list", self.list_roles, methods=["GET"])
        self.router.add_api_route("/applications/{app_id}/roles/create", self.create_role, methods=["POST"])
        self.router.add_api_route("/applications/{app_id}/roles/{name}/delete", self.delete_role, methods=["POST"])
        self.router.add_api_route("/applications/{app_id}/roles/{name}/permissions", self.get_role_permissions, methods=["GET"])
        self.router.add_api_route("/applications/{app_id}/roles/{name}/permissions", self.set_role_permissions, methods=["POST"])

    # --- realms ---------------------------------------------------------------
    async def list_realms(self, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
        return ok([{"name": r.name, "status": r.status} for r in await db.scalars(select(Realm))])

    async def register_realm(self, body: schemas.RealmIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(ADMIN))):
        if await db.get(Realm, body.name):
            raise RbacError("REALM_EXISTS", "Realm already registered", 409)
        db.add(Realm(name=body.name))
        audit(db, p.username, "realm.register", body.name)
        await db.commit()
        return ok({"name": body.name})

    # --- applications -----------------------------------------------------------
    async def create_application(self, body: schemas.ApplicationIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.1. The client secret is returned once and never stored."""
        if not await db.get(Realm, body.realm):
            raise RbacError("REALM_NOT_FOUND", f"Realm {body.realm} is not registered", 404)
        dup = await db.scalar(select(Application).where(Application.realm == body.realm, Application.mnemonic == body.mnemonic))
        if dup:
            raise RbacError("APP_EXISTS", "Application already registered in this realm", 409)

        # Keycloak first; it is idempotent (an existing client is reused), so a failed commit below can be retried.
        r = await keycloak.request(
            "POST", f"/{body.realm}/clients", expect=(201, 409),
            json={
                "clientId": body.mnemonic, "description": body.description or "", "enabled": True,
                "publicClient": False, "serviceAccountsEnabled": True, "standardFlowEnabled": False,
            },
        )
        if r.status_code == 201:
            client_uuid = r.headers["Location"].rsplit("/", 1)[1]
        else:
            existing = await keycloak.find_client(body.realm, body.mnemonic)
            if not existing:
                raise RbacError("KEYCLOAK_ERROR", "Client reported as existing but not found", 502)
            client_uuid = existing["id"]
        # A public client (an existing one may be) has no secret.
        secret = (await keycloak.request("GET", f"/{body.realm}/clients/{client_uuid}/client-secret")).json().get("value")

        # The operator administers the application from now on: the role is created on its own client and granted to
        # them, so a token they log in with next carries it and the service maps it to its Frappe role of that name.
        await keycloak.create_client_role(body.realm, client_uuid, CENTRAL_ADMIN_ROLE)
        if body.realm == _config.auth_realm:
            role = next(r for r in await keycloak.list_client_roles(body.realm, client_uuid) if r["name"] == CENTRAL_ADMIN_ROLE)
            await keycloak.add_client_roles(body.realm, p.sub, client_uuid, [role])

        app = Application(realm=body.realm, mnemonic=body.mnemonic, admin_api_url=body.admin_api_url)
        db.add(app)
        audit(db, p.username, "application.create", f"{app.realm}/{app.mnemonic}", after={"client": client_uuid})
        await db.commit()
        client = {"description": body.description, "enabled": True}
        return ok(ApplicationCreatedOut(**ApplicationOut.of(app, client).model_dump(), client_secret=secret))

    async def list_applications(self, realm: str | None = None, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
        q = select(Application)
        if realm:
            q = q.where(Application.realm == realm)
        rows = list(await db.scalars(q))
        clients = {}
        for r in {a.realm for a in rows}:
            clients[r] = {c["clientId"]: c for c in (await keycloak.request("GET", f"/{r}/clients", params={"max": 1000})).json()}
        return ok([ApplicationOut.of(a, clients[a.realm].get(a.mnemonic)) for a in rows])

    async def disable_application(self, app_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Disabling the Keycloak client is the enforcement: no token can be issued for it."""
        app = await get_app(db, app_id)
        client = await get_client(app)
        await keycloak.request("PUT", f"/{app.realm}/clients/{client['id']}", json={"enabled": False})
        audit(db, p.username, "application.disable", f"{app.realm}/{app.mnemonic}", before={"enabled": client.get("enabled", True)}, after={"enabled": False})
        await db.commit()
        return ok(ApplicationOut.of(app, {**client, "enabled": False}))

    async def set_allow_list(self, app_id: str, body: schemas.AllowListIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(ADMIN))):
        app = await get_app(db, app_id)
        for row in await db.scalars(select(AllowedPermission).where(AllowedPermission.application_id == app.id)):
            await db.delete(row)
        for pair in {(x.resource, x.action) for x in body.permissions}:
            db.add(AllowedPermission(application_id=app.id, resource=pair[0], action=pair[1]))
        audit(db, p.username, "allow_list.set", f"{app.realm}/{app.mnemonic}", after={"count": len(body.permissions)})
        await db.commit()
        return ok({"count": len(body.permissions)})

    # --- roles ------------------------------------------------------------------
    async def list_roles(self, app_id: str, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
        """The client roles of the application in Keycloak, whoever created them."""
        app = await get_app(db, app_id)
        reps = await keycloak.list_client_roles(app.realm, (await get_client(app))["id"])
        return ok([RoleOut(application_id=app.id, name=r["name"], description=r.get("description")) for r in reps])

    async def create_role(self, app_id: str, body: schemas.RoleIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.2: the service creates the role and its Keycloak client role; the result is read back from Keycloak."""
        app = await get_app(db, app_id)
        client = await get_client(app)
        if not client.get("enabled", True):
            raise RbacError("APP_NOT_ACTIVE", "Application is disabled in Keycloak", 409)
        async with ServiceAdapter(app, p.token) as svc:
            await svc.create_role(body.name)
        audit(db, p.username, "role.create", f"{app.mnemonic}/{body.name}")
        await db.commit()
        rep = next((r for r in await keycloak.list_client_roles(app.realm, client["id"]) if r["name"] == body.name), None)
        if not rep:
            raise RbacError(
                "KEYCLOAK_ROLE_MISSING",
                f"{app.mnemonic} created {body.name} but not in its Keycloak client; set admin_client_id and "
                "admin_client_secret on the service's login option",
                502,
            )
        return ok(RoleOut(application_id=app.id, name=rep["name"], description=rep.get("description")))

    async def delete_role(self, app_id: str, name: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """The service deletes the role and its Keycloak client role."""
        app = await get_app(db, app_id)
        async with ServiceAdapter(app, p.token) as svc:
            await svc.delete_role(name)
        audit(db, p.username, "role.delete", f"{app.mnemonic}/{name}")
        await db.commit()
        return ok({"application_id": app.id, "name": name})

    async def get_role_permissions(self, app_id: str, name: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(VIEWER, OPERATOR))):
        """Read live from the managed service."""
        async with ServiceAdapter(await get_app(db, app_id), p.token) as svc:
            return ok(await svc.get_permissions(name))

    async def set_role_permissions(self, app_id: str, name: str, body: schemas.RolePermissionsIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.3: validate against the allow-list, then push to the service (the service is the source of truth)."""
        app = await get_app(db, app_id)
        allowed = {
            (a.resource, a.action)
            for a in (await db.scalars(select(AllowedPermission).where(AllowedPermission.application_id == app.id)))
        }
        wanted = {(x.resource, x.action) for x in body.permissions}
        rejected = sorted(wanted - allowed)
        if rejected:
            raise RbacError("NOT_ALLOWED", f"Not in the allow-list for {app.mnemonic}: {rejected}", 422)

        pairs = [{"resource": r, "action": a} for r, a in sorted(wanted)]
        async with ServiceAdapter(app, p.token) as svc:
            before = await svc.get_permissions(name)
            await svc.set_permissions(name, pairs)
        audit(db, p.username, "role.permissions.set", f"{app.mnemonic}/{name}", before={"count": len(before)}, after={"permissions": sorted(wanted)})
        await db.commit()
        return ok(pairs)
