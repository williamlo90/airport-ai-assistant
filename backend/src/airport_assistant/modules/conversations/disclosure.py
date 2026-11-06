"""Operation-local staff authority plus explicit CUSTOMER recipient grants.

No impersonated customer identity and no personal source grants for the staff
actor. Loaders own current operational ACL; recipient data comes from storage.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass,field
from uuid import UUID
import time

from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection


@dataclass(frozen=True)
class RecipientContext:
    tenant_id:UUID
    principal_id:UUID
    # Whole server-selected routing/participation basis is in the projection.
    basis:dict=field(repr=False)

    @classmethod
    def from_record(cls,tenant_id,record,channel):
        if (type(record) is not dict or record.get('tenant_id')!=tenant_id
            or not isinstance(record.get('principal_id'),UUID)
            or any(record.get(key) is not True for key in ('tenant_active','principal_active','participant_active'))):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        if channel=='email' and (not isinstance(record.get('contact_id'),UUID)
            or record.get('verified') is not True or record.get('account_active') is not True
            or record.get('principal_id')!=record.get('owner_principal_id')
            or not isinstance(record.get('account_id'),UUID) or not record.get('recipient')):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        return cls(tenant_id,record['principal_id'],dict(record))


@dataclass(frozen=True)
class DisclosureCandidate:
    projection:PrivateProjection=field(repr=False)
    recipient:RecipientContext|None=field(repr=False)


class RecipientDisclosureGate:
    def __init__(self,source_access=None,*,clock=time.monotonic):
        self.source_access,self.clock=source_access,clock

    @staticmethod
    def validate(identity,candidate):
        if type(candidate) is not DisclosureCandidate:raise AuthError(404,'RESOURCE_NOT_FOUND')
        PrivateContentGate._candidate(identity,candidate.projection,'staff')
        if candidate.projection.labels is not None and (type(candidate.recipient) is not RecipientContext
            or candidate.recipient.tenant_id!=identity.session.tenant_id):
            raise AuthError(404,'RESOURCE_NOT_FOUND')

    @asynccontextmanager
    async def mutation(self,identity,transaction,loader,after_loader):
        checked=None
        def fresh():
            if checked is not None and not 0<=self.clock()-checked<2:raise SourceFailure(503)
        async with transaction(before_commit=fresh) as tx:
            initial=await loader(tx)
            self.validate(identity,initial)
            if initial.projection.labels is None:
                yield tx,initial
                after=await after_loader(tx)
                self.validate(identity,after)
                if after.projection.labels is not None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return
        allowed=False if self.source_access is None else await self.source_access.allows(
            tenant_id=initial.recipient.tenant_id,principal_id=initial.recipient.principal_id,
            labels=initial.projection.labels)
        if allowed is not True:raise AuthError(404,'RESOURCE_NOT_FOUND')
        checked=self.clock()
        async with transaction(before_commit=fresh) as tx:
            current=await loader(tx)
            self.validate(identity,current)
            if (current.projection.resource_id!=initial.projection.resource_id
                or current.projection.basis_hash!=initial.projection.basis_hash
                or current.recipient!=initial.recipient):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            fresh()
            yield tx,current
            after=await after_loader(tx)
            self.validate(identity,after)
            if (after.projection.resource_id!=initial.projection.resource_id
                or after.recipient!=initial.recipient
                or after.projection.labels!=initial.projection.labels):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            fresh()  # after the operation's LAST awaited stored-object read
        fresh()
