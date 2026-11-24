from collections.abc import AsyncIterator

from openg2p_fastapi_common.context import dbengine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_sessions: async_sessionmaker | None = None


async def get_session() -> AsyncIterator[AsyncSession]:
    global _sessions
    if _sessions is None:  # built lazily: the engine is created by the library's Initializer, after import
        if dbengine.get() is None:
            raise RuntimeError("Database engine is not initialized; set RBAC_DB_DATASOURCE or the RBAC_DB_* settings")
        _sessions = async_sessionmaker(dbengine.get(), expire_on_commit=False)
    async with _sessions() as session:
        yield session
