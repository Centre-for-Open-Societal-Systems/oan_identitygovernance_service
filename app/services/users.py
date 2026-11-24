from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.envelope import RbacError
from app.keycloak import keycloak
from app.models import Application, Realm, Role, User, UserRole
from app.services.audit import audit


async def _resolve_roles(db: AsyncSession, realm: str, role_ids: list[str]) -> list[tuple[Role, Application]]:
    out = []
    for rid in role_ids:
        role = await db.get(Role, rid)
        if not role:
            raise RbacError("ROLE_NOT_FOUND", f"Role {rid} not found", 404)
        app = await db.get(Application, role.application_id)
        if app.realm != realm:
            raise RbacError("ROLE_REALM", f"Role {role.name} belongs to realm {app.realm}, not {realm}", 422)
        if role.status not in ("active", "imported"):
            raise RbacError("ROLE_NOT_ACTIVE", f"Role {role.name} is {role.status}", 409)
        out.append((role, app))
    return out


async def _kc_role_reps(realm: str, app: Application, names: list[str]) -> list[dict]:
    by_name = {r["name"]: r for r in await keycloak.list_client_roles(realm, app.keycloak_client_uuid)}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise RbacError("KEYCLOAK_ROLE_MISSING", f"Roles missing in Keycloak client {app.mnemonic}: {missing}", 409)
    return [by_name[n] for n in names]


async def create_user(db: AsyncSession, actor: str, data: dict) -> User:
    """Section 4: validate, create in Keycloak, map roles, store light copy, audit."""
    realm = data["realm"]
    if not await db.get(Realm, realm):
        raise RbacError("REALM_NOT_FOUND", f"Realm {realm} is not registered", 404)
    resolved = await _resolve_roles(db, realm, data.get("role_ids", []))

    kc_body = {
        "username": data["username"], "email": data.get("email"),
        "firstName": data.get("first_name"), "lastName": data.get("last_name"),
        "enabled": True, "groups": data.get("groups", []),
        "attributes": {"location_codes": data.get("location_codes", [])},
    }
    kc_id = await keycloak.create_user(realm, kc_body)

    by_app: dict[str, tuple[Application, list[str]]] = {}
    for role, app in resolved:
        by_app.setdefault(app.id, (app, []))[1].append(role.name)
    for app, names in by_app.values():
        await keycloak.add_client_roles(realm, kc_id, app.keycloak_client_uuid, await _kc_role_reps(realm, app, names))
    if data.get("send_invite", True):
        await keycloak.send_actions_email(realm, kc_id, ["UPDATE_PASSWORD", "VERIFY_EMAIL"])

    user = await db.scalar(select(User).where(User.realm == realm, User.keycloak_user_id == kc_id))
    if not user:
        user = User(realm=realm, keycloak_user_id=kc_id, username=data["username"], email=data.get("email"))
        db.add(user)
        await db.flush()
    user.groups, user.location_codes = data.get("groups", []), data.get("location_codes", [])
    for role, _ in resolved:
        if not await db.get(UserRole, (user.id, role.id)):
            db.add(UserRole(user_id=user.id, role_id=role.id))
    audit(db, actor, "user.create", f"{realm}/{data['username']}", after={"keycloak_id": kc_id, "roles": [r.name for r, _ in resolved]})
    await db.commit()
    return user


async def set_status(db: AsyncSession, actor: str, user_id: str, enabled: bool) -> User:
    """Keycloak first, then the registry follows."""
    user = await db.get(User, user_id)
    if not user:
        raise RbacError("USER_NOT_FOUND", "User not found", 404)
    await keycloak.set_user_enabled(user.realm, user.keycloak_user_id, enabled)
    before = {"status": user.status}
    user.status = "active" if enabled else "disabled"
    audit(db, actor, "user.enable" if enabled else "user.disable", f"{user.realm}/{user.username}", before=before, after={"status": user.status})
    await db.commit()
    return user


async def assign_role(db: AsyncSession, actor: str, user_id: str, role_id: str, grant: bool) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise RbacError("USER_NOT_FOUND", "User not found", 404)
    (role, app), = await _resolve_roles(db, user.realm, [role_id])
    reps = await _kc_role_reps(user.realm, app, [role.name])
    link = await db.get(UserRole, (user.id, role.id))
    if grant:
        await keycloak.add_client_roles(user.realm, user.keycloak_user_id, app.keycloak_client_uuid, reps)
        if not link:
            db.add(UserRole(user_id=user.id, role_id=role.id))
    else:
        await keycloak.remove_client_roles(user.realm, user.keycloak_user_id, app.keycloak_client_uuid, reps)
        if link:
            await db.delete(link)
    audit(db, actor, "user.role.grant" if grant else "user.role.revoke", f"{user.realm}/{user.username}", after={"role": f"{app.mnemonic}/{role.name}"})
    await db.commit()
    return user
