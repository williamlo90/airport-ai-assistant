"""Human confirms original information; no AI write, status decision or reopen."""
import re
import time
from uuid import uuid4

from airport_assistant.modules.cases.information_preview import InformationInput
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.operation import SourceReadOperation
from airport_assistant.modules.source.private_content import PrivateContentGate, PrivateProjection, inherited_records


class CustomerInformation:
    def __init__(self,repository,codec,private_access=None):
        self._repository,self._codec=repository,codec
        self._source=private_access
        self.clock=time.monotonic

    def _operation(self):
        source=SourceReadOperation(self._source,self.clock)
        return PrivateContentGate(source,clock=self.clock),source.fresh

    async def _parent(self,tx,identity,case_id,*,lock=False):
        if not await tx.information_ready():raise DraftError(503,'CASE_UNAVAILABLE')
        case=await tx.find_case(case_id,lock=lock)
        if case is None or case['owner_principal_id']!=identity.session.principal_id:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        ancestry=await tx.customer_ancestry(case_id)
        labels=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),ancestry)
        return case,labels

    async def preview(self,identity,case_id,command):
        gate,source_fresh=self._operation()
        if type(command) is not InformationInput:raise DraftError(422,'VALIDATION_ERROR')
        async def load():
            async with lifecycle_transaction(self._repository,identity,customer=True) as tx:
                case,labels=await self._parent(tx,identity,case_id)
                if case['version']!=command.expected_version:raise DraftError(409,'VERSION_CONFLICT')
                if case['status']=='closed':raise DraftError(409,'CASE_CLOSED')
                if command.language not in await tx.languages():raise DraftError(422,'VALIDATION_ERROR')
                return PrivateProjection.capture(identity.session.tenant_id,case_id,value=case,
                    basis=dict(case=case,labels=labels),records=labels)
        await gate.read(identity,load)
        source_fresh()
        record,token=self._codec.issue(identity,case_id,command)
        return dict(preview_id=record.preview_id,case_id=case_id,expected_version=record.expected_version,
            message=record.message,language=record.language,expires_at=record.expires_at,confirmation_token=token)

    @staticmethod
    def _view(receipt):
        if (receipt['sequence'] is None or receipt['message']!=receipt['stored_body']
            or receipt['language']!=receipt['stored_language']):raise DraftError(503,'CASE_UNAVAILABLE')
        return dict(state='saved',preview_id=receipt['id'],case_id=receipt['case_id'],message_id=receipt['message_id'],
            sequence=receipt['sequence'],case_version=receipt['expected_version']+1)

    @staticmethod
    def _key(key):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):
            raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')

    async def receipt(self,identity,case_id,key):
        gate,source_fresh=self._operation()
        self._key(key)
        async def load():
            async with lifecycle_transaction(self._repository,identity,customer=True) as tx:
                case,labels=await self._parent(tx,identity,case_id)
                row=await tx.information_receipt(key=key)
                if row is None or row['case_id']!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
                labels=inherited_records(identity.session.tenant_id,labels,row['resource_security_labels'],row['message_labels'])
                return PrivateProjection.capture(identity.session.tenant_id,case_id,value=self._view(row),
                    basis=dict(case=case,receipt=row),records=labels)
        result=await gate.read(identity,load)
        source_fresh()
        return result

    async def confirm(self,identity,case_id,token,key):
        gate,source_fresh=self._operation()
        self._key(key)
        record=self._codec.read(identity,case_id,token,allow_expired=True)
        digest=self._codec.digest(record)
        current=None;result=None;writing=False
        def fresh():
            source_fresh()
            if writing and self._codec._clock()>=record.expires_at:raise SourceFailure(503)
        async def load(tx):
            nonlocal current
            case,labels=await self._parent(tx,identity,case_id,lock=True)
            await tx.information_key_lock(key)
            row=await tx.information_receipt(key=key)
            if row is not None:
                if row['request_hash']!=digest or row['case_id']!=case_id:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                labels=inherited_records(identity.session.tenant_id,labels,row['resource_security_labels'],row['message_labels'])
            else:
                if await tx.information_receipt(preview=record.preview_id) is not None:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                if case['status']=='closed':raise DraftError(409,'CASE_CLOSED')
                if case['version']!=record.expected_version:raise DraftError(409,'VERSION_CONFLICT')
                if self._codec._clock()>=record.expires_at:raise DraftError(410,'PREVIEW_EXPIRED')
            current=PrivateProjection.capture(identity.session.tenant_id,case_id,value=(case,row,labels),
                basis=dict(case=case,receipt=row,labels=labels),records=labels)
            return current
        transaction=lambda *,before_commit=None:lifecycle_transaction(self._repository,identity,customer=True,before_commit=before_commit)
        async with gate.mutation(identity,transaction,load,before_commit=fresh) as tx:
            _,row,labels=current.value
            if row is None:
                writing=True;fresh()
                await tx.publish_information(record,key,digest,uuid4(),labels)
                row=await tx.information_receipt(key=key)
                if row is None:raise DraftError(503,'CASE_UNAVAILABLE')
            result=self._view(row)
        fresh()
        return result


