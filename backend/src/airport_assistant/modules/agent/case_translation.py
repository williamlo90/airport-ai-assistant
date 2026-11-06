"""Case-email translation preview: model proposes, a staff member approves."""
import copy,hashlib,json,time
from datetime import timedelta
from typing import Literal
from uuid import UUID
from pydantic import Field,model_validator
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.conversations.case_email import CaseEmail,CaseEmails
from airport_assistant.modules.conversations.disclosure import RecipientContext
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.private_content import PrivateProjection,PrivateContentGate,inherited_records
from airport_assistant.modules.mail.private_outbound import fingerprint
from .translation import TranslationText,TranslationApproval,preserve_values
from .models import AgentError


class CaseTranslationRequest(CaseEmail):
    target_language:Literal['en','id']
    request_key:UUID=Field(strict=False)
    @model_validator(mode='after')
    def different(self):
        if self.language==self.target_language:raise ValueError('Choose another language.')
        return self


def context_hash(context):
    return fingerprint(json.loads(json.dumps(context,default=str)))


class CaseEmailTranslations:
    def __init__(self,translations,*,clock=time.monotonic):
        self.owner=translations;self.clock=clock;self.gate=PrivateContentGate(clock=clock)
        self.messages=CaseEmails(translations.messages)

    @staticmethod
    def command(row,*,body=None):
        return CaseEmail(body=row['original_body'] if body is None else body,
            language=row['source_language'] if body is None else row['target_language'],
            membership_id=row['membership_id'],expected_version=row['case_version'])

    def factory(self,identity,check=None):
        def transaction(*,before_commit):
            def guard():
                before_commit()
                if check is not None:check()
            return lifecycle_transaction(self.owner.repository,identity,staff=True,before_commit=guard)
        return transaction

    async def snapshot(self,tx,identity,case_id,*,id=None,key=None,request=None,write=False):
        if not await tx.case_translation_ready():raise AgentError(503,'AGENT_UNAVAILABLE')
        case=await tx.find_case(case_id,lock=write)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        row=await tx.case_translation(id,key=key,lock=write)
        if row is not None and (row['case_id']!=case_id or row['principal_id']!=identity.session.principal_id
            or row['session_id']!=identity.session.id):raise AuthError(404,'RESOURCE_NOT_FOUND')
        member=request.membership_id if request is not None else row['membership_id'] if row else None
        if member is None or await tx.staff_workspace(case_id,member) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        ancestry=await tx.case_correspondence_ancestry(case_id)
        if ancestry is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        context=None
        if write:
            command=request if request is not None else self.command(row)
            await self.messages.target(tx,identity,case_id,command)
            context=await tx.case_email_context(case_id)
            if context is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            RecipientContext.from_record(identity.session.tenant_id,context.get('recipient'),'email')
        records=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),ancestry,
            *([row.get('resource_security_labels')] if row else []))
        if await tx.staff_workspace(case_id,member) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return PrivateProjection.capture(identity.session.tenant_id,case_id,
            value=dict(row=copy.deepcopy(row),case=case,context=context,member=member),
            basis=dict(row=row,case=case,context=context,ancestry=ancestry,member=member),records=records)

    async def get(self,identity,case_id,id=None,*,key=None):
        async def load():
            async with lifecycle_transaction(self.owner.repository,identity,staff=True) as tx:
                result=await self.snapshot(tx,identity,case_id,id=id,key=key)
                if result.value['row'] is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                return result
        return (await self.gate.read(identity,load,purpose='staff'))['row']

    async def prepare(self,identity,case_id,request):
        digest=hashlib.sha256(json.dumps(dict(case_id=str(case_id),**request.model_dump(mode='json',exclude={'request_key'})),
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        current=None;created=None
        async def load(tx):
            nonlocal current
            current=await self.snapshot(tx,identity,case_id,key=request.request_key,request=request,write=True)
            return current
        async def after(tx):
            if created is None:
                result=await load(tx)
                if result.basis_hash!=initial.basis_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return result
            result=await self.snapshot(tx,identity,case_id,id=created['id'],write=True)
            if result.value['row']!=created or result.value['context']!=initial.value['context']:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self.gate.mutation(identity,self.factory(identity),load,purpose='staff',after_loader=after) as tx:
            initial=current;previous=initial.value['row']
            if previous is not None:
                if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                return previous
            if request.target_language not in await tx.languages():raise DraftError(422,'VALIDATION_ERROR')
            created=await tx.create_case_translation(case_id,request,digest,context_hash(initial.value['context']),initial.labels)
        try:
            translated=TranslationText(text=await self.owner.model.translate(request.body,request.language,request.target_language)).text
            preserve_values(request.body,translated)
            return await self.finish(identity,case_id,created,translated)
        except Exception:
            # No retry. Revocation/context change may also forbid a failure marker.
            try:await self.finish(identity,case_id,created,None)
            except (AuthError,DraftError,AgentError,SourceFailure):pass
            raise

    async def finish(self,identity,case_id,reserved,text):
        current=None;expected=None;deadline=None
        def check(*,committed=False):
            if deadline is not None and self.clock()>=deadline:
                if committed:raise SourceFailure(503)
                raise AgentError(409,'AGENT_TRANSLATION_EXPIRED')
        async def load(tx):
            nonlocal current,deadline
            current=await self.snapshot(tx,identity,case_id,id=reserved['id'],write=True)
            if current.value['row']!=reserved or reserved['state']!='generating':raise DraftError(409,'VERSION_CONFLICT')
            if context_hash(current.value['context'])!=reserved['source_context_hash']:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if text is not None:
                anchor=self.clock();now=await tx.now()
                candidate=anchor+(reserved['created_at']+timedelta(seconds=120)-now).total_seconds()
                deadline=candidate if deadline is None else min(deadline,candidate)
                check()
            return current
        async def after(tx):
            result=await self.snapshot(tx,identity,case_id,id=reserved['id'],write=True)
            if result.value['row']!=expected or result.value['context']!=current.value['context']:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self.gate.mutation(identity,self.factory(identity,check),load,purpose='staff',after_loader=after) as tx:
            expected=dict(reserved,state='ready' if text is not None else 'failed',translated_body=text,revision=reserved['revision']+1)
            actual=await tx.finish_case_translation(reserved,expected['state'],text=text)
            if actual!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        check(committed=True)
        return expected

    async def approve(self,identity,case_id,id,approval,key):
        approval=TranslationApproval.model_validate(approval)
        row=await self.get(identity,case_id,id)
        participant=CaseTranslationApprovalParticipant(self,identity,case_id,row,approval,key)
        command=self.command(row,body=approval.text)
        return await self.messages.send(identity,case_id,command,key,participant=participant)


class CaseTranslationApprovalParticipant:
    """Internal participant binds draft approval to the atomic message/job commit."""
    def __init__(self,owner,identity,case_id,row,approval,key):
        self.owner,self.identity,self.case_id,self.row,self.approval,self.key=owner,identity,case_id,row,approval,key
        self.deadline=None;self.expected=None

    async def load(self,tx,previous,context):
        row=await tx.case_translation(self.row['id'],lock=True)
        if row!=self.row:raise DraftError(409,'VERSION_CONFLICT')
        if row['state']=='sent':
            if (previous is None or row['message_id']!=previous['id'] or row['reviewed_body']!=self.approval.text
                or row['send_key']!=self.key):raise DraftError(409,'IDEMPOTENCY_CONFLICT')
        else:
            if previous is not None:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            if row['state']!='ready' or row['revision']!=self.approval.expected_revision:raise DraftError(409,'VERSION_CONFLICT')
            if context_hash(context)!=row['source_context_hash']:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            anchor=self.owner.clock();now=await tx.now()
            candidate=anchor+(row['expires_at']-now).total_seconds()
            self.deadline=candidate if self.deadline is None else min(self.deadline,candidate)
            self.check()
        return copy.deepcopy(row)

    def check(self,*,committed=False):
        if self.deadline is not None and self.owner.clock()>=self.deadline:
            if committed:raise SourceFailure(503)
            raise AgentError(409,'AGENT_TRANSLATION_EXPIRED')

    async def commit(self,tx,row,message):
        self.expected=dict(row,state='sent',reviewed_body=self.approval.text,message_id=message['id'],send_key=self.key,revision=row['revision']+1)
        actual=await tx.finish_case_translation(row,'sent',reviewed=self.approval.text,message_id=message['id'],key=self.key)
        if actual!=self.expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')

    async def verify(self,tx):
        if await tx.case_translation(self.row['id'])!=self.expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
