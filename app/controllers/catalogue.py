from fastapi import Depends
from openg2p_fastapi_common.controller import BaseController
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.adapters.service import ServiceAdapter
from app.auth import OPERATOR, VIEWER, Principal, require
from app.config import Settings
from app.db import get_session
from app.envelope import RbacError, ok
from app.keycloak import keycloak
from app.schemas import ApplicationCreatedOut, ApplicationOut, RoleOut
from app.services.common import MANAGED_ATTRIBUTE, audit, check_realm, get_app, managed_clients

_config = Settings.get_config(strict=False)
CENTRAL_ADMIN_ROLE = "OAN Administrator"


class CatalogueController(BaseController):
    """An application is a Keycloak client of one service: RBAC marks it as managed and keeps the service's admin
    API URL as its root URL. Its roles are the client's roles; their permissions live in the service. Nothing about
    either is stored here."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["catalogue"]
        self.router.add_api_route("/realms/list", self.list_realms, methods=["GET"])
        self.router.add_api_route("/applications/create", self.create_application, methods=["POST"])
        self.router.add_api_route("/applications/list", self.list_applications, methods=["GET"])
        self.router.add_api_route("/applications/{realm}/{client_id}/disable", self.disable_application, methods=["POST"])
        self.router.add_api_route("/applications/{realm}/{client_id}/roles/list", self.list_roles, methods=["GET"])
        self.router.add_api_route("/applications/{realm}/{client_id}/roles/create", self.create_role, methods=["POST"])
        self.router.add_api_route("/applications/{realm}/{client_id}/roles/{name}/delete", self.delete_role, methods=["POST"])
        self.router.add_api_route("/applications/{realm}/{client_id}/roles/{name}/permissions", self.get_role_permissions, methods=["GET"])
        self.router.add_api_route("/applications/{realm}/{client_id}/roles/{name}/permissions", self.set_role_permissions, methods=["POST"])

    # --- realms ---------------------------------------------------------------
    async def list_realms(self, _: Principal = Depends(require(VIEWER, OPERATOR))):
        """The realms RBAC manages: the `managed_realms` setting."""
        return ok([{"name": r} for r in _config.managed_realms])

    # --- applications -----------------------------------------------------------
    async def create_application(self, body: schemas.ApplicationIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.1. Creates the service's client, or takes over an existing one: marks it as managed and sets its
        root URL to the admin API. The root URL also resolves the client's relative redirect URIs, so a client people
        log in through must not be registered. The client secret is returned once and never stored."""
        check_realm(body.realm)
        existing = await keycloak.find_client(body.realm, body.client_id)
        if existing and (existing.get("attributes") or {}).get(MANAGED_ATTRIBUTE) == "true":
            raise RbacError("APP_EXISTS", "Application already registered in this realm", 409)

        if existing:
            client_uuid = existing["id"]
            rep = {**existing, "rootUrl": body.admin_api_url, "attributes": {**(existing.get("attributes") or {}), MANAGED_ATTRIBUTE: "true"}}
            if body.description is not None:
                rep["description"] = body.description
            await keycloak.request("PUT", f"/{body.realm}/clients/{client_uuid}", json=rep)
        else:
            r = await keycloak.request(
                "POST", f"/{body.realm}/clients", expect=(201,),
                json={
                    "clientId": body.client_id, "description": body.description or "", "enabled": True,
                    "publicClient": False, "serviceAccountsEnabled": True, "standardFlowEnabled": False,
                    "rootUrl": body.admin_api_url, "attributes": {MANAGED_ATTRIBUTE: "true"},
                },
            )
            client_uuid = r.headers["Location"].rsplit("/", 1)[1]
        # A public client (an existing one may be) has no secret.
        secret = (await keycloak.request("GET", f"/{body.realm}/clients/{client_uuid}/client-secret")).json().get("value")

        # The operator administers the application from now on: the role is created on its own client and granted to
        # them, so a token they log in with next carries it and the service maps it to its Frappe role of that name.
        await keycloak.create_client_role(body.realm, client_uuid, CENTRAL_ADMIN_ROLE)
        if body.realm == _config.auth_realm:
            role = next(r for r in await keycloak.list_client_roles(body.realm, client_uuid) if r["name"] == CENTRAL_ADMIN_ROLE)
            await keycloak.add_client_roles(body.realm, p.sub, client_uuid, [role])

        audit(db, p.username, "application.create", f"{body.realm}/{body.client_id}", after={"admin_api_url": body.admin_api_url})
        await db.commit()
        client = await get_app(body.realm, body.client_id)
        return ok(ApplicationCreatedOut(**ApplicationOut.of(body.realm, client).model_dump(), client_secret=secret))

    async def list_applications(self, realm: str | None = None, _: Principal = Depends(require(VIEWER, OPERATOR))):
        realms = [check_realm(realm)] if realm else _config.managed_realms
        return ok([ApplicationOut.of(r, c) for r in realms for c in await managed_clients(r)])

    async def disable_application(self, realm: str, client_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Disabling the Keycloak client is the enforcement: no token can be issued for it."""
        client = await get_app(realm, client_id)
        await keycloak.request("PUT", f"/{realm}/clients/{client['id']}", json={**client, "enabled": False})
        audit(db, p.username, "application.disable", f"{realm}/{client_id}", before={"enabled": client.get("enabled", True)}, after={"enabled": False})
        await db.commit()
        return ok(ApplicationOut.of(realm, {**client, "enabled": False}))

    # --- roles ------------------------------------------------------------------
    async def list_roles(self, realm: str, client_id: str, _: Principal = Depends(require(VIEWER, OPERATOR))):
        """The client roles of the application in Keycloak, whoever created them."""
        client = await get_app(realm, client_id)
        reps = await keycloak.list_client_roles(realm, client["id"])
        return ok([RoleOut(client_id=client_id, name=r["name"], description=r.get("description")) for r in reps])

    async def create_role(self, realm: str, client_id: str, body: schemas.RoleIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.2: the service creates the role and its Keycloak client role; the result is read back from Keycloak."""
        client = await get_app(realm, client_id)
        if not client.get("enabled", True):
            raise RbacError("APP_NOT_ACTIVE", "Application is disabled in Keycloak", 409)
        async with ServiceAdapter(client, p.token) as svc:
            await svc.create_role(body.name)
        audit(db, p.username, "role.create", f"{realm}/{client_id}/{body.name}")
        await db.commit()
        rep = next((r for r in await keycloak.list_client_roles(realm, client["id"]) if r["name"] == body.name), None)
        if not rep:
            raise RbacError(
                "KEYCLOAK_ROLE_MISSING",
                f"{client_id} created {body.name} but not in its Keycloak client; set admin_client_id and "
                "admin_client_secret on the service's login option",
                502,
            )
        return ok(RoleOut(client_id=client_id, name=rep["name"], description=rep.get("description")))

    async def delete_role(self, realm: str, client_id: str, name: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """The service deletes the role and its Keycloak client role."""
        async with ServiceAdapter(await get_app(realm, client_id), p.token) as svc:
            await svc.delete_role(name)
        audit(db, p.username, "role.delete", f"{realm}/{client_id}/{name}")
        await db.commit()
        return ok({"client_id": client_id, "name": name})

    async def get_role_permissions(self, realm: str, client_id: str, name: str, p: Principal = Depends(require(VIEWER, OPERATOR))):
        """Read live from the managed service."""
        async with ServiceAdapter(await get_app(realm, client_id), p.token) as svc:
            return ok(await svc.get_permissions(name))

    async def set_role_permissions(self, realm: str, client_id: str, name: str, body: schemas.RolePermissionsIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
        """Section 3.3: the service decides what may be granted and is the source of truth for what is."""
        pairs = sorted({(x.resource, x.action) for x in body.permissions})
        wanted = [{"resource": r, "action": a} for r, a in pairs]
        async with ServiceAdapter(await get_app(realm, client_id), p.token) as svc:
            before = await svc.get_permissions(name)
            await svc.set_permissions(name, wanted)
        audit(db, p.username, "role.permissions.set", f"{realm}/{client_id}/{name}", before={"count": len(before)}, after={"permissions": pairs})
        await db.commit()
        return ok(wanted)
