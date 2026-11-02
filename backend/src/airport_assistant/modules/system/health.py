"""Process status shared by entry points, independent of HTTP and providers."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ProcessStatus:
    status: Literal["ok"] = "ok"
    scope: Literal["process"] = "process"


class HealthService(Protocol):
    async def check(self) -> ProcessStatus: ...


class ProcessHealthService:
    async def check(self) -> ProcessStatus:
        # This is liveness, not a dependency or permission check.
        return ProcessStatus()
