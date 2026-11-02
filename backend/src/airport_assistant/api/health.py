"""Process liveness only; no database or provider readiness claim."""

from fastapi import APIRouter

from airport_assistant.api.schemas import ProcessHealth

router = APIRouter()


@router.get("/health/live", response_model=ProcessHealth, tags=["operations"])
async def liveness() -> ProcessHealth:
    return ProcessHealth()
