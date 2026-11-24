from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.service import ServiceAdapter
from app.envelope import RbacError
from app.models import AllowedPermission, Permission, Role, RolePermission
from app.services.applications import get_app
from app.services.audit import audit


async def get_role(db: AsyncSession, role_id: str) -> Role:
    role = await db.get(Role, role_id)
    if not role:
        raise RbacError("ROLE_NOT_FOUND", "Role not found", 404)
    return role


async def create_role(db: AsyncSession, actor: str, application_id: str, name: str, description: str | None) -> Role:
    """Section 3.2: pending row, service creates the role (and its Keycloak client role), then active/failed."""
    app = await get_app(db, application_id)
    if app.status != "active":
        raise RbacError("APP_NOT_ACTIVE", "Application is not active", 409)
    existing = await db.scalar(select(Role).where(Role.application_id == app.id, Role.name == name))
    if existing and existing.status != "failed":
        raise RbacError("ROLE_EXISTS", "Role already exists for this application", 409)
    role = existing or Role(application_id=app.id, name=name)
    role.description, role.status, role.error = description, "pending", None
    db.add(role)
    await db.commit()

    adapter = ServiceAdapter(app)
    try:
        role.keycloak_role_id = await adapter.create_role(name, description)
        role.status = "active"
    except RbacError as e:
        role.status, role.error = "failed", e.message
    finally:
        await adapter.aclose()
    audit(db, actor, "role.create", f"{app.mnemonic}/{name}", after={"status": role.status, "error": role.error})
    await db.commit()
    return role


async def disable_role(db: AsyncSession, actor: str, role_id: str) -> Role:
    role = await get_role(db, role_id)
    app = await get_app(db, role.application_id)
    adapter = ServiceAdapter(app)
    try:
        await adapter.delete_role(role.name)
    finally:
        await adapter.aclose()
    before = {"status": role.status}
    role.status = "disabled"
    audit(db, actor, "role.disable", f"{app.mnemonic}/{role.name}", before=before, after={"status": "disabled"})
    await db.commit()
    return role


async def set_permissions(db: AsyncSession, actor: str, role_id: str, pairs: list[dict]) -> Role:
    """Section 3.3: validate against the allow-list, store, then push to the service."""
    role = await get_role(db, role_id)
    if role.status not in ("active", "imported"):
        raise RbacError("ROLE_NOT_ACTIVE", "Accept or activate the role before setting permissions", 409)
    app = await get_app(db, role.application_id)

    allowed = {
        (a.resource, a.action)
        for a in (await db.scalars(select(AllowedPermission).where(AllowedPermission.application_id == app.id)))
    }
    wanted = {(p["resource"], p["action"]) for p in pairs}
    rejected = sorted(wanted - allowed)
    if rejected:
        raise RbacError("NOT_ALLOWED", f"Not in the allow-list for {app.mnemonic}: {rejected}", 422)

    perm_ids = []
    for resource, action in sorted(wanted):
        perm = await db.scalar(
            select(Permission).where(Permission.application_id == app.id, Permission.resource == resource, Permission.action == action)
        )
        if not perm:
            perm = Permission(application_id=app.id, resource=resource, action=action)
            db.add(perm)
            await db.flush()
        perm_ids.append(perm.id)

    old = [rp.permission_id for rp in await db.scalars(select(RolePermission).where(RolePermission.role_id == role.id))]
    adapter = ServiceAdapter(app)
    try:
        await adapter.set_permissions(role.name, [{"resource": r, "action": a} for r, a in sorted(wanted)])
    finally:
        await adapter.aclose()

    for pid in set(old) - set(perm_ids):
        await db.delete(await db.get(RolePermission, (role.id, pid)))
    for pid in set(perm_ids) - set(old):
        db.add(RolePermission(role_id=role.id, permission_id=pid))
    role.permissions_recorded = True
    audit(db, actor, "role.permissions.set", f"{app.mnemonic}/{role.name}", before={"count": len(old)}, after={"permissions": sorted(wanted)})
    await db.commit()
    return role


async def accept_imported(db: AsyncSession, actor: str, role_id: str) -> Role:
    """Section 5.1 step 6: the operator accepts an imported role; the central RBAC owns it from then on."""
    role = await get_role(db, role_id)
    if role.status != "imported":
        raise RbacError("NOT_IMPORTED", "Only imported roles can be accepted", 409)
    role.status = "active"
    audit(db, actor, "role.accept", f"{role.application_id}/{role.name}", before={"status": "imported"}, after={"status": "active"})
    await db.commit()
    return role
