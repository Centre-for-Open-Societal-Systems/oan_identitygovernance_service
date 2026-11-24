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
from app.models import AllowedPermission, Application, Realm, Role
from app.schemas import ApplicationCreatedOut, ApplicationOut, RoleOut
from app.services.common import audit, get_app, get_client, get_role
from app.services.sync import first_sync

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
        self.router.add_api_route("/roles/create", self.create_role, methods=["POST"])
        self.router.add_api_route("/roles/list", self.list_roles, methods=["GET"])
        self.router.add_api_route("/roles/{role_id}/permissions", self.get_role_permissions, methods=["GET"])
        self.router.add_api_route("/roles/{role_id}/permissions", self.set_role_permissions, methods=["POST"])
        self.router.add_api_route("/roles/{role_id}/accept", self.accept_role, methods=["POST"])
        self.router.add_api_route("/roles/{role_id}/disable", self.disable_role, methods=["POST"])

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
        secret = (await keycloak.request("GET", f"/{body.realm}/clients/{client_uuid}/client-secret")).json()["value"]

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
        await first_sync(db, p.username, app, client_uuid)
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
    async def create_role(self, body: schemas.RoleIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.2: pending row, the service creates the role (and its Keycloak client role), then active/failed."""
        app = await get_app(db, body.application_id)
        if not (await get_client(app)).get("enabled", True):
            raise RbacError("APP_NOT_ACTIVE", "Application is disabled in Keycloak", 409)
        existing = await db.scalar(select(Role).where(Role.application_id == app.id, Role.name == body.name))
        if existing and existing.status != "failed":
            raise RbacError("ROLE_EXISTS", "Role already exists for this application", 409)
        role = existing or Role(application_id=app.id, name=body.name)
        role.status, role.error = "pending", None
        db.add(role)
        await db.commit()

        try:
            async with ServiceAdapter(app, p.token) as svc:
                await svc.create_role(body.name)
            role.status = "active"
        except RbacError as e:
            role.status, role.error = "failed", e.message
        audit(db, p.username, "role.create", f"{app.mnemonic}/{body.name}", after={"status": role.status, "error": role.error})
        await db.commit()
        return ok(RoleOut.model_validate(role))

    async def list_roles(self, application_id: str | None = None, status: str | None = None, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
        q = select(Role)
        if application_id:
            q = q.where(Role.application_id == application_id)
        if status:
            q = q.where(Role.status == status)
        return ok([RoleOut.model_validate(r) for r in await db.scalars(q)])

    async def get_role_permissions(self, role_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(VIEWER, OPERATOR))):
        """Read live from the managed service."""
        role = await get_role(db, role_id)
        async with ServiceAdapter(await get_app(db, role.application_id), p.token) as svc:
            return ok(await svc.get_permissions(role.name))

    async def set_role_permissions(self, role_id: str, body: schemas.RolePermissionsIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.3: validate against the allow-list, then push to the service (the service is the source of truth)."""
        role = await get_role(db, role_id)
        if role.status not in ("active", "imported"):
            raise RbacError("ROLE_NOT_ACTIVE", "Accept or activate the role before setting permissions", 409)
        app = await get_app(db, role.application_id)

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
            before = await svc.get_permissions(role.name)
            await svc.set_permissions(role.name, pairs)
        audit(db, p.username, "role.permissions.set", f"{app.mnemonic}/{role.name}", before={"count": len(before)}, after={"permissions": sorted(wanted)})
        await db.commit()
        return ok(pairs)

    async def accept_role(self, role_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 5.1 step 6: the operator accepts an imported role; the central RBAC owns it from then on."""
        role = await get_role(db, role_id)
        if role.status != "imported":
            raise RbacError("NOT_IMPORTED", "Only imported roles can be accepted", 409)
        role.status = "active"
        audit(db, p.username, "role.accept", f"{role.application_id}/{role.name}", before={"status": "imported"}, after={"status": "active"})
        await db.commit()
        return ok(RoleOut.model_validate(role))

    async def disable_role(self, role_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        role = await get_role(db, role_id)
        app = await get_app(db, role.application_id)
        async with ServiceAdapter(app, p.token) as svc:
            await svc.disable_role(role.name)
        before = {"status": role.status}
        role.status = "disabled"
        audit(db, p.username, "role.disable", f"{app.mnemonic}/{role.name}", before=before, after={"status": "disabled"})
        await db.commit()
        return ok(RoleOut.model_validate(role))
