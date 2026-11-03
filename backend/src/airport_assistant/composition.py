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
from airport_assistant.auth_config import AuthSettings
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository
from airport_assistant.modules.identity.auth import AuthService,AuthPolicy,VerificationSender
from airport_assistant.modules.identity.staff import StaffService
from airport_assistant.modules.identity.access import AccessService
from airport_assistant.source_config import SourceSettings
from airport_assistant.modules.source.service import SourceReadService
from airport_assistant.modules.source.grants import CurrentSourceGrants
from airport_assistant.modules.source.labels import PrivateSourceAccess
from airport_assistant.modules.conversations.service import ConversationService
from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.infrastructure.database.draft_repository import PostgresDrafts
from airport_assistant.infrastructure.database.case_repository import PostgresCases
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.integrations.source.client import SourceHttpClient
from airport_assistant.infrastructure.database.source_subjects import PostgresSourceSubjects
from airport_assistant.infrastructure.database.staff_repository import PostgresStaffRepository
from airport_assistant.infrastructure.security.passwords import BoundedPasswordVerifier


@dataclass(frozen=True)
class ApplicationServices:
    health: HealthService
    auth: AuthService|None=None
    staff: StaffService|None=None
    access: AccessService|None=None
    source: SourceReadService|None=None
    source_grants: CurrentSourceGrants|None=None
    private_source_access: PrivateSourceAccess|None=None
    conversations: ConversationService|None=None
    drafts: DraftService|None=None
    cases: CaseService|None=None
    case_lifecycle: CaseLifecycle|None=None


def build_services(database:RuntimeDatabase|None=None,auth_settings:AuthSettings|None=None,sender:VerificationSender|None=None,source_client=None,tenant_map=None) -> ApplicationServices:
    auth=None
    staff=None
    access=None
    if auth_settings is not None:
        if database is None:raise ValueError("Authentication requires a database resource.")
        auth=AuthService(PostgresAuthRepository(database),auth_settings.session_secret.get_secret_value().encode(),
                         AuthPolicy(**auth_settings.model_dump(exclude={"session_secret"})),sender)
    if auth is not None:
        repository=PostgresStaffRepository(database)
        access=AccessService(auth,repository)
        staff=StaffService(auth,repository,BoundedPasswordVerifier(),access)
    source=SourceReadService(source_client,PostgresSourceSubjects(database),tenant_map) if source_client is not None and database is not None and access is not None else None
    source_grants=CurrentSourceGrants(source,source_client) if source is not None else None
    private_source_access=PrivateSourceAccess(source_grants) if source_grants is not None else None
    conversations=ConversationService(PostgresConversations(database)) if access is not None else None
    drafts=DraftService(PostgresDrafts(database)) if access is not None else None
    cases=CaseService(PostgresCases(database)) if access is not None else None
    case_lifecycle=CaseLifecycle(PostgresCaseLifecycle(database)) if access is not None else None
    return ApplicationServices(health=ProcessHealthService(),auth=auth,staff=staff,access=access,source=source,
                               source_grants=source_grants,private_source_access=private_source_access,conversations=conversations,drafts=drafts,cases=cases,case_lifecycle=case_lifecycle)


@dataclass(frozen=True)
class ApplicationResources:
    database: RuntimeDatabase | None = None
    source: SourceHttpClient | None = None

    @asynccontextmanager
    async def lifecycle(self):
        try:
            if self.database is not None:await self.database.open()
            yield
        finally:
            try:
                if self.source is not None:await self.source.aclose()
            finally:
                if self.database is not None:await self.database.close()


def build_resources(settings: RuntimeDatabaseSettings | None,source_settings:SourceSettings|None=None) -> ApplicationResources:
    database=RuntimeDatabase(
        settings.url.get_secret_value(),pool_max=settings.pool_max,max_waiting=settings.max_waiting,
        connect_timeout=settings.connect_timeout,acquire_timeout=settings.acquire_timeout,
        statement_timeout_ms=settings.statement_timeout_ms) if settings else None
    source=SourceHttpClient(source_settings.base_url,source_settings.token.get_secret_value(),source_settings.context_secret.get_secret_value(),
                            timeout=source_settings.timeout,max_response_bytes=source_settings.max_response_bytes) if source_settings else None
    return ApplicationResources(database,source)


def database_loop_factory():
    # Psycopg async connections require a selector loop on Windows.
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
