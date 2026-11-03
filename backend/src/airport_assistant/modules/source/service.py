"""Verified customer reads, using mappings from trusted backend state."""

import re
from typing import Protocol
from types import MappingProxyType
from uuid import UUID
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.source.models import SourceCaller,SourceFailure,Booking,Transaction,Policy,TransactionPage,RelationPage


class SubjectRepository(Protocol):
    async def find_subject(self,context:DatabaseContext)->str|None:...


class SourceReader(Protocol):
    async def booking(self,caller:SourceCaller,reference:str)->Booking:...
    async def transaction(self,caller:SourceCaller,reference:str)->Transaction:...
    async def booking_transactions(self,caller:SourceCaller,reference:str,limit:int=20,cursor:str|None=None)->TransactionPage:...
    async def policy(self,caller:SourceCaller,reference:str)->Policy:...
    async def relations(self,caller:SourceCaller,limit:int=20,cursor:str|None=None)->RelationPage:...


class SourceReadService:
    def __init__(self,reader:SourceReader,subjects:SubjectRepository,tenant_map:dict[UUID,UUID]):
        self._reader=reader;self._subjects=subjects;self._tenant_map=MappingProxyType(dict(tenant_map))

    async def caller(self,identity:AccessIdentity)->SourceCaller:
        if identity.session.kind!="customer":raise AuthError(401,"AUTH_REQUIRED")
        return await self.caller_for_context(identity.database_context)

    async def caller_for_context(self,context:DatabaseContext)->SourceCaller:
        """Internal trusted principal lookup, also used by background grant checks.

        Does not authenticate a session or establish case participation.
        """
        if context.actor_kind!="customer" or context.principal_id is None:raise AuthError(401,"AUTH_REQUIRED")
        tenant=self._tenant_map.get(context.tenant_id)
        if tenant is None:raise SourceFailure(404)
        subject=await self._subjects.find_subject(context)
        if subject is None:raise SourceFailure(404)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",subject):raise SourceFailure(503)
        return SourceCaller(tenant,subject)

    async def booking(self,identity,reference):return await self._reader.booking(await self.caller(identity),reference)
    async def transaction(self,identity,reference):return await self._reader.transaction(await self.caller(identity),reference)
    async def booking_transactions(self,identity,reference,limit=20,cursor=None):return await self._reader.booking_transactions(await self.caller(identity),reference,limit,cursor)
    async def policy(self,identity,reference):return await self._reader.policy(await self.caller(identity),reference)
    async def relations(self,identity,limit=20,cursor=None):return await self._reader.relations(await self.caller(identity),limit,cursor)
