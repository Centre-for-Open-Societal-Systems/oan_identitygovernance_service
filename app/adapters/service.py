"""Calls to a managed service's admin API. The service writes its own role to Keycloak as well."""
import httpx

from app.config import Settings
from app.envelope import RbacError
from app.models import Application

_config = Settings.get_config(strict=False)
_RIGHTS = ("select", "read", "write", "create", "delete", "submit", "cancel", "amend", "print", "email", "report", "import", "export", "share")


class ServiceAdapter:
    """The admin API of `oan_auth_service` (/v1/admin on any oan_* site). Roles are Frappe roles named with the
    service prefix; a permission is a (doctype, right) pair, e.g. ("Farmer", "read"). Responses carry the payload in `data`.

    POST  /roles {role}                                  create
    DELETE /roles/{role}                                 delete
    GET   /permissions?role=                             rows with a 0/1 per right
    PUT   /permissions {role, doctype, permissions: {right: bool}}

    Called with the operator's own token: the service sees the operator, and its doctype permissions decide.
    Use as `async with ServiceAdapter(app, p.token) as svc:`.
    """

    def __init__(self, app: Application, token: str) -> None:
        if not app.admin_api_url:
            raise RbacError("NO_ADMIN_API", f"Application {app.mnemonic} has no admin_api_url", 409)
        headers = {"Authorization": f"Bearer {token}"}
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

    async def create_role(self, name: str) -> None:
        await self.request("POST", "/roles", json={"role": name})

    async def delete_role(self, name: str) -> None:
        await self.request("DELETE", f"/roles/{name}")

    async def get_permissions(self, name: str) -> list[dict]:
        rows = (await self.request("GET", "/permissions", params={"role": name})).get("data") or []
        return [
            {"resource": r["doctype"], "action": right}
            for r in rows
            if r.get("active", True) and not r.get("permlevel") and not r.get("if_owner")
            for right in _RIGHTS
            if r.get(right)
        ]

    async def set_permissions(self, name: str, pairs: list[dict]) -> None:
        """Grant `pairs` and revoke every other right the role holds on those doctypes or on any it holds today."""
        wanted = {(p["resource"], p["action"]) for p in pairs}
        current = {(p["resource"], p["action"]) for p in await self.get_permissions(name)}
        for doctype in sorted({d for d, _ in wanted | current}):
            rights = {a: (doctype, a) in wanted for a in _RIGHTS if (doctype, a) in wanted | current}
            await self.request("PUT", "/permissions", json={"role": name, "doctype": doctype, "permissions": rights})
