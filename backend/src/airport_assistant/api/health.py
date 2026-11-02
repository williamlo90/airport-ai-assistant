"""Process liveness only; no database or provider readiness claim."""

from typing import Annotated

from fastapi import APIRouter, Depends

from airport_assistant.api.dependencies import get_health_service
from airport_assistant.api.schemas import ProcessHealth
from airport_assistant.modules.system.health import HealthService

router = APIRouter()


@router.get("/health/live", response_model=ProcessHealth, tags=["operations"])
async def liveness(service: Annotated[HealthService, Depends(get_health_service)]) -> ProcessHealth:
    result = await service.check()
    return ProcessHealth(status=result.status, scope=result.scope)
