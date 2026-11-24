from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.envelope import RbacError
from app.keycloak import keycloak
from app.models import Application, Realm
from app.services.audit import audit
from app.services.sync import first_sync


async def get_app(db: AsyncSession, app_id: str) -> Application:
    app = await db.get(Application, app_id)
    if not app:
        raise RbacError("APP_NOT_FOUND", "Application not found", 404)
    return app


async def create_application(db: AsyncSession, actor: str, data: dict) -> tuple[Application, str]:
    """Section 3.1. Returns the application and the client secret (shown once, never stored)."""
    if not await db.get(Realm, data["realm"]):
        raise RbacError("REALM_NOT_FOUND", f"Realm {data['realm']} is not registered", 404)
    dup = await db.scalar(select(Application).where(Application.realm == data["realm"], Application.mnemonic == data["mnemonic"]))
    if dup:
        raise RbacError("APP_EXISTS", "Application already registered in this realm", 409)

    app = Application(**data, status="pending")
    db.add(app)
    await db.flush()
    try:
        app.keycloak_client_uuid = await keycloak.create_client(app.realm, app.mnemonic, app.description)
        secret = await keycloak.client_secret(app.realm, app.keycloak_client_uuid)
    except RbacError:
        await db.rollback()  # step 2 failed: roll back step 1
        raise
    app.status = "active"
    audit(db, actor, "application.create", f"{app.realm}/{app.mnemonic}", after={"id": app.id, "client": app.keycloak_client_uuid})
    await db.commit()
    await first_sync(db, actor, app)
    return app, secret


async def disable_application(db: AsyncSession, actor: str, app_id: str) -> Application:
    app = await get_app(db, app_id)
    before = {"status": app.status}
    app.status = "disabled"
    audit(db, actor, "application.disable", f"{app.realm}/{app.mnemonic}", before=before, after={"status": "disabled"})
    await db.commit()
    return app
