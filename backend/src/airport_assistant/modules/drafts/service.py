"""Draft lifecycle and routing; no consent, case, email or source transaction."""

from dataclasses import replace
from datetime import timedelta
from typing import Protocol,AsyncContextManager
from uuid import UUID,uuid4

from airport_assistant.modules.conversations.service import ConversationTransaction
from airport_assistant.modules.identity.access import AccessService,ResourceKey
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session
from airport_assistant.modules.drafts.models import Draft,ReportPayload,Destination,DraftError,displayed_summary,seal,require_integrity


class DraftTransaction(ConversationTransaction,Protocol):
    async def now(self):...
    async def destination(self,service_type:str)->Destination|None:...
    async def locate_draft(self,id:UUID)->UUID|None:...
    async def find_draft(self,id:UUID)->Draft|None:...
    async def create_draft(self,draft:Draft)->Draft:...
    async def edit_draft(self,draft:Draft)->Draft|None:...
    async def terminal(self,id:UUID,state:str)->Draft|None:...


class DraftRepository(Protocol):
    def transaction(self,context:DatabaseContext)->AsyncContextManager[DraftTransaction]:...


class DraftService:
    def __init__(self,repository:DraftRepository):self._repository=repository

    @staticmethod
    async def _conversation(tx,identity,id):
        conversation=await tx.find(id,lock=True)
        await require_customer_session(tx)
        AccessService.require_object(identity,ResourceKey(identity.session.tenant_id,'conversation',id),
            conversation.projection() if conversation else None,'read_conversation')
        return conversation

    async def _load(self,tx,identity,id,action):
        conversation_id=await tx.locate_draft(id)
        await require_customer_session(tx)
        if conversation_id is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        conversation=await self._conversation(tx,identity,conversation_id)
        draft=await tx.find_draft(id)
        await require_customer_session(tx)
        if draft is None or draft.created_session_id!=identity.session.id or draft.conversation_id!=conversation_id:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        AccessService.require_object(identity,ResourceKey(identity.session.tenant_id,'draft',id),draft.projection(),action)
        require_integrity(draft)
        if draft.state=='committed' and await tx.case_result(draft.result_case_id) is None:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        return conversation,draft

    @staticmethod
    async def _destination(tx,payload):
        destination=await tx.destination(payload.service_type)
        if destination is None and payload.service_type!='general':destination=await tx.destination('general')
        if destination is None:raise DraftError(503,'DRAFT_DESTINATION_UNAVAILABLE')
        return destination

    @staticmethod
    async def _expired(tx,draft):
        if draft.state=='pending' and draft.expires_at<=await tx.now():
            result=await tx.terminal(draft.id,'expired')
            if result is None:
                await require_customer_session(tx)
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            return result
        return draft

    async def create(self,identity,conversation_id,action_type,payload):
        if action_type not in ('create_case','request_handoff') or type(payload) is not ReportPayload:
            raise DraftError(422,'VALIDATION_ERROR')
        async with customer_transaction(self._repository,identity) as tx:
            conversation=await self._conversation(tx,identity,conversation_id)
            destination=await self._destination(tx,payload)
            now=await tx.now()
            draft=Draft(uuid4(),identity.session.tenant_id,conversation_id,identity.session.principal_id,
                identity.session.id if identity.session.kind=='anonymous' else None,identity.session.id,action_type,
                payload,destination,displayed_summary(action_type,payload,destination,conversation.language),
                conversation.language,1,'',now,now+timedelta(minutes=15))
            result=await tx.create_draft(seal(draft))
            if result is None:
                await require_customer_session(tx)
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            require_integrity(result)
            return result

    async def get(self,identity,id):
        async with customer_transaction(self._repository,identity) as tx:
            _,draft=await self._load(tx,identity,id,'read_draft')
            return await self._expired(tx,draft)

    async def edit(self,identity,id,expected_version,payload):
        if type(expected_version) is not int or expected_version<1 or type(payload) is not ReportPayload:
            raise DraftError(422,'VALIDATION_ERROR')
        error=None
        async with customer_transaction(self._repository,identity) as tx:
            conversation,draft=await self._load(tx,identity,id,'edit_draft')
            draft=await self._expired(tx,draft)
            if draft.state=='expired':error=DraftError(410,'DRAFT_EXPIRED')
            elif draft.state!='pending':error=DraftError(409,'DRAFT_NOT_PENDING',draft.result_ref)
            elif expected_version!=draft.version:error=DraftError(409,'VERSION_CONFLICT')
            else:
                destination=await self._destination(tx,payload)
                proposed=seal(replace(draft,payload=payload,destination=destination,
                    displayed_summary=displayed_summary(draft.action_type,payload,destination,conversation.language),
                    language=conversation.language,version=draft.version+1))
                result=await tx.edit_draft(proposed)
                await require_customer_session(tx)
                if result is None:
                    draft=await self._expired(tx,draft)
                    error=DraftError(410,'DRAFT_EXPIRED') if draft.state=='expired' else DraftError(409,'VERSION_CONFLICT')
                else:require_integrity(result)
        # Expiration state must commit even though editing an expired draft is410.
        if error is not None:raise error
        return result

    async def cancel(self,identity,id):
        error=None
        async with customer_transaction(self._repository,identity) as tx:
            _,draft=await self._load(tx,identity,id,'cancel_draft')
            draft=await self._expired(tx,draft)
            if draft.state=='pending':
                draft=await tx.terminal(id,'cancelled')
                await require_customer_session(tx)
                if draft is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            if draft.state=='expired':error=DraftError(410,'DRAFT_EXPIRED')
            elif draft.state!='cancelled':error=DraftError(409,'DRAFT_NOT_PENDING',draft.result_ref)
        if error is not None:raise error
        return draft
