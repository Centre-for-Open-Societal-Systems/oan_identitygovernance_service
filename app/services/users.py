"""User writes shared by the single and bulk endpoints. Users live in Keycloak; nothing about a user is stored here."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.envelope import RbacError
from app.keycloak import keycloak
from app.schemas import UserOut
from app.services.common import audit, check_realm, get_app


async def _resolve_roles(realm: str, refs: list[dict]) -> list[tuple[str, dict]]:
    """(name, application client) per reference, in the user's realm. Whether the role exists is Keycloak's answer,
    checked when it is mapped."""
    return [(ref["name"], await get_app(realm, ref["client_id"])) for ref in refs]


async def _kc_role_reps(realm: str, client: dict, names: list[str]) -> list[dict]:
    by_name = {r["name"]: r for r in await keycloak.list_client_roles(realm, client["id"])}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise RbacError("KEYCLOAK_ROLE_MISSING", f"Roles missing in Keycloak client {client['clientId']}: {missing}", 409)
    return [by_name[n] for n in names]


async def create_user(db: AsyncSession, actor: str, data: dict) -> UserOut:
    """Section 4: validate, create in Keycloak, map roles, invite, audit."""
    realm = data["realm"]
    check_realm(realm)
    resolved = await _resolve_roles(realm, data.get("roles", []))

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

    by_app: dict[str, tuple[dict, list[str]]] = {}
    for name, client in resolved:
        by_app.setdefault(client["id"], (client, []))[1].append(name)
    for client, names in by_app.values():
        await keycloak.add_client_roles(realm, kc_id, client["id"], await _kc_role_reps(realm, client, names))
    if data.get("send_invite", True):
        await keycloak.request("PUT", f"/{realm}/users/{kc_id}/execute-actions-email", json=["UPDATE_PASSWORD", "VERIFY_EMAIL"])

    audit(db, actor, "user.create", f"{realm}/{data['username']}", after={"keycloak_id": kc_id, "roles": [f"{c['clientId']}/{n}" for n, c in resolved]})
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
    (name, client), = await _resolve_roles(realm, [ref])
    reps = await _kc_role_reps(realm, client, [name])
    if grant:
        await keycloak.add_client_roles(realm, user_id, client["id"], reps)
    else:
        await keycloak.request("DELETE", f"/{realm}/users/{user_id}/role-mappings/clients/{client['id']}", json=reps)
    audit(db, actor, "user.role.grant" if grant else "user.role.revoke", f"{realm}/{user.get('username')}", after={"role": f"{client['clientId']}/{name}"})
    await db.commit()
    return UserOut.of(user)
