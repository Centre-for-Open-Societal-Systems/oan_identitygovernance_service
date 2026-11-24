from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import OPERATOR, VIEWER, Principal, require
from app.db import get_session
from app.envelope import RbacError, ok
from app.models import User
from app.services import users as users_svc

router = APIRouter(prefix="/users")


def _user(u: User) -> dict:
    return {c: getattr(u, c) for c in ("id", "realm", "keycloak_user_id", "username", "email", "status", "groups", "location_codes")}


@router.post("/create")
async def create_user(body: schemas.UserIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
    return ok(_user(await users_svc.create_user(db, p.username, body.model_dump())))


@router.post("/bulk-create")
async def bulk_create(body: schemas.BulkUsersIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
    """Per-row result; one bad row never aborts the rest. Re-running is safe (Keycloak 409 -> existing user)."""
    results = []
    for row in body.users:
        try:
            results.append({"username": row.username, "status": "ok", "user": _user(await users_svc.create_user(db, p.username, row.model_dump()))})
        except RbacError as e:
            await db.rollback()
            results.append({"username": row.username, "status": "error", "error_code": e.code, "error": e.message})
    return ok(results)


@router.get("/list")
async def list_users(realm: str | None = None, db: AsyncSession = Depends(get_session), _: Principal = Depends(require(VIEWER, OPERATOR))):
    q = select(User)
    if realm:
        q = q.where(User.realm == realm)
    return ok([_user(u) for u in await db.scalars(q)])


@router.post("/{user_id}/disable")
async def disable(user_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
    return ok(_user(await users_svc.set_status(db, p.username, user_id, False)))


@router.post("/{user_id}/enable")
async def enable(user_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
    return ok(_user(await users_svc.set_status(db, p.username, user_id, True)))


@router.post("/{user_id}/roles/grant")
async def grant(user_id: str, body: schemas.RoleAssignIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
    return ok(_user(await users_svc.assign_role(db, p.username, user_id, body.role_id, True)))


@router.post("/{user_id}/roles/revoke")
async def revoke(user_id: str, body: schemas.RoleAssignIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(require(OPERATOR))):
    return ok(_user(await users_svc.assign_role(db, p.username, user_id, body.role_id, False)))
