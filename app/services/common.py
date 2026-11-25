"""Lookups and the audit write shared by several controllers and services."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.envelope import RbacError
from app.keycloak import keycloak
from app.models import AuditLog

_config = Settings.get_config(strict=False)

# Client attribute marking a Keycloak client as an application RBAC manages. Its root URL is the service's admin API.
MANAGED_ATTRIBUTE = "rbac.managed"


def check_realm(realm: str) -> str:
    if realm not in _config.managed_realms:
        raise RbacError("REALM_NOT_MANAGED", f"Realm {realm} is not managed by RBAC", 404)
    return realm


def is_managed(client: dict) -> bool:
    return (client.get("attributes") or {}).get(MANAGED_ATTRIBUTE) == "true"


async def managed_clients(realm: str) -> list[dict]:
    """The realm's clients marked as applications, read live."""
    clients = (await keycloak.request("GET", f"/{realm}/clients", params={"max": 1000})).json()
    return [c for c in clients if is_managed(c)]


async def get_app(realm: str, client_id: str) -> dict:
    """The application's Keycloak client (uuid, enabled, description, rootUrl = admin API), read live."""
    check_realm(realm)
    client = await keycloak.find_client(realm, client_id)
    if not client or not is_managed(client):
        raise RbacError("APP_NOT_FOUND", f"Application {client_id} not found in realm {realm}", 404)
    return client


def audit(db: AsyncSession, actor: str, action: str, target: str, before: dict | None = None, after: dict | None = None) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, before=before, after=after))
