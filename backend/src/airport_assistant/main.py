"""Executable API bootstrap; business routes are introduced in later tasks."""

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from airport_assistant.api.boundary import RequestBoundary
from airport_assistant.api.errors import install_error_handlers
from airport_assistant.api.health import router as health_router
from airport_assistant.api.schemas import ErrorResponse
from airport_assistant.config import ConfigurationError, Settings, load_settings
from airport_assistant.composition import ApplicationServices, build_services, build_resources
from airport_assistant.database_config import RuntimeDatabaseSettings, load_database_settings
from airport_assistant.auth_config import AuthSettings,load_auth_settings
from airport_assistant.modules.identity.auth import VerificationSender
from airport_assistant.api.auth import router as auth_router
from airport_assistant.api.staff import router as staff_router
from airport_assistant.api.source import router as source_router
from airport_assistant.api.conversations import router as conversation_router
from airport_assistant.api.drafts import router as draft_router
from airport_assistant.source_config import SourceSettings,load_source_settings


def create_app(settings: Settings, *, services: ApplicationServices | None = None,
               database_settings: RuntimeDatabaseSettings | None = None,
               auth_settings:AuthSettings|None=None,verification_sender:VerificationSender|None=None,source_settings:SourceSettings|None=None) -> FastAPI:
    """Build the ASGI application from previously validated settings."""
    development_docs = settings.env != "production"
    if settings.database_enabled != (database_settings is not None):
        raise ConfigurationError("Database enablement and runtime configuration must match.")
    if settings.auth_enabled != (auth_settings is not None) or (settings.auth_enabled and not settings.database_enabled):
        raise ConfigurationError("Authentication requires explicit settings and an enabled database.")
    if settings.source_enabled != (source_settings is not None) or (settings.source_enabled and not settings.auth_enabled):
        raise ConfigurationError("Source reads require explicit source settings and authentication.")
    if source_settings and settings.env=='production' and source_settings.base_url.lower().startswith('http:'):
        raise ConfigurationError("Production source connections require TLS.")
    resources = build_resources(database_settings,source_settings)
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
    app.state.services = (build_services(resources.database,auth_settings,verification_sender,resources.source,source_settings.tenant_map if source_settings else None) if auth_settings else build_services()) if services is None else services
    app.state.resources = resources
    install_error_handlers(app)
    app.include_router(health_router)
    if settings.auth_enabled:
        if app.state.services.auth is None or app.state.services.access is None:raise ConfigurationError("Authentication and access services are required.")
        app.include_router(auth_router)
        if app.state.services.staff is not None:app.include_router(staff_router)
        if app.state.services.conversations is not None:app.include_router(conversation_router)
        if app.state.services.drafts is not None:app.include_router(draft_router)
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
    args = parser.parse_args()
    try:
        settings = load_settings(None if args.no_env_file else args.env_file)
        if settings.auth_enabled and not settings.database_enabled:
            raise ConfigurationError("APP_AUTH_ENABLED requires APP_DATABASE_ENABLED.")
        if settings.source_enabled and not settings.auth_enabled:raise ConfigurationError("APP_SOURCE_ENABLED requires authentication.")
        source_settings=load_source_settings(None if args.no_env_file else args.env_file) if settings.source_enabled else None
        if source_settings and settings.env=='production' and source_settings.base_url.lower().startswith('http:'):raise ConfigurationError("Production source connections require TLS.")
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
    uvicorn.run(create_app(settings, database_settings=database_settings,auth_settings=auth_settings,source_settings=source_settings), host=settings.host,
                port=settings.port, access_log=False,
                loop="airport_assistant.composition:database_loop_factory" if settings.database_enabled else "auto")


if __name__ == "__main__":
    main()
