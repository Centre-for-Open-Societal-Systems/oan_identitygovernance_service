"""Calls to a managed service's admin API. The service writes its own role to Keycloak."""
import os

import httpx

from app.config import settings
from app.envelope import RbacError
from app.models import Application


class ServiceAdapter:
    """Contract a managed service's admin API must meet (versioned in the service's own docs):

    POST   {admin_api_url}/roles                 {name, description}      -> {keycloak_role_id}
    DELETE {admin_api_url}/roles/{name}
    PUT    {admin_api_url}/roles/{name}/permissions   {permissions: [{resource, action}]}
    GET    {admin_api_url}/roles/{name}/permissions   -> {permissions: [...]}
    """

    def __init__(self, app: Application) -> None:
        if not app.admin_api_url:
            raise RbacError("NO_ADMIN_API", f"Application {app.mnemonic} has no admin_api_url", 409)
        headers = {}
        if app.admin_api_secret_ref:
            token = os.environ.get(app.admin_api_secret_ref)
            if not token:
                raise RbacError("NO_SERVICE_CREDENTIAL", f"Secret {app.admin_api_secret_ref} is not set", 500)
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=app.admin_api_url.rstrip("/"), headers=headers, timeout=settings.http_timeout)

    async def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise RbacError("SERVICE_UNREACHABLE", str(e), 502)
        if r.status_code >= 400:
            raise RbacError("SERVICE_ERROR", f"{method} {path} -> {r.status_code}: {r.text[:300]}", 502)
        return r.json() if r.content else {}

    async def create_role(self, name: str, description: str | None) -> str | None:
        data = await self._call("POST", "/roles", json={"name": name, "description": description})
        return data.get("keycloak_role_id")

    async def delete_role(self, name: str) -> None:
        await self._call("DELETE", f"/roles/{name}")

    async def set_permissions(self, name: str, permissions: list[dict]) -> None:
        await self._call("PUT", f"/roles/{name}/permissions", json={"permissions": permissions})

    async def get_permissions(self, name: str) -> list[dict]:
        return (await self._call("GET", f"/roles/{name}/permissions")).get("permissions", [])

    async def aclose(self) -> None:
        await self._http.aclose()
