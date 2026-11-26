# ruff: noqa: E402

import asyncio

from .config import Settings

_config = Settings.get_config(
    strict=False
)  # before any library module reads its own config

from openg2p_fastapi_common.app import Initializer as BaseInitializer

from .controllers.auth import AuthController
from .controllers.catalogue import CatalogueController
from .controllers.users import UsersController
from .envelope import RbacExceptionHandler
from .models import AuditLog

_TABLES = (AuditLog,)


async def _create_tables() -> None:
    for model in _TABLES:
        await model.create_migrate()


class Initializer(BaseInitializer):
    def initialize(self, **kwargs):
        super().initialize()
        RbacExceptionHandler()  # replaces the library's error body with our envelope

        AuthController().post_init()
        CatalogueController().post_init()
        UsersController().post_init()

    async def fastapi_app_startup(self, app):
        # Dev convenience until Alembic migrations are added; `migrate` runs the same step on demand.
        await _create_tables()

    def migrate_database(self, args):
        super().migrate_database(args)
        asyncio.run(_create_tables())
