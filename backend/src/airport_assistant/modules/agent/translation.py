"""Staff-owned translation previews. The model can never approve or send a reply."""
from collections import Counter
from datetime import timedelta
import hashlib
import json
import re
from typing import Literal
from uuid import UUID
from pydantic import Field,model_validator,field_validator
from airport_assistant.modules.conversations.publication import StaffMessage
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from .models import Model,AgentError


class TranslationText(Model):
    text:str=Field(min_length=1,max_length=4000,repr=False)
    @field_validator('text')
    @classmethod
    def nonblank(cls,value):return StaffMessage.nonblank(value)


class TranslationRequest(StaffMessage):
    target_language:Literal['en','id']
    request_key:UUID=Field(strict=False)
    @model_validator(mode='after')
    def different(self):
        if self.language==self.target_language:raise ValueError('Choose another language.')
        return self


class TranslationApproval(TranslationText):
    approved:Literal[True]
    expected_revision:int=Field(ge=1)
    @field_validator('approved',mode='before')
    @classmethod
    def human_true(cls,value):
        if type(value) is not bool or not value:raise ValueError('Explicit approval required.')
        return value


def preserve_values(original,translated):
    # Check literal identifiers and numeric dates/amounts. Semantic quality still
    # requires the reviewing human and AI-04 evaluation; this is not an entailment proof.
    pattern=r'\b(?=[A-Za-z0-9_-]*\d)[A-Za-z]+[A-Za-z0-9]*-[A-Za-z0-9_-]+\b|\d+(?:[.,:/-]\d+)*|(?<!\w)(?:USD|SGD|IDR|EUR|GBP|AUD|Rp)(?![A-Za-z])|(?<![A-Za-z])(?:US|S|HK|A|C|NZ)\$|[$€£]'
    if Counter(re.findall(pattern,original))!=Counter(re.findall(pattern,translated)):
        raise AgentError(502,'AGENT_TRANSLATION_VALUES_CHANGED')


class StaffTranslations:
    def __init__(self,repository,messages,model,*,private_translation=False):
        if type(private_translation) is not bool:raise ValueError('Explicit private translation switch required.')
        self.repository,self.messages,self.model=repository,messages,model
        self.private=None
        if private_translation:
            from .private_translation import PrivateStaffTranslations
            self.private=PrivateStaffTranslations(self)

    @staticmethod
    def _public(row):
        records=row.get('resource_security_labels',[])
        if type(records) is not list or records:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')

    @staticmethod
    def command(row,*,translated=False,body=None):
        return StaffMessage(conversation_id=row['conversation_id'],channel=row['channel'],
            body=body if body is not None else row['original_body'],
            language=row['target_language'] if translated else row['source_language'],
            expected_version=row['case_version'],expected_conversation_version=row['conversation_version'],expected_epoch=row['generation_epoch'])

    async def _find(self,tx,case_id,id):
        case=await tx.find_case(case_id,lock=True)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        row=await tx.translation(id)
        if row is None or row['case_id']!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
        await tx.chat(row['conversation_id'],lock=True)
        row=await tx.translation(id,lock=True)
        if row is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return row

    async def prepare(self,identity,case_id,request):
        if self.private is not None:return await self.private.prepare(identity,case_id,request)
        digest=hashlib.sha256(json.dumps(request.model_dump(mode='json',exclude={'request_key'}),sort_keys=True).encode()).hexdigest()
        async with lifecycle_transaction(self.repository,identity,staff=True) as tx:
            await self.messages.target(tx,identity,case_id,request)
            if request.target_language not in await tx.languages():raise DraftError(422,'VALIDATION_ERROR')
            if not await tx.translation_context_clear(case_id,request.conversation_id):raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            previous=await tx.translation_request(request.request_key)
            if previous:
                self._public(previous)
                if previous['case_id']!=case_id or previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                if previous['state']=='generating':raise AgentError(409,'AGENT_TRANSLATION_IN_FLIGHT')
                return previous
            row=await tx.create_translation(case_id,request,digest)
            self._public(row)
        try:
            translated=TranslationText.model_validate({'text':await self.model.translate(request.body,request.language,request.target_language)}).text
            preserve_values(request.body,translated)
            async with lifecycle_transaction(self.repository,identity,staff=True) as tx:
                saved=await self._find(tx,case_id,row['id'])
                self._public(saved)
                await self.messages.target(tx,identity,case_id,request)
                if not await tx.translation_context_clear(case_id,request.conversation_id):raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
                if saved['created_at']+timedelta(seconds=120)<=await tx.now():raise AgentError(409,'AGENT_TRANSLATION_EXPIRED')
                return await tx.finish_translation(saved,translated)
        except Exception:
            # No retry of an unknown provider outcome. A revoked actor cannot
            # mutate even the failure marker; its inaccessible row stays generating.
            try:
                async with lifecycle_transaction(self.repository,identity,staff=True) as tx:
                    saved=await self._find(tx,case_id,row['id'])
                    self._public(saved)
                    if saved['state']=='generating':await tx.fail_translation(saved)
            except (AuthError,DraftError,AgentError):pass
            raise

    async def get(self,identity,case_id,id):
        if self.private is not None:return await self.private.get(identity,case_id,id)
        async with lifecycle_transaction(self.repository,identity,staff=True) as tx:
            row=await self._find(tx,case_id,id)
            self._public(row)
            if not await tx.translation_context_clear(case_id,row['conversation_id']):raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            return row

    async def by_request(self,identity,case_id,key):
        # Discovery is read-only. Generation/expiry must never be advanced by GET.
        async with lifecycle_transaction(self.repository,identity,staff=True) as tx:
            case=await tx.find_case(case_id)
            if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            row=await tx.translation_request(key)
            if (row is None or row['case_id']!=case_id or row.get('request_key')!=key
                or row.get('principal_id')!=identity.session.principal_id
                or row.get('session_id')!=identity.session.id):raise AuthError(404,'RESOURCE_NOT_FOUND')
            id=row['id']
        # Re-enter the existing current ACL/classification gate after discovery.
        return await self.get(identity,case_id,id)

    async def approve(self,identity,case_id,id,approval,key):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
        if self.private is not None:return await self.private.approve(identity,case_id,id,approval,key)
        async with lifecycle_transaction(self.repository,identity,staff=True) as tx:
            row=await self._find(tx,case_id,id)
            self._public(row)
            if not await tx.translation_context_clear(case_id,row['conversation_id']):raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            if row['state']=='sent':
                if row['reviewed_body']!=approval.text or row['send_key']!=key:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                saved=await tx.staff_receipt(key)
                if saved is None or saved['id']!=row['message_id']:raise DraftError(500,'INTERNAL_ERROR')
                return tx.message_view(saved,case_id)
            if row['state']!='ready' or row['revision']!=approval.expected_revision:raise DraftError(409,'VERSION_CONFLICT')
            if row['expires_at']<=await tx.now():raise AgentError(409,'AGENT_TRANSLATION_EXPIRED')
            if not await tx.translation_context_clear(case_id,row['conversation_id']):raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            command=self.command(row,translated=True,body=approval.text)
            # Reuse the same human messaging rules and transaction, including
            # handler/assignment/epoch and email routing. Approval comes from UI only.
            digest=hashlib.sha256(json.dumps({'case_id':str(case_id),**command.model_dump(mode='json')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
            result=await self.messages.send_in_transaction(tx,identity,case_id,command,key,digest)
            await tx.sent_translation(row,approval.text,result['message_id'],key)
            return result
