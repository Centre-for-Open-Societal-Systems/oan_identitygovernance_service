"""Direct operator login: authorization code + PKCE, exchanged by this backend with the client secret.

The browser never sees the client secret. Tokens live in httpOnly cookies.
"""

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import ACCESS_COOKIE, DIRECT_ISSUER, REFRESH_COOKIE, Principal, current_principal
from app.config import Settings
from app.envelope import RbacError, ok
from openg2p_fastapi_common.controller import BaseController

_config = Settings.get_config(strict=False)


_OAUTH_COOKIE = "rbac_oauth"
_oidc = f"{DIRECT_ISSUER}/protocol/openid-connect"
_redirect_uri = f"{_config.public_url}/auth/callback"


def _set(resp, name: str, value: str, max_age: int, path: str = "/") -> None:
    resp.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        httponly=True,
        secure=_config.cookie_secure,
        samesite="lax",
        domain=_config.cookie_domain,
    )


def _set_tokens(resp, tok: dict) -> None:
    _set(resp, ACCESS_COOKIE, tok["access_token"], tok["expires_in"])
    if tok.get("refresh_token"):
        # The refresh token is only ever sent to /auth.
        _set(
            resp,
            REFRESH_COOKIE,
            tok["refresh_token"],
            tok.get("refresh_expires_in") or 1800,
            path="/auth",
        )


def _clear(resp) -> None:
    resp.delete_cookie(ACCESS_COOKIE, path="/", domain=_config.cookie_domain)
    resp.delete_cookie(REFRESH_COOKIE, path="/auth", domain=_config.cookie_domain)


async def _token(data: dict) -> dict:
    data = {
        **data,
        "client_id": _config.auth_client_id,
        "client_secret": _config.keycloak_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=_config.http_timeout) as c:
            r = await c.post(f"{_oidc}/token", data=data)
    except httpx.HTTPError as e:
        raise RbacError("KEYCLOAK_UNREACHABLE", str(e), 502)
    if r.status_code != 200:
        raise RbacError(
            "LOGIN_FAILED", f"Token exchange refused ({r.status_code})", 401
        )
    return r.json()


class AuthController(BaseController):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.router.tags += ["auth"]
        self.router.prefix = "/auth"
        self.router.add_api_route("/login", self.login, methods=["GET"])
        self.router.add_api_route("/callback", self.callback, methods=["GET"])
        self.router.add_api_route("/refresh", self.refresh, methods=["POST"])
        self.router.add_api_route("/logout", self.logout, methods=["POST"])
        self.router.add_api_route("/me", self.me, methods=["GET"])

    async def login(self):
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = f"{_oidc}/auth?" + urlencode(
            {
                "client_id": _config.auth_client_id,
                "response_type": "code",
                "scope": "openid",
                "redirect_uri": _redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        resp = RedirectResponse(url)
        _set(resp, _OAUTH_COOKIE, f"{state}.{verifier}", 600, path="/auth")
        return resp

    async def callback(
        self, request: Request, code: str = "", state: str = "", error: str = ""
    ):
        if error:
            raise RbacError("LOGIN_FAILED", f"Keycloak returned: {error}", 401)
        saved = request.cookies.get(_OAUTH_COOKIE, "")
        saved_state, _, verifier = saved.partition(".")
        if not code or not verifier or not secrets.compare_digest(saved_state, state):
            raise RbacError(
                "LOGIN_FAILED", "Login state mismatch; start again at /auth/login", 400
            )
        tok = await _token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri,
                "code_verifier": verifier,
            }
        )
        resp = RedirectResponse(_config.post_login_url)
        _set_tokens(resp, tok)
        resp.delete_cookie(_OAUTH_COOKIE, path="/auth")
        return resp

    async def refresh(self, request: Request):
        # Cookie-only call: the frontend must still send the CSRF header.
        if request.headers.get("x-requested-with") != "central-rbac":
            raise RbacError("CSRF", "Missing x-requested-with header", 403)
        rt = request.cookies.get(REFRESH_COOKIE)
        if not rt:
            raise RbacError("AUTH_REQUIRED", "No session to refresh", 401)
        resp = JSONResponse(ok({"refreshed": True}))
        try:
            _set_tokens(
                resp, await _token({"grant_type": "refresh_token", "refresh_token": rt})
            )
        except RbacError:
            _clear(resp)
            raise
        return resp

    async def logout(self, request: Request):
        if request.headers.get("x-requested-with") != "central-rbac":
            raise RbacError("CSRF", "Missing x-requested-with header", 403)
        if rt := request.cookies.get(REFRESH_COOKIE):
            try:  # end the Keycloak session; local cookies are cleared either way
                async with httpx.AsyncClient(timeout=_config.http_timeout) as c:
                    await c.post(
                        f"{_oidc}/logout",
                        data={
                            "client_id": _config.auth_client_id,
                            "client_secret": _config.keycloak_client_secret,
                            "refresh_token": rt,
                        },
                    )
            except httpx.HTTPError:
                pass
        resp = JSONResponse(ok({"logged_out": True}))
        _clear(resp)
        return resp

    async def me(self, p: Principal = Depends(current_principal)):
        return ok(
            {
                "sub": p.sub,
                "username": p.username,
                "email": p.email,
                "name": p.name,
                "roles": sorted(p.roles),
                "source": p.source,
            }
        )
