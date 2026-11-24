from collections.abc import AsyncIterator

from openg2p_fastapi_common.context import dbengine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_sessions: async_sessionmaker | None = None


def _session_maker() -> async_sessionmaker:
    """Built lazily: the engine is created by the library's Initializer, after import."""
    global _sessions
    if _sessions is None:
        engine = dbengine.get()
        if engine is None:
            raise RuntimeError("Database engine is not initialized; set RBAC_DB_DATASOURCE or the RBAC_DB_* settings")
        _sessions = async_sessionmaker(engine, expire_on_commit=False)
    return _sessions


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_maker()() as session:
        yield session
