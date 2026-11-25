import uuid
from datetime import datetime, timezone

from openg2p_fastapi_common.models import BaseORMModel
from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Realm(BaseORMModel):
    __tablename__ = "realms"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    keycloak_realm_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")


class Application(BaseORMModel):
    """Registry of the Keycloak clients central RBAC manages, and how to reach each one's admin API.

    Everything about the client itself (description, enabled, uuid) and its roles is read from Keycloak; a role's
    permissions are read from the service. Nothing about a role is stored here.
    """

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("realm", "mnemonic"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    realm: Mapped[str] = mapped_column(ForeignKey("realms.name"))
    mnemonic: Mapped[str] = mapped_column(String(100))  # the Keycloak clientId
    admin_api_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AllowedPermission(BaseORMModel):
    """Allow-list: the resources and actions the central RBAC may grant per application."""

    __tablename__ = "allowed_permissions"
    __table_args__ = (UniqueConstraint("application_id", "resource", "action"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    resource: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(50))


class AuditLog(BaseORMModel):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(300))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
