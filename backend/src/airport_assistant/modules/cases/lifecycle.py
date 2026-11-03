"""Manual customer-service decisions; no AI closure or payment execution."""
from contextlib import asynccontextmanager
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import CustomerSessionLost
from airport_assistant.modules.drafts.models import DraftError


class StatusChange(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=1)
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
async def lifecycle_transaction(repository,identity,*,staff=False,customer=False):
    if staff and identity.session.kind!='staff' or customer and identity.session.kind!='customer':raise AuthError(401,'AUTH_REQUIRED')
    try:
        async with repository.transaction(identity.database_context) as tx:
            if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
            yield tx
            if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
    except CustomerSessionLost:raise AuthError(401,'AUTH_REQUIRED') from None


class CaseLifecycle:
    def __init__(self,repository):self._repository=repository

    @staticmethod
    async def _find(tx,id,version=None):
        case=await tx.find_case(id,lock=version is not None)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if version is not None and case['version']!=version:raise DraftError(409,'VERSION_CONFLICT')
        return case

    async def get(self,identity,id,*,staff=False):
        if not staff and identity.session.kind=='staff':raise AuthError(403,'ACTION_FORBIDDEN')
        async with lifecycle_transaction(self._repository,identity,staff=staff) as tx:
            case=await self._find(tx,id)
            return await tx.public_case(case)

    async def queue(self,identity,status=None):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            return await tx.queue(status)

    async def status(self,identity,id,command):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            case=await self._find(tx,id,command.expected_version)
            values=decide_status(case,command)
            if command.status!='closed' and await tx.current_assignee(id) is None:raise DraftError(409,'CASE_ASSIGNEE_REQUIRED')
            if command.related_case_id is not None:
                # Only metadata is needed; don't lock two cases in reverse order.
                related=await tx.find_case(command.related_case_id)
                if related is None or related['id']==id or (related['owner_principal_id'],related['owner_anonymous_session_id'])!=(case['owner_principal_id'],case['owner_anonymous_session_id']):
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
            updated=await tx.update_case(case,values)
            await tx.history(case,updated,'status_changed')
            return await tx.public_case(updated)

    async def assign(self,identity,id,command):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            case=await self._find(tx,id,command.expected_version)
            if case['status']=='closed':raise DraftError(409,'CASE_CLOSED')
            target=await tx.target_membership(command.assignee_id,case)
            if command.assignee_id is not None and target is None:raise DraftError(422,'CASE_ASSIGNEE_INVALID')
            supervisor=await tx.supervisor(case)
            if not supervisor and(case['assigned_membership_id'] is not None or target is None or UUID(target['principal_id'])!=identity.session.principal_id):
                raise AuthError(403,'ACTION_FORBIDDEN')
            if command.assignee_id==case['assigned_membership_id']:raise DraftError(409,'ASSIGNEE_CONFLICT')
            if command.assignee_id is None and case['status']!='new':raise DraftError(409,'CASE_ASSIGNEE_REQUIRED')
            values=dict(assigned_membership_id=command.assignee_id)
            # Self-claim accepts the case. Supervisor may preassign new cases.
            if not supervisor and case['status']=='new':values.update(status='in_progress',next_actor='staff',next_action='Petugas sedang menangani kasus.')
            updated=await tx.update_case(case,values)
            await tx.history(case,updated,'assigned')
            return await tx.public_case(updated)

    async def continue_case(self,identity,id,command):
        digest=hashlib.sha256(json.dumps(command.model_dump(mode='json'),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        async with lifecycle_transaction(self._repository,identity,customer=True) as tx:
            case=await tx.find_case(id,lock=True)
            if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            previous=await tx.reopen_receipt(id,command.client_request_id)
            if previous is not None:
                if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                return await tx.public_case(case)
            if case['version']!=command.expected_version:raise DraftError(409,'VERSION_CONFLICT')
            if case['status']!='closed':raise DraftError(409,'CASE_NOT_CLOSED')
            assignee=await tx.current_assignee(id)
            status='in_progress' if assignee is not None else 'new'
            updated=await tx.update_case(case,dict(status=status,assigned_membership_id=assignee,next_actor='staff',
                next_action='Pelanggan meminta kelanjutan masalah yang sama.',close_reason=None,resolution=None,duplicate_of_case_id=None,followup_evidence=None))
            await tx.history(case,updated,'reopened',command=command,digest=digest)
            return await tx.public_case(updated)
