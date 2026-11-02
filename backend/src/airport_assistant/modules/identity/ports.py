"""Consumer-owned ports; implementations arrive with DB and source tasks.

These are not public request schemas or proof of authorization. Callers must
obtain tenant/principal from trusted server context. No default fake adapters
are installed in the application.
"""

from typing import Literal, Protocol
from uuid import UUID


class TenantRepository(Protocol):
    """Lookup by canonical registered host; None means no tenant mapping."""

    async def find_id_by_host(self, *, canonical_host: str) -> UUID | None: ...


class SourceGrantProvider(Protocol):
    """Check current source grants, not a cached relationship or model claim.

    False means insufficient grants. Transport/invalid-response errors must
    propagate to the business service for fail-closed handling, never True.
    An empty requirement is not a request to bypass source authorization.
    """

    async def has_grants(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        resource_type: Literal["booking", "transaction"],
        resource_reference: str,
        required_scopes: frozenset[str],
    ) -> bool: ...
