"""Customer conversation rules inside one repository transaction per operation."""

from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import Protocol, AsyncContextManager
from uuid import UUID, uuid4

from airport_assistant.modules.identity.access import AccessIdentity, AccessService, OwnedObject, ResourceKey
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.context import DatabaseContext


class ConversationError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


class ConversationSessionLost(Exception):
    """Repository confirmed session loss after a rolled-back RLS failure."""


@dataclass(frozen=True)
class Conversation:
    id: UUID
    tenant_id: UUID
    customer_principal_id: UUID | None
    anonymous_session_id: UUID | None
    language: str
    version: int
    generation_epoch: int
    last_sequence: int
    case_id: UUID | None = None
    resource_security_labels: tuple = ()

    def projection(self):
        return OwnedObject(self.tenant_id, self.id, 'conversation', self.customer_principal_id,
                           self.anonymous_session_id, self.case_id is None and not self.resource_security_labels)


@dataclass(frozen=True)
class Message:
    id: UUID
    sequence: int
    body: str
    language: str


class ConversationTransaction(Protocol):
    async def session_active(self) -> bool: ...
    async def languages(self) -> list[str]: ...
    async def create(self, id: UUID, language: str) -> Conversation: ...
    async def find(self, id: UUID, *, lock: bool = False) -> Conversation | None: ...
    async def duplicate(self, conversation_id: UUID, client_message_id: UUID) -> Message | None: ...
    async def append(self, conversation: Conversation, id: UUID, client_message_id: UUID, body: str) -> Message | None: ...
    async def change_language(self, conversation: Conversation, language: str) -> Conversation: ...


class ConversationRepository(Protocol):
    def transaction(self, context: DatabaseContext) -> AsyncContextManager[ConversationTransaction]: ...


class ConversationService:
    def __init__(self, repository: ConversationRepository):
        self._repository = repository

    @staticmethod
    def _context(identity: AccessIdentity):
        if identity.session.kind not in {'anonymous','customer'}:
            raise AuthError(403,'ACTION_FORBIDDEN')
        return identity.database_context

    @staticmethod
    async def _active(tx):
        if not await tx.session_active():
            raise AuthError(401,'AUTH_REQUIRED')

    @asynccontextmanager
    async def _transaction(self,identity):
        try:
            async with self._repository.transaction(self._context(identity)) as tx:
                await self._active(tx)
                yield tx
                # Expiry is not frozen by the session row lock. Check again
                # before returning data or committing writes, including replay.
                await self._active(tx)
        except ConversationSessionLost:
            raise AuthError(401,'AUTH_REQUIRED') from None

    @staticmethod
    def _require(identity, id, conversation, action):
        AccessService.require_object(identity, ResourceKey(identity.session.tenant_id,'conversation',id),
                                     conversation.projection() if conversation else None, action)

    @staticmethod
    def _language(language):
        if language not in ('id','en'):
            raise ConversationError(422,'VALIDATION_ERROR')

    async def create(self, identity: AccessIdentity, language: str):
        self._language(language)
        async with self._transaction(identity) as tx:
            if language not in await tx.languages():
                raise ConversationError(422,'VALIDATION_ERROR')
            return await tx.create(uuid4(),language)

    async def get(self, identity: AccessIdentity, id: UUID):
        async with self._transaction(identity) as tx:
            conversation = await tx.find(id)
            await self._active(tx)
            self._require(identity,id,conversation,'read_conversation')
            return conversation

    async def append(self, identity: AccessIdentity, id: UUID, client_message_id: UUID, body: str):
        if not isinstance(client_message_id,UUID) or not isinstance(body,str) or not 1<=len(body)<=4000 or not body.strip() or '\x00' in body:
            raise ConversationError(422,'VALIDATION_ERROR')
        async with self._transaction(identity) as tx:
            conversation = await tx.find(id,lock=True)
            await self._active(tx)
            self._require(identity,id,conversation,'append_message')
            duplicate = await tx.duplicate(id,client_message_id)
            if duplicate is not None:
                if duplicate.body != body:
                    raise ConversationError(409,'MESSAGE_CONFLICT')
                return duplicate
            result = await tx.append(conversation,uuid4(),client_message_id,body)
            if result is None:
                await self._active(tx)
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            return result

    async def change_language(self, identity: AccessIdentity, id: UUID, language: str, expected_version: int):
        self._language(language)
        if type(expected_version) is not int or expected_version<1:
            raise ConversationError(422,'VALIDATION_ERROR')
        async with self._transaction(identity) as tx:
            conversation = await tx.find(id,lock=True)
            await self._active(tx)
            self._require(identity,id,conversation,'append_message')
            if conversation.version != expected_version:
                raise ConversationError(409,'VERSION_CONFLICT')
            if language not in await tx.languages():
                raise ConversationError(422,'VALIDATION_ERROR')
            if language==conversation.language:
                return conversation
            result = await tx.change_language(conversation,language)
            if result is None:
                await self._active(tx)
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            return result
