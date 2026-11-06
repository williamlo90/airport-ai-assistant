"""Executable API bootstrap; business routes are introduced in later tasks."""

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from airport_assistant.api.boundary import RequestBoundary
from airport_assistant.api.errors import install_error_handlers
from airport_assistant.api.knowledge import router as knowledge_router
from airport_assistant.api.health import router as health_router
from airport_assistant.api.schemas import ErrorResponse
from airport_assistant.agent_config import AgentSettings,load_agent_settings
from airport_assistant.knowledge_config import KnowledgeSettings,load_knowledge_settings
from airport_assistant.config import ConfigurationError, Settings, load_settings
from airport_assistant.composition import ApplicationServices, ApplicationResources, build_services, build_resources, validate_schema
from airport_assistant.database_config import RuntimeDatabaseSettings, load_database_settings
from airport_assistant.auth_config import AuthSettings,load_auth_settings
from airport_assistant.modules.identity.auth import VerificationSender
from airport_assistant.api.auth import router as auth_router
from airport_assistant.api.staff import router as staff_router
from airport_assistant.api.source import router as source_router
from airport_assistant.api.conversations import router as conversation_router
from airport_assistant.api.drafts import router as draft_router
from airport_assistant.api.cases import router as case_router
from airport_assistant.api.attachments import router as attachment_router
from airport_assistant.api.ratings import router as rating_router
from airport_assistant.api.administration import router as administration_router
from airport_assistant.api.staff_messages import router as staff_message_router
from airport_assistant.api.agent import router as agent_router
from airport_assistant.api.email_delivery import router as delivery_router
from airport_assistant.api.email_inbox import router as inbox_router
from airport_assistant.mail_config import MailSettings,load_mail_settings
from airport_assistant.api.chat_handoff import router as chat_handoff_router
from airport_assistant.api.conversation_events import router as event_router
from airport_assistant.api.case_lifecycle import router as case_lifecycle_router
from airport_assistant.source_config import SourceSettings,load_source_settings


def create_app(settings: Settings, *, services: ApplicationServices | None = None,
               database_settings: RuntimeDatabaseSettings | None = None,
               auth_settings:AuthSettings|None=None,verification_sender:VerificationSender|None=None,source_settings:SourceSettings|None=None,mail_settings:MailSettings|None=None,knowledge_settings:KnowledgeSettings|None=None,agent_settings:AgentSettings|None=None,resources:ApplicationResources|None=None,schema:str='app') -> FastAPI:
    """Build the ASGI application from previously validated settings."""
    development_docs = settings.env != "production"
    validate_schema(schema)
    if schema!='app' and settings.env!='test':raise ConfigurationError('Custom schema composition is reserved for isolated tests.')
    if schema!='app' and mail_settings is not None and any((mail_settings.enabled,mail_settings.auth_enabled,mail_settings.ingress_enabled,mail_settings.receiving_enabled)):
        raise ConfigurationError('Isolated browser test composition cannot activate provider mail.')
    if settings.database_enabled != (database_settings is not None):
        raise ConfigurationError("Database enablement and runtime configuration must match.")
    if settings.auth_enabled != (auth_settings is not None) or (settings.auth_enabled and not settings.database_enabled):
        raise ConfigurationError("Authentication requires explicit settings and an enabled database.")
    if settings.source_enabled != (source_settings is not None) or (settings.source_enabled and not settings.auth_enabled):
        raise ConfigurationError("Source reads require explicit source settings and authentication.")
    if settings.private_source_enabled and not (settings.source_enabled and settings.auth_enabled and settings.database_enabled):
        raise ConfigurationError('Private source workflows require source, authentication and database services.')
    if source_settings and settings.env=='production' and source_settings.base_url.lower().startswith('http:'):
        raise ConfigurationError("Production source connections require TLS.")
    if mail_settings is not None and mail_settings.ingress_enabled and not settings.database_enabled:raise ConfigurationError('Mail ingress requires database configuration.')
    if mail_settings is not None and mail_settings.auth_enabled and not settings.auth_enabled:raise ConfigurationError('Authentication mail requires authentication configuration.')
    if knowledge_settings is not None and knowledge_settings.enabled and not settings.auth_enabled:raise ConfigurationError('Knowledge requires authentication.')
    if agent_settings is not None and agent_settings.enabled:
        if not settings.auth_enabled:raise ConfigurationError('Agent requires authentication.')
        if agent_settings.knowledge_min_score is not None and(knowledge_settings is None or not knowledge_settings.enabled):raise ConfigurationError('Agent knowledge policy requires enabled retrieval.')
    resources = resources if resources is not None else build_resources(database_settings,source_settings,schema=schema)
    if resources.database is not None and resources.database.schema!=schema:raise ConfigurationError('Database and application schemas must match.')
    if settings.private_source_enabled:
        if resources.database is None:raise ConfigurationError('Private source workflows require a database resource.')
        resources.database.require_private_mail_history(schema)
    app = FastAPI(
        title="Airport AI Assistant API",
        version="0.1.0",
        docs_url="/docs" if development_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if development_docs else None,
        responses={status: {"model": ErrorResponse} for status in (400, 404, 405, 413, 422, 500)},
        lifespan=lambda app: resources.lifecycle(),
    )
    app.state.settings = settings
    app.state.services = build_services(resources.database,auth_settings,verification_sender,resources.source,source_settings.tenant_map if source_settings else None,settings.attachment_scanner_port,mail_settings,knowledge_settings,agent_settings,private_enabled=settings.private_source_enabled,schema=schema,scanner_backend=settings.attachment_scanner_backend) if services is None else services
    app.state.resources = resources
    install_error_handlers(app)
    app.include_router(health_router)
    if settings.auth_enabled:
        if app.state.services.auth is None or app.state.services.access is None:raise ConfigurationError("Authentication and access services are required.")
        app.include_router(auth_router)
        if app.state.services.staff is not None:app.include_router(staff_router)
        if app.state.services.conversations is not None:app.include_router(conversation_router)
        if app.state.services.drafts is not None:app.include_router(draft_router)
        if app.state.services.cases is not None:app.include_router(case_router)
        if app.state.services.case_lifecycle is not None:app.include_router(case_lifecycle_router)
        if app.state.services.attachments is not None:app.include_router(attachment_router)
        if app.state.services.ratings is not None:app.include_router(rating_router)
        if app.state.services.administration is not None:app.include_router(administration_router)
        if app.state.services.conversation_events is not None:app.include_router(event_router)
        if app.state.services.chat_handoff is not None:app.include_router(chat_handoff_router)
        if app.state.services.staff_messages is not None:app.include_router(staff_message_router)
        app.include_router(agent_router)
        if app.state.services.delivery_status is not None:app.include_router(delivery_router)
        if app.state.services.knowledge is not None:app.include_router(knowledge_router)
    if app.state.services.email_ingress is not None:app.include_router(inbox_router)
    if settings.source_enabled:
        if app.state.services.source is None:raise ConfigurationError("Source read services are required.")
        app.include_router(source_router)
    app.add_middleware(RequestBoundary, max_body_bytes=settings.max_request_body_bytes)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Airport AI Assistant API.")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"), help="Dotenv file (default: .env)."
    )
    parser.add_argument(
        "--no-env-file", action="store_true", help="Read process environment only."
    )
    parser.add_argument(
        "--check-config", action="store_true", help="Validate configuration and exit."
    )
    parser.add_argument(
        "--local-proxy", action="store_true",
        help="Development only: bind 127.0.0.1:8001 and trust HTTPS forwarding from 127.0.0.1 only.",
    )
    parser.add_argument('--trusted-loopback-proxy', action='store_true',
        help='Production same-pod TLS proxy only; bind loopback and trust loopback forwarding.')
    args = parser.parse_args()
    try:
        settings = load_settings(None if args.no_env_file else args.env_file)
        if args.trusted_loopback_proxy:
            if args.local_proxy or settings.env != 'production':
                raise ConfigurationError('Production loopback proxy requires APP_ENV=production and excludes --local-proxy.')
            settings = settings.model_copy(update={'host':'127.0.0.1'})
        if args.local_proxy:
            if settings.env != "development":
                raise ConfigurationError("Local proxy mode requires APP_ENV=development.")
            settings = settings.model_copy(update={"host": "127.0.0.1", "port": 8001})
        if settings.auth_enabled and not settings.database_enabled:
            raise ConfigurationError("APP_AUTH_ENABLED requires APP_DATABASE_ENABLED.")
        if settings.source_enabled and not settings.auth_enabled:raise ConfigurationError("APP_SOURCE_ENABLED requires authentication.")
        source_settings=load_source_settings(None if args.no_env_file else args.env_file) if settings.source_enabled else None
        if source_settings and settings.env=='production' and source_settings.base_url.lower().startswith('http:'):raise ConfigurationError("Production source connections require TLS.")
        mail_settings=load_mail_settings(None if args.no_env_file else args.env_file) if settings.database_enabled else None
        if mail_settings is not None and mail_settings.auth_enabled and not settings.auth_enabled:raise ConfigurationError('Authentication mail requires authentication configuration.')
        knowledge_settings=load_knowledge_settings(None if args.no_env_file else args.env_file)
        if knowledge_settings.enabled and not settings.auth_enabled:raise ConfigurationError('Knowledge requires authentication.')
        agent_settings=load_agent_settings(None if args.no_env_file else args.env_file)
        if agent_settings.enabled and not settings.auth_enabled:raise ConfigurationError('Agent requires authentication.')
        if agent_settings.enabled and agent_settings.knowledge_min_score is not None and not knowledge_settings.enabled:raise ConfigurationError('Agent knowledge policy requires enabled retrieval.')
        auth_settings=load_auth_settings(None if args.no_env_file else args.env_file) if settings.auth_enabled else None
        database_settings = load_database_settings(
            RuntimeDatabaseSettings, None if args.no_env_file else args.env_file,
        ) if settings.database_enabled else None
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.check_config:
        print("Configuration valid.")
        return
    # Default Uvicorn access logs include raw paths/query strings.
    uvicorn.run(create_app(settings, agent_settings=agent_settings,knowledge_settings=knowledge_settings, database_settings=database_settings,auth_settings=auth_settings,source_settings=source_settings,mail_settings=mail_settings), host=settings.host,
                port=settings.port, access_log=False,
                proxy_headers=args.local_proxy or args.trusted_loopback_proxy, forwarded_allow_ips="127.0.0.1" if args.local_proxy or args.trusted_loopback_proxy else "",
                loop="airport_assistant.composition:database_loop_factory" if settings.database_enabled else "auto")


if __name__ == "__main__":
    main()
