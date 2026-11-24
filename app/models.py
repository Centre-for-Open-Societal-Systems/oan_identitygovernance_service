import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Realm(Base):
    __tablename__ = "realms"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    keycloak_realm_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("realm", "mnemonic"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    realm: Mapped[str] = mapped_column(ForeignKey("realms.name"))
    mnemonic: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    keycloak_client_uuid: Mapped[str | None] = mapped_column(String(64))
    service_url: Mapped[str | None] = mapped_column(String(500))
    admin_api_url: Mapped[str | None] = mapped_column(String(500))
    # Name of the env var / secret-store key holding the service's admin API token. Never the value.
    admin_api_secret_ref: Mapped[str | None] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(200))
    auto_repair: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    roles: Mapped[list["Role"]] = relationship(back_populates="application")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("application_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    keycloak_role_id: Mapped[str | None] = mapped_column(String(64))
    # pending | active | failed | disabled | imported
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    permissions_recorded: Mapped[bool] = mapped_column(default=False)

    application: Mapped[Application] = relationship(back_populates="roles")


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("application_id", "resource", "action"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    resource: Mapped[str] = mapped_column(String(200))
    # A named (non-Frappe) permission is a resource with action "*".
    action: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


class AllowedPermission(Base):
    """Allow-list: the resources and actions the central RBAC may grant per application."""

    __tablename__ = "allowed_permissions"
    __table_args__ = (UniqueConstraint("application_id", "resource", "action"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    resource: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(50))


class User(Base):
    """Light copy of a Keycloak user. Never holds a password or token."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("realm", "keycloak_user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    realm: Mapped[str] = mapped_column(ForeignKey("realms.name"))
    keycloak_user_id: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), default="active")
    groups: Mapped[list] = mapped_column(JSON, default=list)
    location_codes: Mapped[list] = mapped_column(JSON, default=list)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(300))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    realm: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(50))
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)
