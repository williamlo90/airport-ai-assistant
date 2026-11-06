"""UI confirmation creates one atomic report case, never a booking/payment."""

from uuid import UUID,uuid4
import re
from typing import Protocol,AsyncContextManager
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session
from airport_assistant.modules.drafts.models import DraftError,require_confirmation
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.drafts.service import DraftTransaction
from airport_assistant.modules.cases.models import CaseDeadlineLost,CommitResult,request_hash,validate_request


class CaseTransaction(DraftTransaction,Protocol):
    async def confirmation_context(self,id:UUID)->dict|None:...
    async def handoff_consent(self,draft,request_id):...
    async def lock_key(self,key):...
    async def idempotency(self,key):...
    async def verified_contact(self,email):...
    async def lock_destination(self,destination):...
    def savepoint(self)->AsyncContextManager:...
    async def create_case(self,draft,case_id,message_id,owner_contact,followup_contact,request_id):...
    async def mark_committed(self,draft,result):...
    async def record_key(self,key,digest,draft,result):...
    async def case_result(self,id):...
    async def receipt(self,draft_id)->dict|None:...


class CaseRepository(Protocol):
    def transaction(self,context)->AsyncContextManager[CaseTransaction]:...


class CaseService:
    def __init__(self,repository:CaseRepository,private_access=None):
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._repository=repository
        self._drafts=DraftService(repository,private_access)
        self._private_content=PrivateContentGate(private_access)

    async def status(self,identity,id):
        # Receipts belong to the resulting case owner. This is not permission
        # to reopen the original session-bound preview or confirm it again.
        from airport_assistant.modules.source.private_content import PrivateProjection
        seen_receipt=False
        async def load():
            nonlocal seen_receipt
            async with customer_transaction(self._repository,identity) as tx:
                result=await tx.receipt(id)
                if result is None:
                    if seen_receipt:raise AuthError(404,'RESOURCE_NOT_FOUND')
                    return PrivateProjection.capture(identity.session.tenant_id,id,value=None,basis={'pending':True},records=[])
                seen_receipt=True
                if (type(result) is not dict or set(result)!={'draft_id','state','case_id','result_ref','version','expires_at'}
                    or result['draft_id']!=id or result['state']!='committed' or not isinstance(result['case_id'],UUID)
                    or type(result['version']) is not int or result['version']<1 or result['expires_at'] is not None
                    or not isinstance(result['result_ref'],str) or not re.fullmatch(r'CASE-[a-f0-9]{32}',result['result_ref'])):
                    raise DraftError(500,'INTERNAL_ERROR')
                parent=await tx.source_case_context(result['case_id'])
                if parent is None or parent.get('id')!=result['case_id']:raise AuthError(404,'RESOURCE_NOT_FOUND')
                return PrivateProjection.capture(identity.session.tenant_id,id,value=result,
                    basis=dict(case=parent,receipt=result),
                    records=parent.get('resource_security_labels'))
        result=await self._private_content.read(identity,load)
        if result is not None:return result
        return await self._drafts.get(identity,id)

    @staticmethod
    async def _contact(tx,identity,draft):
        report=draft.payload
        if identity.session.kind=='anonymous':
            if (draft.action_type!='request_handoff' or report.category!='information' or report.service_type!='general'
                or report.booking_reference is not None or report.transaction_reference is not None or report.contact_email is not None):
                raise AuthError(401,'AUTH_REQUIRED')
            return None,None
        if draft.action_type=='create_case' and report.contact_email is None:
            raise DraftError(422,'CASE_CONTACT_REQUIRED')
        contact=await tx.verified_contact(report.contact_email)
        if contact is None:raise DraftError(422,'CASE_CONTACT_VERIFICATION_REQUIRED')
        return contact,contact if report.contact_email is not None else None

    async def confirm(self,identity,id,expected_version,payload_hash,key,request_id):
        validate_request(expected_version,payload_hash,key)
        if not isinstance(request_id,UUID):raise DraftError(422,'VALIDATION_ERROR')
        digest=request_hash(id,expected_version,payload_hash)
        error=None
        current=None
        initially_committed=None
        async def load(tx):
            nonlocal current,initially_committed
            await tx.lock_key(key)
            current=await self._drafts._mutation_candidate(tx,identity,id,'confirm_draft',historical_receipt=True)
            if initially_committed is None:initially_committed=current.value[1].state=='committed'
            return current
        async def after(tx):
            from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
            candidate=await load(tx)
            _,stored=candidate.value
            context=None
            records=candidate.labels.to_records() if candidate.labels is not None else []
            # Both create_case and an untargeted handoff create a new case.
            # Existing-case handoffs retain that case's original provenance.
            if stored.state=='committed' and stored.case_id is None:
                context=await tx.confirmation_context(stored.result_case_id)
                expected=stored.source_evidence.to_records() if stored.source_evidence is not None else []
                if (context is None or context.get('id')!=stored.result_case_id
                    or context.get('origin_draft_id')!=stored.id or context.get('origin_conversation_id')!=stored.conversation_id
                    or (not initially_committed and (context.get('source_observations')!=expected
                        or inherited_records(identity.session.tenant_id,context.get('resource_security_labels'))
                            !=inherited_records(identity.session.tenant_id,list(stored.resource_security_labels))))):
                    raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                records=inherited_records(identity.session.tenant_id,records,context.get('resource_security_labels'))
            return PrivateProjection.capture(identity.session.tenant_id,id,value=candidate.value,
                basis=dict(draft=candidate.basis_hash,confirmed_case=context),
                records=records)
        async with self._private_content.mutation(identity,lambda *,before_commit:customer_transaction(self._repository,identity,before_commit=before_commit),load,after_loader=after) as tx:
            _,draft=current.value
            previous=await tx.idempotency(key)
            if previous is not None and previous!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            if draft.state=='committed':
                if draft.version!=expected_version or draft.payload_hash!=payload_hash:raise DraftError(409,'VERSION_CONFLICT')
                result=await tx.case_result(draft.result_case_id)
                if result is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                if previous is None:await tx.record_key(key,digest,draft,result)
                return result
            draft=await self._drafts._expired(tx,draft)
            if draft.state=='expired':error=DraftError(410,'DRAFT_EXPIRED')
            else:
                require_confirmation(draft,expected_version=expected_version,payload_hash=payload_hash,now=await tx.now())
                if draft.case_id is not None and draft.source_evidence is not None:
                    raise DraftError(422,'VALIDATION_ERROR')
                destination=await tx.lock_destination(draft.destination)
                if destination!=draft.destination:raise DraftError(409,'CASE_DESTINATION_CHANGED')
                target=await tx.handoff_case(draft.case_id,lock=True) if draft.case_id else None
                if draft.case_id and target is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                if target and target['status']=='closed':raise DraftError(409,'CASE_CLOSED')
                if target and await tx.active_handoff(draft.case_id):raise DraftError(409,'HANDOFF_ALREADY_REQUESTED')
                conversation=await tx.find(draft.conversation_id)
                if conversation.case_id is not None:raise DraftError(409,'HANDOFF_ALREADY_REQUESTED')
                owner_contact,followup_contact=(None,None) if target else await self._contact(tx,identity,draft)
                try:
                    async with tx.savepoint():
                        if target:
                            result=CommitResult(case_id=draft.case_id,result_ref=target['result_ref'],message_id=target['message_id'])
                            await tx.handoff_consent(draft,request_id)
                        else:result=await tx.create_case(draft,uuid4(),uuid4(),owner_contact,followup_contact,request_id)
                        if not await tx.mark_committed(draft,result):raise CaseDeadlineLost()
                        await tx.record_key(key,digest,draft,result)
                except CaseDeadlineLost:
                    await require_customer_session(tx)
                    draft=await self._drafts._expired(tx,draft)
                    error=DraftError(410,'DRAFT_EXPIRED') if draft.state=='expired' else DraftError(409,'VERSION_CONFLICT')
        if error is not None:raise error
        return result
