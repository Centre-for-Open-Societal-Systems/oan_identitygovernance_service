"""Who is calling. Accepted credentials, in order:

1. `Authorization: Bearer <token>` (API clients, scripts)
2. `rbac_access` cookie set by this service after the direct login (routers/auth.py)
3. the staff IAM access-token cookie, when IAM_ISSUER is configured

All are Keycloak RS256 tokens. Rights come from client roles on `central-rbac`.
"""
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool

from app.config import Settings
from app.envelope import RbacError

_config = Settings.get_config(strict=False)

ACCESS_COOKIE = "rbac_access"
REFRESH_COOKIE = "rbac_refresh"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "central-rbac"

# Operator roles: client roles on `central-rbac`.
ADMIN, OPERATOR, VIEWER = "rbac-admin", "rbac-operator", "rbac-viewer"


def realm_issuer(realm: str) -> str:
    return f"{_config.keycloak_url}/realms/{realm}"


DIRECT_ISSUER = realm_issuer(_config.auth_realm)

# issuer -> (jwks client, accepted azp)
_trusted: dict[str, tuple[jwt.PyJWKClient, str]] = {
    DIRECT_ISSUER: (jwt.PyJWKClient(f"{DIRECT_ISSUER}/protocol/openid-connect/certs"), _config.auth_client_id)
}
if _config.iam_issuer:
    _trusted[_config.iam_issuer] = (
        jwt.PyJWKClient(f"{_config.iam_issuer.rstrip('/')}/protocol/openid-connect/certs"),
        _config.iam_client_id,
    )


@dataclass
class Principal:
    sub: str
    username: str
    roles: set[str] = field(default_factory=set)
    email: str | None = None
    name: str | None = None
    source: str = "bearer"


def _extract(request: Request) -> tuple[str, str]:
    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and bearer:
        return bearer, "bearer"
    if tok := request.cookies.get(ACCESS_COOKIE):
        return tok, "cookie"
    if _config.iam_issuer and (tok := request.cookies.get(_config.iam_cookie_name)):
        return tok, "iam_cookie"
    raise RbacError("AUTH_REQUIRED", "Login required", 401)


def _check_csrf(request: Request) -> None:
    """Cookies are sent by the browser on its own, so writes need proof the call came from our frontend."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise RbacError("CSRF", f"Missing {CSRF_HEADER}: {CSRF_VALUE} header", 403)
    origin = request.headers.get("origin")
    if origin and origin not in _config.cors_allow_origins:
        raise RbacError("CSRF", "Origin not allowed", 403)


def decode(token: str) -> dict:
    try:
        iss = jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError as e:
        raise RbacError("AUTH_INVALID", f"Invalid token: {e}", 401)
    if iss not in _trusted:
        raise RbacError("AUTH_INVALID", "Untrusted issuer", 401)
    jwks, azp = _trusted[iss]
    try:
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=iss, options={"verify_aud": False})
    except jwt.ExpiredSignatureError:
        raise RbacError("AUTH_EXPIRED", "Token expired; refresh or log in again", 401)
    except jwt.PyJWTError as e:
        raise RbacError("AUTH_INVALID", f"Invalid token: {e}", 401)
    # IAM skips `aud`, so isolation between clients and realms rests on exact issuer + azp.
    if claims.get("azp") != azp:
        raise RbacError("AUTH_INVALID", "Token was not issued for this client", 401)
    return claims


async def current_principal(request: Request) -> Principal:
    token, source = _extract(request)
    if source != "bearer":
        _check_csrf(request)
    claims = await run_in_threadpool(decode, token)
    roles = set(claims.get("resource_access", {}).get(_config.auth_client_id, {}).get("roles", []))
    return Principal(
        sub=claims["sub"], username=claims.get("preferred_username", claims["sub"]), roles=roles,
        email=claims.get("email"), name=claims.get("name"), source=source,
    )


def require(*allowed: str):
    """Dependency: caller must hold one of `allowed` (rbac-admin always passes)."""

    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if ADMIN not in p.roles and not p.roles.intersection(allowed):
            raise RbacError("FORBIDDEN", "Missing role for this action", 403)
        return p

    return dep
