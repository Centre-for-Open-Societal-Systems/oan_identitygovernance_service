from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def audit(db: AsyncSession, actor: str, action: str, target: str, before: dict | None = None, after: dict | None = None) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, before=before, after=after))
