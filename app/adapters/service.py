"""Calls to a managed service's admin API. The service writes its own role to Keycloak."""
import os

import httpx

from app.config import Settings
from app.envelope import RbacError
from app.models import Application

_config = Settings.get_config(strict=False)


class ServiceAdapter:
    """Contract a managed service's admin API must meet (versioned in the service's own docs):

    POST   {admin_api_url}/roles                 {name, description}      -> {keycloak_role_id}
    DELETE {admin_api_url}/roles/{name}
    PUT    {admin_api_url}/roles/{name}/permissions   {permissions: [{resource, action}]}
    GET    {admin_api_url}/roles/{name}/permissions   -> {permissions: [...]}

    Use as `async with ServiceAdapter(app) as svc:`.
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
        self._http = httpx.AsyncClient(base_url=app.admin_api_url.rstrip("/"), headers=headers, timeout=_config.http_timeout)

    async def __aenter__(self) -> "ServiceAdapter":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, **kw) -> dict:
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise RbacError("SERVICE_UNREACHABLE", str(e), 502)
        if r.status_code >= 400:
            raise RbacError("SERVICE_ERROR", f"{method} {path} -> {r.status_code}: {r.text[:300]}", 502)
        return r.json() if r.content else {}

    async def get_permissions(self, name: str) -> list[dict]:
        return (await self.request("GET", f"/roles/{name}/permissions")).get("permissions", [])
