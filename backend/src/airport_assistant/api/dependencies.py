"""HTTP adapters retrieve services; they never construct database/provider clients."""

from fastapi import Request

from airport_assistant.modules.system.health import HealthService


def get_health_service(request: Request) -> HealthService:
    return request.app.state.services.health
