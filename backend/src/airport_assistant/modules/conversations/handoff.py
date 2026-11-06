"""Staff accept and shared atomic case/conversation transition rules."""
import hashlib
import json
import re
from uuid import UUID,uuid4
from pydantic import BaseModel,ConfigDict,Field
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


class AcceptChat(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=1)
    expected_case_version:int=Field(ge=1)


async def linked_version(tx,case,expected):
    chat=await tx.active_chat(case['id'],lock=True)
    if chat is not None:
        if expected is None:raise DraftError(422,'CHAT_VERSION_REQUIRED')
        if chat['version']!=expected:raise DraftError(409,'VERSION_CONFLICT')
    elif expected is not None:raise DraftError(409,'VERSION_CONFLICT')
    return chat


class ChatHandoff:
    def __init__(self,repository):self._repository=repository

    async def get(self,identity,id):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            case_id=await tx.chat_case(id)
            if case_id is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            case=await tx.find_case(case_id,lock=True)
            if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            chat=await tx.chat(id,lock=True)
            if chat is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            return {**chat,'conversation_id':chat['id'],'case_version':case['version']}

    async def accept(self,identity,id,command,key):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
        digest=hashlib.sha256(json.dumps(dict(conversation_id=str(id),**command.model_dump()),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            case_id=await tx.chat_case(id)
            if case_id is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            case=await tx.find_case(case_id,lock=True)
            if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            chat=await tx.chat(id,lock=True)
            if chat is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            await tx.accept_key(key)
            previous=await tx.accept_receipt(key)
            if previous is not None:
                if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                return tx.accept_view(previous)
            if case['status']=='closed' or chat['mode']=='ended':raise DraftError(409,'CASE_CLOSED')
            if case['version']!=command.expected_case_version or chat['version']!=command.expected_version:raise DraftError(409,'VERSION_CONFLICT')
            if chat['mode']!='waiting_staff':raise DraftError(409,'CHAT_NOT_WAITING')
            candidates=sorted((m for m in identity.memberships if m.allows('reply_case',case['organization_id'],case['queue_id'])),key=lambda m:(m.id!=case['assigned_membership_id'],str(m.id)))
            target=None
            for member in candidates:
                checked=await tx.target_membership(member.id,case)
                if checked is not None and UUID(checked['principal_id'])==identity.session.principal_id:
                    target=member.id;break
            if target is None:raise AuthError(403,'ACTION_FORBIDDEN')
            if case['assigned_membership_id'] is not None and case['assigned_membership_id']!=target:raise DraftError(409,'ASSIGNEE_CONFLICT')
            transition=uuid4()
            updated=await tx.update_case(case,dict(status='in_progress',assigned_membership_id=target,next_actor='staff',next_action='Petugas sedang menangani kasus.'))
            await tx.history(case,updated,'assigned' if case['assigned_membership_id']!=target else 'status_changed',transition=transition)
            current=await tx.move_chat(chat,'human',target,transition)
            receipt=await tx.transition(transition,'accept',case,updated,chat,current,key=key,digest=digest)
            return tx.accept_view(receipt)
