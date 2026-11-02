"""Composition root: assemble services once per application, never globally.

Concrete repository/provider bindings belong here as features become executable.
Do not put transactions or tenant/actor session state in this application scope.
"""

from dataclasses import dataclass
from contextlib import asynccontextmanager
import asyncio
import sys

from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.modules.system.health import HealthService, ProcessHealthService


@dataclass(frozen=True)
class ApplicationServices:
    health: HealthService


def build_services() -> ApplicationServices:
    return ApplicationServices(health=ProcessHealthService())


@dataclass(frozen=True)
class ApplicationResources:
    database: RuntimeDatabase | None = None

    @asynccontextmanager
    async def lifecycle(self):
        if self.database is None:
            yield
            return
        try:
            await self.database.open()
            yield
        finally:
            await self.database.close()


def build_resources(settings: RuntimeDatabaseSettings | None) -> ApplicationResources:
    if settings is None:
        return ApplicationResources()
    return ApplicationResources(database=RuntimeDatabase(
        settings.url.get_secret_value(), pool_max=settings.pool_max,
        max_waiting=settings.max_waiting, connect_timeout=settings.connect_timeout,
        acquire_timeout=settings.acquire_timeout, statement_timeout_ms=settings.statement_timeout_ms,
    ))


def database_loop_factory():
    # Psycopg async connections require a selector loop on Windows.
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
