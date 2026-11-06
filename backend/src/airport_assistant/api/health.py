"""Separate process liveness and bounded core database readiness; no provider health claim."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from airport_assistant.api.dependencies import get_health_service
from airport_assistant.api.schemas import ProcessHealth
from airport_assistant.modules.system.health import HealthService

router = APIRouter()


@router.get("/health/live", response_model=ProcessHealth, tags=["operations"])
async def liveness(service: Annotated[HealthService, Depends(get_health_service)]) -> ProcessHealth:
    result = await service.check()
    return ProcessHealth(status=result.status, scope=result.scope)


@router.get('/health/ready', tags=['operations'])
async def readiness(request: Request):
    """Core database only; no tenant records or commercial provider calls."""
    from fastapi.responses import JSONResponse
    database=request.app.state.resources.database
    try:
        if database is None or await database.ready() is not True:raise RuntimeError('Core check failed')
        return JSONResponse({'status':'ready','scope':'core_database'},headers={'Cache-Control':'no-store'})
    except Exception:
        return JSONResponse({'status':'not_ready','scope':'core_database'},status_code=503,headers={'Cache-Control':'no-store'})
