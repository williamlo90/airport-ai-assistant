"""Manual customer-service decisions; no AI closure or payment execution."""
from contextlib import asynccontextmanager
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import CustomerSessionLost,run_before_commit
from airport_assistant.modules.drafts.models import DraftError


class StatusChange(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=1)
    expected_conversation_version:int|None=Field(default=None,ge=1)
    status:Literal['in_progress','awaiting_customer','awaiting_external','closed']
    next_action:str=Field(min_length=1,max_length=2000)
    close_reason:Literal['resolved','customer_cancelled','duplicate','no_response']|None=None
    resolution:str|None=Field(default=None,min_length=1,max_length=4000)
    related_case_id:UUID|None=Field(default=None,strict=False)
    followup_evidence:str|None=Field(default=None,min_length=1,max_length=2000)

    @field_validator('next_action','resolution','followup_evidence')
    @classmethod
    def nonblank(cls,value):
        if value is not None and (not value.strip() or '\x00' in value):raise ValueError('Nonblank text required.')
        return value

    @model_validator(mode='after')
    def shape(self):
        if self.status=='closed':
            if self.close_reason is None or self.resolution is None:raise ValueError('Closure reason and resolution required.')
            if (self.close_reason=='duplicate')!=(self.related_case_id is not None):raise ValueError('Duplicate reference required only for duplicate closure.')
            if (self.close_reason=='no_response')!=(self.followup_evidence is not None):raise ValueError('Follow-up evidence required only for no_response.')
        elif any(v is not None for v in (self.close_reason,self.resolution,self.related_case_id,self.followup_evidence)):
            raise ValueError('Closure fields require closed status.')
        return self


class Assignment(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=1)
    expected_conversation_version:int|None=Field(default=None,ge=1)
    assignee_id:UUID|None=Field(strict=False)


class ContinueCase(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=1)
    intent:Literal['same_issue']
    message:str=Field(min_length=1,max_length=4000)
    client_request_id:UUID=Field(strict=False)

    @field_validator('message')
    @classmethod
    def nonblank(cls,value):return StatusChange.nonblank(value)


NEXT_ACTOR={'new':'staff','in_progress':'staff','awaiting_customer':'customer','awaiting_external':'external','closed':'none'}
TRANSITIONS={'new':{'in_progress','closed'},'in_progress':{'in_progress','awaiting_customer','awaiting_external','closed'},
    'awaiting_customer':{'awaiting_customer','in_progress','closed'},'awaiting_external':{'awaiting_external','in_progress','closed'}}


def decide_status(case,command):
    if case['status']=='closed':raise DraftError(409,'CASE_CLOSED')
    if command.status not in TRANSITIONS[case['status']]:raise DraftError(409,'CASE_TRANSITION_INVALID')
    if command.status!='closed' and case['assigned_membership_id'] is None:raise DraftError(409,'CASE_ASSIGNEE_REQUIRED')
    return dict(status=command.status,next_actor=NEXT_ACTOR[command.status],next_action=command.next_action,
        close_reason=command.close_reason,resolution=command.resolution,duplicate_of_case_id=command.related_case_id,
        followup_evidence=command.followup_evidence)


@asynccontextmanager
async def lifecycle_transaction(repository,identity,*,staff=False,customer=False,before_commit=None):
    if before_commit is not None and not callable(before_commit):raise TypeError('before_commit must be callable.')
    if staff and identity.session.kind!='staff' or customer and identity.session.kind!='customer':raise AuthError(401,'AUTH_REQUIRED')
    try:
        async with repository.transaction(identity.database_context) as tx:
            if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
            yield tx
            if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
            run_before_commit(before_commit)
    except CustomerSessionLost:raise AuthError(401,'AUTH_REQUIRED') from None


class CaseLifecycle:
    def __init__(self,repository,private_access=None,*,private_mail_history=False):
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._repository=repository
        self._private_content=PrivateContentGate(private_access)
        from airport_assistant.modules.mail.history import StaffMailHistory
        self._mail_history=StaffMailHistory(repository,private_history=private_mail_history)

    @staticmethod
    async def _find(tx,id,version=None):
        case=await tx.find_case(id,lock=version is not None)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if version is not None and case['version']!=version:raise DraftError(409,'VERSION_CONFLICT')
        return case

    async def get(self,identity,id,*,staff=False,membership=None):
        if not staff and identity.session.kind=='staff':raise AuthError(403,'ACTION_FORBIDDEN')
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        async def load():
            async with lifecycle_transaction(self._repository,identity,staff=staff) as tx:
                case=await self._find(tx,id,version=None)
                if membership is not None:
                    if not staff or await tx.staff_workspace(id,membership) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                value=await tx.public_case(case)
                current=await self._find(tx,id,version=None)
                related={}
                for target in (case.get('duplicate_of_case_id'),current.get('duplicate_of_case_id')):
                    if target is not None and target not in related:
                        related[target]=await tx.related_source_context(target)
                if value.get('related_case_ref') is not None and related.get(case.get('duplicate_of_case_id')) is None:
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                labels=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),current.get('resource_security_labels'),
                    *(r.get('resource_security_labels') for r in related.values() if r is not None))
                if membership is not None and await tx.staff_workspace(id,membership) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                return PrivateProjection.capture(identity.session.tenant_id,id,value=value,
                    basis=dict(case=current,original_case=case,view=value,membership=membership,
                        related={str(k):v for k,v in related.items()}),records=labels)
        return await self._private_content.read(identity,load,purpose='staff' if staff else 'customer')

    async def email_replies(self,identity,id):
        return await self._mail_history.read(identity,id,'replies')

    async def queue(self,identity,status=None):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            return await tx.queue(status)

    async def status(self,identity,id,command):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            case=await self._find(tx,id,command.expected_version)
            from airport_assistant.modules.conversations.handoff import linked_version
            chat=await linked_version(tx,case,command.expected_conversation_version)
            values=decide_status(case,command)
            if command.status!='closed' and await tx.current_assignee(id) is None:raise DraftError(409,'CASE_ASSIGNEE_REQUIRED')
            if command.related_case_id is not None:
                # Only metadata is needed; don't lock two cases in reverse order.
                related=await tx.find_case(command.related_case_id)
                if related is None or related['id']==id or (related['owner_principal_id'],related['owner_anonymous_session_id'])!=(case['owner_principal_id'],case['owner_anonymous_session_id']):
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
            from uuid import uuid4
            transition=uuid4() if chat else None
            updated=await tx.update_case(case,values)
            await tx.history(case,updated,'status_changed',transition=transition)
            if chat:
                current=await tx.move_chat(chat,'ended' if command.status=='closed' else chat['mode'],None if command.status=='closed' else chat['handler_membership_id'],transition)
                await tx.transition(transition,'status',case,updated,chat,current)
            return await tx.public_case(updated)

    async def assign(self,identity,id,command):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            case=await self._find(tx,id,command.expected_version)
            from airport_assistant.modules.conversations.handoff import linked_version
            chat=await linked_version(tx,case,command.expected_conversation_version)
            if case['status']=='closed':raise DraftError(409,'CASE_CLOSED')
            target=await tx.target_membership(command.assignee_id,case)
            if command.assignee_id is not None and target is None:raise DraftError(422,'CASE_ASSIGNEE_INVALID')
            supervisor=await tx.supervisor(case)
            if chat and not supervisor:raise AuthError(403,'ACTION_FORBIDDEN')
            if not supervisor and(case['assigned_membership_id'] is not None or target is None or UUID(target['principal_id'])!=identity.session.principal_id):
                raise AuthError(403,'ACTION_FORBIDDEN')
            if command.assignee_id==case['assigned_membership_id']:raise DraftError(409,'ASSIGNEE_CONFLICT')
            if command.assignee_id is None and case['status']!='new':raise DraftError(409,'CASE_ASSIGNEE_REQUIRED')
            values=dict(assigned_membership_id=command.assignee_id)
            # Self-claim accepts the case. Supervisor may preassign new cases.
            if not supervisor and case['status']=='new':values.update(status='in_progress',next_actor='staff',next_action='Petugas sedang menangani kasus.')
            from uuid import uuid4
            transition=uuid4() if chat else None
            updated=await tx.update_case(case,values)
            await tx.history(case,updated,'assigned',transition=transition)
            if chat:
                current=await tx.move_chat(chat,chat['mode'],command.assignee_id if chat['mode']=='human' else None,transition)
                await tx.transition(transition,'assignment',case,updated,chat,current)
            return await tx.public_case(updated)

    async def continue_case(self,identity,id,command):
        from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection,inherited_records
        from airport_assistant.modules.source.operation import SourceReadOperation
        digest=hashlib.sha256(json.dumps(command.model_dump(mode='json'),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        source=SourceReadOperation(self._private_content.source_access,self._private_content.clock)
        gate=PrivateContentGate(source,clock=self._private_content.clock)
        def fresh():source.fresh()
        related_ids=set();current=None;result=None
        async def load(tx):
            nonlocal current
            case=await tx.find_case(id,lock=True)
            if case is None or case['owner_principal_id']!=identity.session.principal_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
            ancestry=await tx.continuation_ancestry(id)
            labels=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),ancestry)
            previous=await tx.reopen_receipt(id,command.client_request_id)
            if previous is not None:
                if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            else:
                if case['version']!=command.expected_version:raise DraftError(409,'VERSION_CONFLICT')
                if case['status']!='closed':raise DraftError(409,'CASE_NOT_CLOSED')
            target=case.get('duplicate_of_case_id')
            if target is not None:related_ids.add(target)
            related={}
            for target in sorted(related_ids):
                meta=await tx.related_source_context(target)
                if meta is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                required=await tx.continuation_ancestry(target)
                labels=inherited_records(identity.session.tenant_id,labels,meta.get('resource_security_labels'),required)
                related[str(target)]=dict(meta=meta,labels=required)
            view=await tx.public_case(case)
            current=PrivateProjection.capture(identity.session.tenant_id,id,value=(case,previous,view),
                basis=dict(case=case,receipt=previous,view=view,related=related,labels=labels),records=labels)
            return current
        transaction=lambda *,before_commit=None:lifecycle_transaction(self._repository,identity,customer=True,before_commit=before_commit)
        async with gate.mutation(identity,transaction,load,before_commit=fresh) as tx:
            case,previous,result=current.value
            if previous is None:
                result=await self._continue_write(tx,case,command,digest)
        source.fresh()
        return result

    async def _continue_write(self,tx,case,command,digest):
        id=case['id']
        assignee=await tx.current_assignee(id)
        status='in_progress' if assignee is not None else 'new'
        updated=await tx.update_case(case,dict(status=status,assigned_membership_id=assignee,next_actor='staff',
            next_action='Pelanggan meminta kelanjutan masalah yang sama.',close_reason=None,resolution=None,duplicate_of_case_id=None,followup_evidence=None))
        await tx.history(case,updated,'reopened',command=command,digest=digest)
        return await tx.public_case(updated)
