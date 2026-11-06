"""Human email on a case; never creates or impersonates a customer live chat."""
import hashlib,json,re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
from .disclosure import DisclosureCandidate,RecipientContext,RecipientDisclosureGate


class CaseEmail(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    body:str=Field(min_length=1,max_length=4000,repr=False)
    language:Literal['en','id']
    expected_version:int=Field(ge=1)
    membership_id:UUID=Field(strict=False)

    @field_validator('body')
    @classmethod
    def nonblank(cls,value):
        if not value.strip() or '\x00' in value:raise ValueError('Nonempty text required.')
        return value


class CaseEmails:
    def __init__(self,messages):
        self.owner=messages
        self.gate=messages._disclosure or RecipientDisclosureGate()

    async def capability(self,identity,case_id,membership):
        """Routing availability only; every send still checks current grants."""
        async def load():
            async with lifecycle_transaction(self.owner._repository,identity,staff=True) as tx:
                case=await tx.find_case(case_id)
                if case is None or await tx.staff_workspace(case_id,membership) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                ready=await tx.case_email_ready()
                context=await tx.case_email_context(case_id) if ready and case['assigned_membership_id']==membership else None
                available=False
                if context is not None and case['status']!='closed':
                    try:
                        RecipientContext.from_record(identity.session.tenant_id,context.get('recipient'),'email')
                        available=True
                    except AuthError:pass
                ancestry=await tx.case_correspondence_ancestry(case_id) if ready else []
                records=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),ancestry)
                if records and self.owner._disclosure is None:available=False
                if await tx.staff_workspace(case_id,membership) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                current=await tx.find_case(case_id)
                if current!=case:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return PrivateProjection.capture(identity.session.tenant_id,case_id,
                    value=dict(case_id=case_id,case_version=case['version'],membership_id=membership,email_available=available),
                    basis=dict(case=case,context=context,ancestry=ancestry,membership=membership),records=records)
        return await self.owner._history.read(identity,load,purpose='staff')

    async def target(self,tx,identity,case_id,command,*,replay=False):
        case=await tx.find_case(case_id,lock=True)
        if case is None or await tx.staff_workspace(case_id,command.membership_id) is None:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        member=await tx.target_membership(command.membership_id,case)
        if (member is None or UUID(member['principal_id'])!=identity.session.principal_id
            or case['assigned_membership_id']!=command.membership_id):raise DraftError(409,'STALE_MESSAGE')
        if not replay and (case['status']=='closed' or case['version']!=command.expected_version):
            raise DraftError(409,'STALE_MESSAGE')
        return case

    async def send(self,identity,case_id,command,key,*,participant=None):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):
            raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
        if participant is not None:
            from airport_assistant.modules.agent.case_translation import CaseTranslationApprovalParticipant
            if type(participant) is not CaseTranslationApprovalParticipant:raise TypeError('Typed internal approval required.')
            if (participant.identity.session!=identity.session or participant.case_id!=case_id or participant.key!=key
                or participant.owner.command(participant.row,body=participant.approval.text)!=command):
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        digest=hashlib.sha256(json.dumps(dict(kind='case_email',case_id=str(case_id),
            **command.model_dump(mode='json')),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        current=None;initial=None;result=None;job_id=None
        async def load(tx):
            nonlocal current
            if not await tx.case_email_ready():raise DraftError(503,'MAIL_UNAVAILABLE')
            # Parent lock serializes allocation with the existing inbound writer.
            case=await tx.find_case(case_id,lock=True)
            if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            await tx.send_key(key)
            previous=await tx.staff_receipt(key)
            if previous is not None and previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            case=await self.target(tx,identity,case_id,command,replay=previous is not None)
            if command.language not in await tx.languages():raise DraftError(422,'VALIDATION_ERROR')
            context=await tx.case_email_context(case_id)
            if (context is None or context.get('tenant_id')!=identity.session.tenant_id
                or context.get('case_id')!=case_id or context.get('case_version')!=case['version']
                or context.get('assigned_membership_id')!=command.membership_id
                or context.get('status')!=case['status']):raise AuthError(404,'RESOURCE_NOT_FOUND')
            recipient=RecipientContext.from_record(identity.session.tenant_id,context.get('recipient'),'email')
            records=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),
                context.get('case_labels'),context.get('draft_labels'),context.get('conversation_labels'))
            if records and self.owner._disclosure is None:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            stored=await tx.staff_disclosure_result(previous['id']) if previous else None
            if previous and (stored is None or stored['message'].get('case_id')!=case_id
                or stored['message'].get('conversation_id') is not None):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if stored:
                records=inherited_records(identity.session.tenant_id,records,
                    stored['message'].get('resource_security_labels'),
                    *([stored['job'].get('resource_security_labels')] if stored['job'] else []))
            part=await participant.load(tx,previous,context) if participant is not None else None
            if part is not None:records=inherited_records(identity.session.tenant_id,records,part.get('resource_security_labels'))
            final=await tx.case_email_context(case_id)
            if final!=context:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            current=DisclosureCandidate(PrivateProjection.capture(identity.session.tenant_id,case_id,
                value=dict(case=case,context=context,previous=previous,stored=stored,participant=part),
                basis=dict(case=case,context=context,previous=previous,stored=stored,participant=part),records=records),recipient)
            return current
        async def after(tx):
            if initial.projection.value['previous'] is not None:
                checked=await load(tx)
                if checked.projection.basis_hash!=initial.projection.basis_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return checked
            await self.target(tx,identity,case_id,command)
            saved=await tx.staff_disclosure_result(result['id'])
            previous=await tx.staff_receipt(key)
            expected=dict(initial.projection.value['context']);expected['last_sequence']+=1
            labels=initial.projection.labels.to_records() if initial.projection.labels else []
            message=dict(id=result['id'],tenant_id=identity.session.tenant_id,conversation_id=None,case_id=case_id,
                author_principal_id=identity.session.principal_id,created_session_id=identity.session.id,
                body=command.body,language=command.language,staff_request_key=key,request_hash=digest,
                sequence=expected['last_sequence'],basis_case_version=command.expected_version,
                basis_conversation_version=None,basis_epoch=None,resource_security_labels=labels,
                author_type='staff',source_kind='staff_reply',delivery_channel='email',sender_membership_id=command.membership_id)
            if (saved is None or saved['message']!=message
                or saved['job']!=dict(id=job_id,case_id=case_id,source_message_id=result['id'],resource_security_labels=labels)
                or previous is None or previous['id']!=result['id'] or previous['request_hash']!=digest):
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if participant is not None:await participant.verify(tx)
            context=await tx.case_email_context(case_id)
            if context!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return DisclosureCandidate(PrivateProjection.capture(identity.session.tenant_id,case_id,
                value=saved,basis=dict(context=context,stored=saved),records=labels),
                RecipientContext.from_record(identity.session.tenant_id,context.get('recipient'),'email'))
        def factory(*,before_commit):
            def check():
                before_commit()
                if participant is not None:participant.check()
            return lifecycle_transaction(self.owner._repository,identity,staff=True,before_commit=check)
        async with self.gate.mutation(identity,factory,load,after) as (tx,initial):
            value=initial.projection.value
            if value['previous'] is not None:return tx.message_view(value['previous'],case_id)
            result=await tx.case_email_message(value['case'],command,key,digest,requirements=initial.projection.labels)
            job_id=await tx.enqueue_email(case_id,result['id'],requirements=initial.projection.labels)
            if participant is not None:await participant.commit(tx,value['participant'],result)
            response=tx.message_view(result,case_id)
        if participant is not None:participant.check(committed=True)
        return response
