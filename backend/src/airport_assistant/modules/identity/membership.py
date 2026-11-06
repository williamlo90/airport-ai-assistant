"""Membership-local permissions shared by login and object access services."""

from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class Membership:
    id: UUID
    organization_id: UUID
    version: int
    roles: frozenset[str]
    queue_ids: frozenset[UUID]

    def __post_init__(self):
        if not isinstance(self.id, UUID) or not isinstance(self.organization_id, UUID) or type(self.version) is not int or self.version < 1:
            raise ValueError("Validated membership identifiers/version required.")
        object.__setattr__(self, "roles", frozenset(self.roles))
        object.__setattr__(self, "queue_ids", frozenset(self.queue_ids))
        if not self.roles or not self.roles <= {"cs", "supervisor", "admin"} or any(not isinstance(q, UUID) for q in self.queue_ids):
            raise ValueError("Validated role and queue metadata required.")

    def allows(self, action: str, organization_id: UUID, queue_id: UUID | None = None) -> bool:
        if self.organization_id != organization_id:
            return False
        if action == "manage_memberships":
            return "admin" in self.roles
        if action in {"read_case", "reply_case"}:
            return bool(self.roles & {"cs", "supervisor"}) and queue_id is not None and queue_id in self.queue_ids
        if action == "assign_case":
            return "supervisor" in self.roles and queue_id is not None and queue_id in self.queue_ids
        return False
