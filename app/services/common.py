"""Lookups and the audit write shared by several controllers and services."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.envelope import RbacError
from app.keycloak import keycloak
from app.models import Application, AuditLog


async def get_app(db: AsyncSession, app_id: str) -> Application:
    app = await db.get(Application, app_id)
    if not app:
        raise RbacError("APP_NOT_FOUND", "Application not found", 404)
    return app


async def get_client(app: Application) -> dict:
    """The application's Keycloak client (uuid, enabled, description), read live."""
    client = await keycloak.find_client(app.realm, app.mnemonic)
    if not client:
        raise RbacError("CLIENT_MISSING", f"Keycloak client {app.mnemonic} not found in realm {app.realm}", 409)
    return client


def audit(db: AsyncSession, actor: str, action: str, target: str, before: dict | None = None, after: dict | None = None) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, before=before, after=after))
