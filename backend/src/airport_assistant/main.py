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
from airport_assistant.composition import ApplicationServices, build_services


def create_app(settings: Settings, *, services: ApplicationServices | None = None) -> FastAPI:
    """Build the ASGI application from previously validated settings."""
    development_docs = settings.env != "production"
    app = FastAPI(
        title="Airport AI Assistant API",
        version="0.1.0",
        docs_url="/docs" if development_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if development_docs else None,
        responses={status: {"model": ErrorResponse} for status in (400, 404, 405, 413, 422, 500)},
    )
    app.state.settings = settings
    app.state.services = build_services() if services is None else services
    install_error_handlers(app)
    app.include_router(health_router)
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
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.check_config:
        print("Configuration valid.")
        return
    # Default Uvicorn access logs include raw paths/query strings.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
