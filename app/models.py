import uuid
from datetime import datetime, timezone

from openg2p_fastapi_common.models import BaseORMModel
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(BaseORMModel):
    """The only table: who changed what, when. Applications and roles are Keycloak clients and client roles,
    users are Keycloak users, permissions live in each service, managed realms are a setting."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(300))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
