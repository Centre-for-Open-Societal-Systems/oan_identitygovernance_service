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

    async def request(self, method: str, path: str, expect: tuple[int, ...] = (200, 201, 204), **kw: Any) -> httpx.Response:
        """Call `/admin/realms{path}`. Any status outside `expect` becomes a 502."""
        try:
            if not self._token or time.monotonic() >= self._expires - 15:
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
                self._token = r.json()["access_token"]
                self._expires = time.monotonic() + r.json()["expires_in"]
            r = await self._http.request(method, f"/admin/realms{path}", headers={"Authorization": f"Bearer {self._token}"}, **kw)
        except httpx.HTTPError as e:
            raise RbacError("KEYCLOAK_UNREACHABLE", str(e), 502)
        if r.status_code not in expect:
            raise RbacError("KEYCLOAK_ERROR", f"{method} {path} -> {r.status_code}: {r.text[:300]}", 502)
        return r

    async def find_client(self, realm: str, client_id: str) -> dict | None:
        r = await self.request("GET", f"/{realm}/clients", params={"clientId": client_id})
        return next(iter(r.json()), None)

    async def list_client_roles(self, realm: str, client_uuid: str) -> list[dict]:
        return (await self.request("GET", f"/{realm}/clients/{client_uuid}/roles", params={"max": 1000})).json()

    async def get_user(self, realm: str, user_id: str) -> dict:
        r = await self.request("GET", f"/{realm}/users/{user_id}", expect=(200, 404))
        if r.status_code == 404:
            raise RbacError("USER_NOT_FOUND", "User not found", 404)
        return r.json()

    async def add_client_roles(self, realm: str, user_id: str, client_uuid: str, roles: list[dict]) -> None:
        await self.request("POST", f"/{realm}/users/{user_id}/role-mappings/clients/{client_uuid}", json=roles)


keycloak = Keycloak()
