"""Keycloak admin client. Authenticates as the central-rbac service account, never as the caller."""
import time
from typing import Any

import httpx

from app.config import Settings
from app.envelope import RbacError

_config = Settings.get_config(strict=False)


class Keycloak:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=_config.keycloak_url, timeout=_config.http_timeout)
        self._token: str | None = None
        self._expires = 0.0

    async def _bearer(self) -> str:
        if self._token and time.monotonic() < self._expires - 15:
            return self._token
        r = await self._http.post(
            f"/realms/{_config.keycloak_admin_realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": _config.keycloak_client_id,
                "client_secret": _config.keycloak_client_secret,
            },
        )
        if r.status_code != 200:
            raise RbacError("KEYCLOAK_AUTH", f"Service account login failed ({r.status_code})", 502)
        data = r.json()
        self._token = data["access_token"]
        self._expires = time.monotonic() + data["expires_in"]
        return self._token

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._bearer()}"}
        try:
            r = await self._http.request(method, f"/admin/realms{path}", headers=headers, **kw)
        except httpx.HTTPError as e:
            raise RbacError("KEYCLOAK_UNREACHABLE", str(e), 502)
        return r

    async def _ok(self, method: str, path: str, expect: tuple[int, ...] = (200, 201, 204), **kw: Any) -> httpx.Response:
        r = await self._call(method, path, **kw)
        if r.status_code not in expect:
            raise RbacError("KEYCLOAK_ERROR", f"{method} {path} -> {r.status_code}: {r.text[:300]}", 502)
        return r

    # --- clients -------------------------------------------------------------
    async def find_client(self, realm: str, client_id: str) -> dict | None:
        r = await self._ok("GET", f"/{realm}/clients", params={"clientId": client_id})
        return next(iter(r.json()), None)

    async def create_client(self, realm: str, client_id: str, description: str | None) -> str:
        """Create a confidential client with a service account. Returns the client UUID."""
        body = {
            "clientId": client_id,
            "description": description or "",
            "enabled": True,
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
        }
        r = await self._ok("POST", f"/{realm}/clients", json=body, expect=(201, 409))
        if r.status_code == 409:
            existing = await self.find_client(realm, client_id)
            if not existing:
                raise RbacError("KEYCLOAK_ERROR", "Client reported as existing but not found", 502)
            return existing["id"]
        return r.headers["Location"].rsplit("/", 1)[1]

    async def delete_client(self, realm: str, client_uuid: str) -> None:
        await self._ok("DELETE", f"/{realm}/clients/{client_uuid}", expect=(204, 404))

    async def client_secret(self, realm: str, client_uuid: str) -> str:
        r = await self._ok("GET", f"/{realm}/clients/{client_uuid}/client-secret")
        return r.json()["value"]

    # --- roles ---------------------------------------------------------------
    async def list_client_roles(self, realm: str, client_uuid: str) -> list[dict]:
        r = await self._ok("GET", f"/{realm}/clients/{client_uuid}/roles", params={"max": 1000})
        return r.json()

    async def role_users(self, realm: str, client_uuid: str, role: str) -> list[dict]:
        r = await self._ok("GET", f"/{realm}/clients/{client_uuid}/roles/{role}/users", params={"max": 1000})
        return r.json()

    # --- users ---------------------------------------------------------------
    async def find_user(self, realm: str, email: str) -> dict | None:
        r = await self._ok("GET", f"/{realm}/users", params={"email": email, "exact": "true"})
        return next(iter(r.json()), None)

    async def create_user(self, realm: str, body: dict) -> str:
        r = await self._ok("POST", f"/{realm}/users", json=body, expect=(201, 409))
        if r.status_code == 409:
            existing = await self.find_user(realm, body.get("email", ""))
            if not existing:
                raise RbacError("KEYCLOAK_CONFLICT", "Username or email already used by another user", 409)
            return existing["id"]
        return r.headers["Location"].rsplit("/", 1)[1]

    async def set_user_enabled(self, realm: str, user_id: str, enabled: bool) -> None:
        await self._ok("PUT", f"/{realm}/users/{user_id}", json={"enabled": enabled})

    async def add_client_roles(self, realm: str, user_id: str, client_uuid: str, roles: list[dict]) -> None:
        await self._ok("POST", f"/{realm}/users/{user_id}/role-mappings/clients/{client_uuid}", json=roles)

    async def remove_client_roles(self, realm: str, user_id: str, client_uuid: str, roles: list[dict]) -> None:
        await self._ok("DELETE", f"/{realm}/users/{user_id}/role-mappings/clients/{client_uuid}", json=roles)

    async def send_actions_email(self, realm: str, user_id: str, actions: list[str]) -> None:
        await self._ok("PUT", f"/{realm}/users/{user_id}/execute-actions-email", json=actions)


keycloak = Keycloak()
