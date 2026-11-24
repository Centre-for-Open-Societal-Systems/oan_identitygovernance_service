"""Keycloak -> central RBAC sync (section 5). Read-only on Keycloak and on services."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keycloak import keycloak
from app.models import Application, Role, SyncRun
from app.services.common import audit


async def first_sync(db: AsyncSession, actor: str, app: Application, client_uuid: str) -> SyncRun:
    """Section 5.1: import the client's existing roles as `imported`. Users stay in Keycloak and are read live."""
    run = SyncRun(realm=app.realm, direction="keycloak_to_rbac", counts={}, errors=[])
    db.add(run)
    roles_new = 0
    try:
        for kr in await keycloak.list_client_roles(app.realm, client_uuid):
            role = await db.scalar(select(Role).where(Role.application_id == app.id, Role.name == kr["name"]))
            if not role:
                db.add(Role(application_id=app.id, name=kr["name"], keycloak_role_id=kr["id"], status="imported"))
                roles_new += 1
            elif not role.keycloak_role_id:
                role.keycloak_role_id = kr["id"]
    except Exception as e:  # recorded on the run; a failed sync never half-applies silently
        run.errors = [str(e)]
    run.counts = {"roles_imported": roles_new}
    run.finished = datetime.now(timezone.utc)
    audit(db, actor, "sync.first", f"{app.realm}/{app.mnemonic}", after=run.counts)
    await db.commit()
    return run
