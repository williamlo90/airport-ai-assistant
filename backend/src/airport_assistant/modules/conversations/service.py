"""Customer conversation rules inside one repository transaction per operation."""

from dataclasses import dataclass,asdict
from typing import Protocol, AsyncContextManager
from uuid import UUID, uuid4

from airport_assistant.modules.identity.access import AccessIdentity, AccessService, OwnedObject, ResourceKey
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.identity.customer_transaction import (
    CustomerSessionLost as ConversationSessionLost, customer_transaction, require_customer_session,
)


class ConversationError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


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
    mode: str = 'ai'
    case_access_verified: bool = False

    def projection(self):
        return OwnedObject(self.tenant_id, self.id, 'conversation', self.customer_principal_id,
                           self.anonymous_session_id, not self.resource_security_labels and (self.case_id is None or self.case_access_verified))


@dataclass(frozen=True)
class Message:
    id: UUID
    sequence: int
    body: str
    language: str


@dataclass(frozen=True)
class CustomerReadProjection:
    conversation: Conversation
    data: Message | tuple[Message,...] | None
    parent: dict | None
    session_active: bool


class ConversationTransaction(Protocol):
    async def session_active(self) -> bool: ...
    async def languages(self) -> list[str]: ...
    async def lock_conversation_case(self,id:UUID)->UUID|None: ...
    async def create(self, id: UUID, language: str) -> Conversation: ...
    async def find(self, id: UUID, *, lock: bool = False) -> Conversation | None: ...
    async def duplicate(self, conversation_id: UUID, client_message_id: UUID) -> Message | None: ...
    async def customer_message(self, conversation_id: UUID, message_id: UUID) -> Message | None: ...
    async def customer_history(self, conversation_id: UUID, limit: int) -> tuple[Message,...]: ...
    async def source_case_context(self,id:UUID)->dict|None: ...
    async def append(self, conversation: Conversation, id: UUID, client_message_id: UUID, body: str) -> Message | None: ...
    async def change_language(self, conversation: Conversation, language: str) -> Conversation: ...


class ConversationRepository(Protocol):
    def transaction(self, context: DatabaseContext) -> AsyncContextManager[ConversationTransaction]: ...


class ConversationService:
    def __init__(self, repository: ConversationRepository, private_access=None):
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._repository = repository
        self._private_content = PrivateContentGate(private_access)

    @staticmethod
    def _context(identity: AccessIdentity):
        if identity.session.kind not in {'anonymous','customer'}:
            raise AuthError(403,'ACTION_FORBIDDEN')
        return identity.database_context

    @staticmethod
    async def _active(tx):
        await require_customer_session(tx)

    def _transaction(self,identity,*,before_commit=None):
        return customer_transaction(self._repository,identity,before_commit=before_commit)

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
        return await self._read(identity,id,'meta')

    async def customer_message(self,identity,id,message_id):
        return await self._read(identity,id,'message',message_id=message_id)

    async def customer_history(self,identity,id,limit=8):
        if type(limit) is not int or not 1<=limit<=8:raise ConversationError(422,'VALIDATION_ERROR')
        return await self._read(identity,id,'history',limit=limit)

    @staticmethod
    def _owned_candidate(identity,id,conversation,action='read_conversation'):
        # ONLY an internal candidate read may separate ownership from external
        # source grants. Public returns still pass through PrivateContentGate.
        # Mutations may use this only inside the source-gated write unit.
        target=None if conversation is None else OwnedObject(conversation.tenant_id,conversation.id,'conversation',
            conversation.customer_principal_id,conversation.anonymous_session_id,
            conversation.case_id is None or conversation.case_access_verified)
        AccessService.require_object(identity,ResourceKey(identity.session.tenant_id,'conversation',id),target,action)

    async def _mutation_candidate(self,identity,id,tx):
        """Lock in case → conversation order; bind the current parent as well.

        Called before source I/O, again in the write unit, and before commit.
        Incomplete stored inheritance is rejected until the graph is repaired;
        a customer command must never silently lower the classification.
        """
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        from airport_assistant.modules.drafts.models import DraftError
        locked_case=await tx.lock_conversation_case(id)
        conversation=await tx.find(id,lock=True)
        await self._active(tx)
        if conversation is None:
            # A newly linked parent can hide the row in the lock statement's
            # snapshot. Only a fully authorized PUBLIC fresh read may explain
            # that race; never acquire its parent lock late or write from it.
            fresh=await tx.find(id)
            await self._active(tx)
            records=getattr(fresh,'resource_security_labels',None)
            if type(records) not in {tuple,list} or records:raise AuthError(404,'RESOURCE_NOT_FOUND')
            self._require(identity,id,fresh,'append_message')
            if fresh.case_id!=locked_case:raise ConversationError(409,'VERSION_CONFLICT')
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        self._owned_candidate(identity,id,conversation,'append_message')
        if conversation.case_id!=locked_case:
            raise ConversationError(409,'VERSION_CONFLICT')
        parent=None
        if conversation.case_id is not None:
            parent=await tx.source_case_context(conversation.case_id)
            if parent is None or parent.get('id')!=conversation.case_id:
                raise AuthError(404,'RESOURCE_NOT_FOUND')
        records=inherited_records(identity.session.tenant_id,list(conversation.resource_security_labels),
            *([parent.get('resource_security_labels')] if parent is not None else []))
        own=inherited_records(identity.session.tenant_id,list(conversation.resource_security_labels))
        if records!=own:
            raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        # The locked rows protect the basis. Recheck session after the last DB
        # await as well; the shared customer unit checks it again on exit.
        await self._active(tx)
        return PrivateProjection.capture(identity.session.tenant_id,id,value=conversation,
            basis=dict(conversation=asdict(conversation),parent=parent),records=records)

    async def _read(self,identity,id,kind,*,message_id=None,limit=8):
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        from airport_assistant.modules.drafts.models import DraftError
        async def load():
            async with self._transaction(identity) as tx:
                # A real SQL adapter can assemble head/body/parent in one MVCC
                # statement. Two independent gate loaders and live session
                # entry/exit checks remain; no grant result is cached here.
                project=getattr(tx,'customer_read_projection',None)
                if project is not None:
                    packet=await project(id,kind,message_id=message_id,limit=limit)
                    if packet is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                    if type(packet) is not CustomerReadProjection:raise AuthError(404,'RESOURCE_NOT_FOUND')
                    if packet.session_active is not True:raise AuthError(401,'AUTH_REQUIRED')
                    current,data,parent=packet.conversation,packet.data,packet.parent
                    self._owned_candidate(identity,id,current)
                    if current.case_id is not None and (type(parent) is not dict or parent.get('id')!=current.case_id):
                        raise AuthError(404,'RESOURCE_NOT_FOUND')
                    if kind=='message' and (type(data) is not Message or data.id!=message_id):raise AuthError(404,'RESOURCE_NOT_FOUND')
                    if kind=='history' and (type(data) is not tuple or len(data)>limit or any(type(item) is not Message for item in data)):
                        raise AuthError(404,'RESOURCE_NOT_FOUND')
                    records=inherited_records(identity.session.tenant_id,list(current.resource_security_labels),
                        *([parent.get('resource_security_labels')] if parent is not None else []))
                    value=(current,data) if kind=='message' else data if kind=='history' else current
                    basis=asdict(data) if kind=='message' else [asdict(item) for item in data] if kind=='history' else None
                    return PrivateProjection.capture(identity.session.tenant_id,id,value=value,records=records,
                        basis=dict(original=asdict(current),current=asdict(current),kind=kind,data=basis,
                            parents={str(current.case_id):parent} if parent is not None else {}))
                conversation=await tx.find(id)
                await self._active(tx)
                self._owned_candidate(identity,id,conversation)
                if kind=='message':
                    data=await tx.customer_message(id,message_id)
                    if data is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                    data_basis=asdict(data)
                elif kind=='history':
                    data=await tx.customer_history(id,limit)
                    data_basis=[asdict(item) for item in data]
                else:data=None;data_basis=None
                current=await tx.find(id)
                await self._active(tx)
                self._owned_candidate(identity,id,current)
                parents={}
                for case_id in (conversation.case_id,current.case_id):
                    if case_id is not None and case_id not in parents:
                        parent=await tx.source_case_context(case_id)
                        if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                        parents[case_id]=parent
                labels=inherited_records(identity.session.tenant_id,
                    list(conversation.resource_security_labels),list(current.resource_security_labels),
                    *(parent.get('resource_security_labels') for parent in parents.values()))
                latest=await tx.find(id)
                await self._active(tx)
                self._owned_candidate(identity,id,latest)
                if latest!=current:
                    if labels or latest.resource_security_labels or latest.case_id!=current.case_id:
                        raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                    # Ordinary public head movement can return a valid prefix;
                    # a new classification/parent always requires a new read.
                    current=latest
                value=(current,data) if kind=='message' else data if kind=='history' else current
                return PrivateProjection.capture(identity.session.tenant_id,id,value=value,
                    basis=dict(original=asdict(conversation),current=asdict(current),kind=kind,data=data_basis,
                        parents={str(k):v for k,v in parents.items()}),records=labels)
        return await self._private_content.read(identity,load)

    async def append(self, identity: AccessIdentity, id: UUID, client_message_id: UUID, body: str):
        if not isinstance(client_message_id,UUID) or not isinstance(body,str) or not 1<=len(body)<=4000 or not body.strip() or '\x00' in body:
            raise ConversationError(422,'VALIDATION_ERROR')
        current=None
        async def load(tx):
            nonlocal current
            current=await self._mutation_candidate(identity,id,tx)
            return current
        async with self._private_content.mutation(identity,lambda *,before_commit:self._transaction(identity,before_commit=before_commit),load) as tx:
            conversation=current.value
            duplicate = await tx.duplicate(id,client_message_id)
            if duplicate is not None:
                if duplicate.body != body:
                    raise ConversationError(409,'MESSAGE_CONFLICT')
                return duplicate
            if conversation.mode=='ended':raise ConversationError(409,'CONVERSATION_ENDED')
            result = await tx.append(conversation,uuid4(),client_message_id,body)
            if result is None:
                await self._active(tx)
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            return result

    async def change_language(self, identity: AccessIdentity, id: UUID, language: str, expected_version: int):
        self._language(language)
        if type(expected_version) is not int or expected_version<1:
            raise ConversationError(422,'VALIDATION_ERROR')
        current=None
        async def load(tx):
            nonlocal current
            current=await self._mutation_candidate(identity,id,tx)
            return current
        async with self._private_content.mutation(identity,lambda *,before_commit:self._transaction(identity,before_commit=before_commit),load) as tx:
            conversation=current.value
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
