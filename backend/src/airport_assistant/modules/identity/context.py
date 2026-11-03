"""Trusted server context, never constructed from model or public tenant fields."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class DatabaseContext:
    tenant_id: UUID
    principal_id: UUID | None = None
    actor_kind: Literal["anonymous", "customer", "staff"] = "anonymous"
    session_id: UUID | None = None

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID):
            raise ValueError("A validated tenant UUID is required.")
        if self.actor_kind not in {"anonymous", "customer", "staff"}:
            raise ValueError("Invalid actor kind.")
        if self.principal_id is not None and not isinstance(self.principal_id, UUID):
            raise ValueError("A validated principal UUID is required.")
        if (self.actor_kind == "anonymous") != (self.principal_id is None):
            raise ValueError("Principal and actor kind must agree.")
        if self.session_id is not None and not isinstance(self.session_id, UUID):
            raise ValueError("A validated session UUID is required.")
