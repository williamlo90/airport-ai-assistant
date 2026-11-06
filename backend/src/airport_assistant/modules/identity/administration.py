"""Organization-scoped administration of recorded staff access, never case data."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,model_validator
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError

Identifier=Annotated[UUID,Field(strict=False)]


class MembershipEdit(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    actor_membership_id:Identifier
    actor_version:int=Field(ge=1,le=2147483647)
    expected_version:int=Field(ge=1,le=2147483646)
    active:bool
    roles:list[Literal['cs','supervisor','admin']]=Field(min_length=1,max_length=3)
    queue_ids:list[Identifier]=Field(max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.roles))!=len(self.roles) or len(set(self.queue_ids))!=len(self.queue_ids):
            raise ValueError('Unique roles and queues required.')
        return self


class StaffAdministration:
    def __init__(self,repository):self._repository=repository

    async def scope(self,tx,actor):
        if await tx.administration_ready() is not True:raise DraftError(503,'STAFF_UNAVAILABLE')
        value=await tx.admin_workspace(actor)
        if value is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return value

    async def workspace(self,identity,actor):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            return await self.scope(tx,actor)

    async def directory(self,identity,actor,*,after=None,limit=25):
        if type(limit) is not int or not 1<=limit<=100:raise DraftError(422,'VALIDATION_ERROR')
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            scope=await self.scope(tx,actor)
            rows=await tx.admin_directory(actor,after,limit+1)
            if rows is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            current=await self.scope(tx,actor)
            if scope!=current:raise DraftError(409,'MEMBERSHIP_SCOPE_CHANGED')
            items,more=rows[:limit],len(rows)>limit
            return dict(workspace=current,items=items,limit=limit,has_more=more,
                next_after=items[-1]['principal_id'] if more else None,consistency='live_keyset')

    async def get(self,identity,principal,actor):
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            scope=await self.scope(tx,actor)
            target=await tx.admin_member(actor,principal)
            if target is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            if scope!=await self.scope(tx,actor):raise DraftError(409,'MEMBERSHIP_SCOPE_CHANGED')
            return dict(workspace=scope,member=target)

    async def update(self,identity,principal,command):
        if type(command) is not MembershipEdit:raise DraftError(422,'VALIDATION_ERROR')
        if principal==identity.session.principal_id:raise AuthError(403,'ACTION_FORBIDDEN')
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            scope=await self.scope(tx,command.actor_membership_id)
            if scope['membership_version']!=command.actor_version:raise DraftError(409,'MEMBERSHIP_SCOPE_CHANGED')
            result=await tx.admin_change(principal,command)
            code=result.get('status') if type(result) is dict else None
            if code in {'not_found','forbidden'}:raise AuthError(404 if code=='not_found' else 403,'RESOURCE_NOT_FOUND' if code=='not_found' else 'ACTION_FORBIDDEN')
            if code=='actor_changed':raise DraftError(409,'MEMBERSHIP_SCOPE_CHANGED')
            if code=='version_conflict':raise DraftError(409,'VERSION_CONFLICT')
            if code=='invalid':raise DraftError(422,'VALIDATION_ERROR')
            if code!='saved':raise DraftError(503,'STAFF_UNAVAILABLE')
            latest=await self.scope(tx,command.actor_membership_id)
            if latest!=scope:raise DraftError(409,'MEMBERSHIP_SCOPE_CHANGED')
            return dict(workspace=latest,member=result['member'])
