"""UI confirmation creates one atomic report case, never a booking/payment."""

from uuid import UUID,uuid4
from typing import Protocol,AsyncContextManager
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session
from airport_assistant.modules.drafts.models import DraftError,require_confirmation
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.drafts.service import DraftTransaction
from airport_assistant.modules.cases.models import CaseDeadlineLost,request_hash,validate_request


class CaseTransaction(DraftTransaction,Protocol):
    async def lock_key(self,key):...
    async def idempotency(self,key):...
    async def verified_contact(self,email):...
    async def lock_destination(self,destination):...
    def savepoint(self)->AsyncContextManager:...
    async def create_case(self,draft,case_id,message_id,owner_contact,followup_contact,request_id):...
    async def mark_committed(self,draft,result):...
    async def record_key(self,key,digest,draft,result):...
    async def case_result(self,id):...
    async def receipt(self,draft_id):...


class CaseRepository(Protocol):
    def transaction(self,context)->AsyncContextManager[CaseTransaction]:...


class CaseService:
    def __init__(self,repository:CaseRepository):
        self._repository=repository
        self._drafts=DraftService(repository)

    async def status(self,identity,id):
        # Receipts belong to the resulting case owner. This is not permission
        # to reopen the original session-bound preview or confirm it again.
        async with customer_transaction(self._repository,identity) as tx:
            result=await tx.receipt(id)
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
        async with customer_transaction(self._repository,identity) as tx:
            await tx.lock_key(key)
            _,draft=await self._drafts._load(tx,identity,id,'confirm_draft')
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
                if draft.case_id is not None or draft.resource_security_labels:
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                destination=await tx.lock_destination(draft.destination)
                if destination!=draft.destination:raise DraftError(409,'CASE_DESTINATION_CHANGED')
                owner_contact,followup_contact=await self._contact(tx,identity,draft)
                try:
                    async with tx.savepoint():
                        result=await tx.create_case(draft,uuid4(),uuid4(),owner_contact,followup_contact,request_id)
                        if not await tx.mark_committed(draft,result):raise CaseDeadlineLost()
                        await tx.record_key(key,digest,draft,result)
                except CaseDeadlineLost:
                    await require_customer_session(tx)
                    draft=await self._drafts._expired(tx,draft)
                    error=DraftError(410,'DRAFT_EXPIRED') if draft.state=='expired' else DraftError(409,'VERSION_CONFLICT')
        if error is not None:raise error
        return result
