"""Current per-resource grants. Never infer payment access from a booking."""

from uuid import UUID
from types import MappingProxyType

from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.service import SourceReadService, SourceReader


SCOPES = MappingProxyType({
    "booking": frozenset({"booking:read"}),
    "transaction": frozenset({"payment_status:read", "payment_details:read"}),
})


class CurrentSourceGrants:
    """SourceGrantProvider implementation; every call performs a fresh source GET.

    Only the source's 404 means denied. All other source failures propagate.
    Caller must separately establish session, membership and object permission.
    """

    def __init__(self, source: SourceReadService, reader: SourceReader):
        self._source = source
        self._reader = reader

    async def has_grants(self, *, tenant_id: UUID, principal_id: UUID,
                         resource_type: str, resource_reference: str,
                         required_scopes: frozenset[str]) -> bool:
        import re
        if (resource_type not in SCOPES or type(required_scopes) is not frozenset
                or not required_scopes or not required_scopes <= SCOPES[resource_type]
                or not isinstance(resource_reference, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", resource_reference)):
            raise ValueError("Invalid source grant requirement.")
        context = DatabaseContext(tenant_id, principal_id, "customer")
        try:
            caller = await self._source.caller_for_context(context)
            observation = await (self._reader.booking(caller, resource_reference)
                                 if resource_type == "booking"
                                 else self._reader.transaction(caller, resource_reference))
        except SourceFailure as error:
            if error.status == 404:
                return False
            raise
        if observation.reference != resource_reference:
            raise SourceFailure(502)
        return required_scopes <= frozenset(observation.allowed_scopes)
