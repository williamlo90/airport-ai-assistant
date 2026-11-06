"""Optional customer feedback; desired-state updates do not resolve a case."""
import time
from functools import partial
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel,ConfigDict,Field,field_validator
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import customer_transaction
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.operation import SourceReadOperation
from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection,inherited_records


class RatingInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=0,le=2147483646)
    score:int=Field(ge=1,le=5)
    comment:str|None=Field(default=None,min_length=1,max_length=2000,repr=False)

    @field_validator('comment')
    @classmethod
    def meaningful(cls,value):
        if value is not None:
            if not value.strip() or '\x00' in value:raise ValueError('Nonblank comment required.')
            try:value.encode('utf-8')
            except UnicodeError:raise ValueError('Valid Unicode required.') from None
        return value


class CustomerRatings:
    def __init__(self,repository,source_access=None):
        self._repository,self._source=repository,source_access
        self.clock=time.monotonic

    def operation(self):
        source=SourceReadOperation(self._source,self.clock)
        return PrivateContentGate(source,clock=self.clock),source.fresh

    async def candidate(self,tx,identity,kind,id,*,lock=False):
        if kind not in {'case','conversation'}:raise DraftError(422,'VALIDATION_ERROR')
        if kind=='case' and identity.session.kind!='customer':raise AuthError(401,'AUTH_REQUIRED')
        if await tx.ratings_ready() is not True:raise DraftError(503,'CASE_UNAVAILABLE')
        parent=await tx.rating_target(kind,id,lock=lock)
        if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        row=await tx.rating(kind,id,lock=lock)
        labels=inherited_records(identity.session.tenant_id,parent['resource_security_labels'],
            row['resource_security_labels'] if row else [])
        return PrivateProjection.capture(identity.session.tenant_id,id,value=(parent,row),
            basis=dict(parent=parent,rating=row),records=labels)

    @staticmethod
    def view(kind,id,parent,row):
        fields=('id','score','comment','version','created_at','updated_at')
        return dict(target_type=kind,target_id=id,eligible=parent['eligible'],
            rating={name:row[name] for name in fields} if row else None)

    async def get(self,identity,kind:Literal['case','conversation'],id):
        gate,fresh=self.operation()
        async def load():
            async with customer_transaction(self._repository,identity) as tx:
                projection=await self.candidate(tx,identity,kind,id)
                parent,row=projection.value
                return PrivateProjection.capture(identity.session.tenant_id,id,value=self.view(kind,id,parent,row),
                    basis=dict(parent=parent,rating=row),records=projection.labels.to_records() if projection.labels else [])
        result=await gate.read(identity,load)
        fresh()
        return result

    async def save(self,identity,kind,id,command):
        if type(command) is not RatingInput:raise DraftError(422,'VALIDATION_ERROR')
        gate,fresh=self.operation();current=None;result=None
        async def load(tx):
            nonlocal current
            current=await self.candidate(tx,identity,kind,id,lock=True)
            return current
        transaction=partial(customer_transaction,self._repository,identity)
        def checked():fresh()
        async with gate.mutation(identity,transaction,load,before_commit=checked) as tx:
            parent,row=current.value
            version=row['version'] if row else 0
            same=row is not None and (row['score'],row['comment'])==(command.score,command.comment)
            if command.expected_version!=version and not(same and command.expected_version+1==version):
                raise DraftError(409,'VERSION_CONFLICT')
            if not same:
                if not parent['eligible']:raise DraftError(409,'RATING_NOT_AVAILABLE')
                labels=current.labels.to_records() if current.labels else []
                await tx.save_rating(kind,id,row['id'] if row else uuid4(),version,command,labels)
            result=await self.candidate(tx,identity,kind,id,lock=True)
            parent,row=result.value
            if row is None or (row['score'],row['comment'])!=(command.score,command.comment):
                raise DraftError(503,'CASE_UNAVAILABLE')
            result=self.view(kind,id,parent,row)
        fresh()
        return result
