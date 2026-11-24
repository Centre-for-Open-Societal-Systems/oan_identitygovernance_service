"""Keycloak -> central RBAC sync (section 5). Read-only on Keycloak and on services."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keycloak import keycloak
from app.models import Application, Role, SyncRun, User, UserRole
from app.services.audit import audit


async def first_sync(db: AsyncSession, actor: str, app: Application) -> SyncRun:
    """Section 5.1: import the client's existing roles and the users holding them as `imported`."""
    run = SyncRun(realm=app.realm, direction="keycloak_to_rbac", counts={}, errors=[])
    db.add(run)
    roles_new = users_new = links_new = 0
    try:
        kc_roles = await keycloak.list_client_roles(app.realm, app.keycloak_client_uuid)
        for kr in kc_roles:
            role = await db.scalar(select(Role).where(Role.application_id == app.id, Role.name == kr["name"]))
            if not role:
                role = Role(
                    application_id=app.id, name=kr["name"], description=kr.get("description"),
                    keycloak_role_id=kr["id"], status="imported",
                )
                db.add(role)
                await db.flush()
                roles_new += 1
            elif not role.keycloak_role_id:
                role.keycloak_role_id = kr["id"]
            for ku in await keycloak.role_users(app.realm, app.keycloak_client_uuid, kr["name"]):
                user = await db.scalar(select(User).where(User.realm == app.realm, User.keycloak_user_id == ku["id"]))
                if not user:
                    user = User(
                        realm=app.realm, keycloak_user_id=ku["id"], username=ku["username"],
                        email=ku.get("email"), status="active" if ku.get("enabled", True) else "disabled",
                    )
                    db.add(user)
                    await db.flush()
                    users_new += 1
                if not await db.get(UserRole, (user.id, role.id)):
                    db.add(UserRole(user_id=user.id, role_id=role.id))
                    links_new += 1
    except Exception as e:  # recorded on the run; a failed sync never half-applies silently
        run.errors = [str(e)]
    run.counts = {"roles_imported": roles_new, "users_imported": users_new, "user_roles": links_new}
    run.finished = datetime.now(timezone.utc)
    audit(db, actor, "sync.first", f"{app.realm}/{app.mnemonic}", after=run.counts)
    await db.commit()
    return run
