"""User writes shared by the single and bulk endpoints. Users live in Keycloak; nothing about a user is stored here."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.envelope import RbacError
from app.keycloak import keycloak
from app.models import Application, Realm
from app.schemas import UserOut
from app.services.common import audit, get_app, get_client


async def _resolve_roles(db: AsyncSession, realm: str, refs: list[dict]) -> list[tuple[str, Application]]:
    """(name, application) per reference. Whether the role exists is Keycloak's answer, checked when it is mapped."""
    out = []
    for ref in refs:
        app = await get_app(db, ref["application_id"])
        if app.realm != realm:
            raise RbacError("ROLE_REALM", f"Role {ref['name']} belongs to realm {app.realm}, not {realm}", 422)
        out.append((ref["name"], app))
    return out


async def _kc_role_reps(realm: str, client_uuid: str, app: Application, names: list[str]) -> list[dict]:
    by_name = {r["name"]: r for r in await keycloak.list_client_roles(realm, client_uuid)}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise RbacError("KEYCLOAK_ROLE_MISSING", f"Roles missing in Keycloak client {app.mnemonic}: {missing}", 409)
    return [by_name[n] for n in names]


async def create_user(db: AsyncSession, actor: str, data: dict) -> UserOut:
    """Section 4: validate, create in Keycloak, map roles, invite, audit."""
    realm = data["realm"]
    if not await db.get(Realm, realm):
        raise RbacError("REALM_NOT_FOUND", f"Realm {realm} is not registered", 404)
    resolved = await _resolve_roles(db, realm, data.get("roles", []))

    body = {
        "username": data["username"], "email": data.get("email"),
        "firstName": data.get("first_name"), "lastName": data.get("last_name"),
        "enabled": True, "groups": data.get("groups", []),
        "attributes": {"location_codes": data.get("location_codes", [])},
    }
    r = await keycloak.request("POST", f"/{realm}/users", json=body, expect=(201, 409))
    if r.status_code == 201:
        kc_id = r.headers["Location"].rsplit("/", 1)[1]
    else:  # already exists: re-running is safe
        found = await keycloak.request("GET", f"/{realm}/users", params={"email": data.get("email", ""), "exact": "true"})
        if not found.json():
            raise RbacError("KEYCLOAK_CONFLICT", "Username or email already used by another user", 409)
        kc_id = found.json()[0]["id"]

    by_app: dict[str, tuple[Application, list[str]]] = {}
    for name, app in resolved:
        by_app.setdefault(app.id, (app, []))[1].append(name)
    for app, names in by_app.values():
        client_uuid = (await get_client(app))["id"]
        await keycloak.add_client_roles(realm, kc_id, client_uuid, await _kc_role_reps(realm, client_uuid, app, names))
    if data.get("send_invite", True):
        await keycloak.request("PUT", f"/{realm}/users/{kc_id}/execute-actions-email", json=["UPDATE_PASSWORD", "VERIFY_EMAIL"])

    audit(db, actor, "user.create", f"{realm}/{data['username']}", after={"keycloak_id": kc_id, "roles": [f"{a.mnemonic}/{n}" for n, a in resolved]})
    await db.commit()
    return UserOut.of(await keycloak.get_user(realm, kc_id))


async def set_status(db: AsyncSession, actor: str, realm: str, user_id: str, enabled: bool) -> UserOut:
    before = await keycloak.get_user(realm, user_id)
    await keycloak.request("PUT", f"/{realm}/users/{user_id}", json={"enabled": enabled})
    audit(db, actor, "user.enable" if enabled else "user.disable", f"{realm}/{before.get('username')}", before={"enabled": before.get("enabled")}, after={"enabled": enabled})
    await db.commit()
    return UserOut.of(await keycloak.get_user(realm, user_id))


async def assign_role(db: AsyncSession, actor: str, realm: str, user_id: str, ref: dict, grant: bool) -> UserOut:
    user = await keycloak.get_user(realm, user_id)
    (name, app), = await _resolve_roles(db, realm, [ref])
    client_uuid = (await get_client(app))["id"]
    reps = await _kc_role_reps(realm, client_uuid, app, [name])
    if grant:
        await keycloak.add_client_roles(realm, user_id, client_uuid, reps)
    else:
        await keycloak.request("DELETE", f"/{realm}/users/{user_id}/role-mappings/clients/{client_uuid}", json=reps)
    audit(db, actor, "user.role.grant" if grant else "user.role.revoke", f"{realm}/{user.get('username')}", after={"role": f"{app.mnemonic}/{name}"})
    await db.commit()
    return UserOut.of(user)
