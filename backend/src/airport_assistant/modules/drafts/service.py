"""Draft lifecycle and routing; no consent, case, email or source transaction."""

from dataclasses import replace,asdict
from datetime import timedelta
from typing import Protocol,AsyncContextManager
from uuid import UUID,uuid4

from airport_assistant.modules.conversations.service import ConversationTransaction,ConversationService
from airport_assistant.modules.identity.access import AccessService,ResourceKey,OwnedObject
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session
from airport_assistant.modules.drafts.models import Draft,ReportPayload,Destination,DraftError,displayed_summary,reviewed_summary,source_review,with_source_evidence,seal,require_integrity


class DraftTransaction(ConversationTransaction,Protocol):
    async def lock_draft_sources(self,id:UUID)->tuple[UUID,...]:...
    async def lock_draft_case(self,id:UUID):...
    async def handoff_case(self,id:UUID,*,lock:bool=False):...
    async def active_handoff(self,id:UUID)->bool:...
    async def now(self):...
    async def destination(self,service_type:str)->Destination|None:...
    async def locate_draft(self,id:UUID)->UUID|None:...
    async def find_draft(self,id:UUID)->Draft|None:...
    async def create_draft(self,draft:Draft)->Draft:...
    async def edit_draft(self,draft:Draft)->Draft|None:...
    async def terminal(self,id:UUID,state:str)->Draft|None:...
    async def selected_attachment_context(self,draft_id:UUID,ids:tuple[UUID,...])->list[dict]:...


class DraftRepository(Protocol):
    def transaction(self,context:DatabaseContext)->AsyncContextManager[DraftTransaction]:...


class DraftService:
    def __init__(self,repository:DraftRepository,private_access=None):
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._repository=repository
        self._private_content=PrivateContentGate(private_access)

    @staticmethod
    async def _conversation(tx,identity,id,*,candidate_only=False):
        conversation=await tx.find(id,lock=True)
        # A competing confirmation can link a newly created case while this
        # FOR UPDATE waits. Its statement snapshot cannot yet see that parent
        # through RLS. Re-read once with a fresh statement; never replay writes.
        if conversation is None:conversation=await tx.find(id,lock=True)
        await require_customer_session(tx)
        if candidate_only:ConversationService._owned_candidate(identity,id,conversation)
        else:AccessService.require_object(identity,ResourceKey(identity.session.tenant_id,'conversation',id),
            conversation.projection() if conversation else None,'read_conversation')
        return conversation

    async def _load(self,tx,identity,id,action,*,candidate_only=False,parents_locked=False,locked_conversation=None):
        if not parents_locked:await tx.lock_draft_case(id)
        if locked_conversation is None:
            conversation_id=await tx.locate_draft(id)
            await require_customer_session(tx)
            if conversation_id is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            conversation=await self._conversation(tx,identity,conversation_id,candidate_only=candidate_only)
        else:
            # Only the same transaction's just-loaded, row-locked creation
            # candidate is reused. No permission/result survives source I/O.
            if not parents_locked or not candidate_only:raise TypeError('Locked creation candidate required.')
            conversation=locked_conversation;conversation_id=conversation.id
            ConversationService._owned_candidate(identity,conversation_id,conversation)
        draft=await tx.find_draft(id)
        await require_customer_session(tx)
        if draft is None or draft.created_session_id!=identity.session.id or draft.conversation_id!=conversation_id:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        if candidate_only and draft.resource_security_labels:
            from airport_assistant.modules.source.labels import SecurityLabels
            try:SecurityLabels.from_records(draft.tenant_id,list(draft.resource_security_labels))
            except (ValueError,TypeError):raise AuthError(404,'RESOURCE_NOT_FOUND') from None
        target=OwnedObject(draft.tenant_id,draft.id,'draft',draft.actor_principal_id,draft.actor_session_id,
            draft.case_id is None or draft.case_access_verified) if candidate_only else draft.projection()
        AccessService.require_object(identity,ResourceKey(identity.session.tenant_id,'draft',id),target,action)
        require_integrity(draft)
        if draft.state=='committed' and await tx.case_result(draft.result_case_id) is None:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        return conversation,draft

    @staticmethod
    async def _destination(tx,payload):
        destination=await tx.destination(payload.service_type)
        if destination is None and payload.service_type!='general':destination=await tx.destination('general')
        if destination is None:raise DraftError(503,'DRAFT_DESTINATION_UNAVAILABLE')
        return destination

    @staticmethod
    async def _expired(tx,draft):
        if draft.state=='pending' and draft.expires_at<=await tx.now():
            result=await tx.terminal(draft.id,'expired')
            if result is None:
                await require_customer_session(tx)
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            return result
        return draft

    async def _creation_candidate(self,tx,identity,conversation_id,case_id):
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        target=await tx.handoff_case(case_id,lock=True) if case_id is not None else None
        if case_id is not None and target is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        conversation=await self._conversation(tx,identity,conversation_id,candidate_only=True)
        if conversation.case_id is not None:raise DraftError(409,'HANDOFF_ALREADY_REQUESTED')
        parent=await tx.source_case_context(case_id) if case_id is not None else None
        if case_id is not None and (parent is None or parent.get('id')!=case_id):raise AuthError(404,'RESOURCE_NOT_FOUND')
        records=inherited_records(identity.session.tenant_id,list(conversation.resource_security_labels),
            *([parent.get('resource_security_labels')] if parent is not None else []))
        await require_customer_session(tx)
        target_basis=None if target is None else {**target,'destination':target['destination'].model_dump(mode='json')}
        return PrivateProjection.capture(identity.session.tenant_id,conversation_id,value=conversation,
            basis=dict(conversation=asdict(conversation),parent=parent,target=target_basis),records=records)

    async def create(self,identity,conversation_id,action_type,payload,case_id=None,*,expected_conversation_version=None,expected_generation_epoch=None,source_evidence=None):
        # source_evidence is an INTERNAL typed argument, absent from public input.
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        if type(payload) is not ReportPayload:raise DraftError(422,'VALIDATION_ERROR')
        if source_evidence is not None:
            # Existing-case handoff does not amend that case's source snapshot.
            # New evidence belongs to the explicit additional-info flow.
            if case_id is not None:raise DraftError(422,'VALIDATION_ERROR')
            try:
                source_evidence=source_review(payload,source_evidence)
                if source_evidence.tenant_id!=identity.session.tenant_id:raise ValueError()
            except (ValueError,TypeError):raise DraftError(422,'VALIDATION_ERROR') from None
        result=None;current=None
        async def load(tx):
            nonlocal current
            current=await self._creation_candidate(tx,identity,conversation_id,case_id)
            return current
        async def after(tx):
            parent=await load(tx)
            _,stored=await self._load(tx,identity,result.id,'read_draft',candidate_only=True,parents_locked=True,
                locked_conversation=parent.value)
            if stored!=result or stored.case_id!=case_id or stored.conversation_id!=conversation_id:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            records=inherited_records(identity.session.tenant_id,
                parent.labels.to_records() if parent.labels is not None else [],list(stored.resource_security_labels))
            return PrivateProjection.capture(identity.session.tenant_id,conversation_id,value=stored,records=records,
                basis=dict(parent=parent.basis_hash,draft_id=stored.id,hash=stored.payload_hash,state=stored.state))
        async with self._private_content.mutation(identity,lambda *,before_commit:customer_transaction(self._repository,identity,before_commit=before_commit),load,
                additional_labels=source_evidence.labels if source_evidence is not None else None,after_loader=after) as tx:
            result=await self._create(tx,identity,conversation_id,action_type,payload,case_id,candidate_only=True,
                source_evidence=source_evidence,expected_conversation_version=expected_conversation_version,
                expected_generation_epoch=expected_generation_epoch,locked_conversation=current.value)
        return result

    async def create_in_transaction(self,tx,identity,conversation_id,action_type,payload,case_id=None,*,expected_conversation_version=None,expected_generation_epoch=None):
        # Internal shared customer unit: caller owns session entry/exit checks and commit.
        # Legacy callers remain report-only; source-bearing creation uses create's
        # gate so no grant/provider I/O can happen inside their DB transaction.
        return await self._create(tx,identity,conversation_id,action_type,payload,case_id,
            expected_conversation_version=expected_conversation_version,expected_generation_epoch=expected_generation_epoch)

    async def create_source_in_transaction(self,tx,identity,conversation_id,action_type,payload,*,source_evidence,requirements,expected_conversation_version,expected_generation_epoch):
        # Internal only: caller owns the source-gated unit and its exact postloader.
        # No source/provider IO is permitted here, and the legacy entry stays closed.
        from airport_assistant.modules.source.labels import SecurityLabels
        if type(requirements) is not SecurityLabels or requirements.tenant_id!=identity.session.tenant_id:
            raise DraftError(422,'VALIDATION_ERROR')
        try:source_review(payload,source_evidence)
        except (ValueError,TypeError):raise DraftError(422,'VALIDATION_ERROR') from None
        return await self._create(tx,identity,conversation_id,action_type,payload,candidate_only=True,
            source_evidence=source_evidence,source_requirements=requirements,
            expected_conversation_version=expected_conversation_version,expected_generation_epoch=expected_generation_epoch)

    async def _create(self,tx,identity,conversation_id,action_type,payload,case_id=None,*,expected_conversation_version=None,expected_generation_epoch=None,candidate_only=False,source_evidence=None,source_requirements=None,locked_conversation=None):
        if action_type not in ('create_case','request_handoff') or type(payload) is not ReportPayload:
            raise DraftError(422,'VALIDATION_ERROR')
        if (expected_conversation_version is None)!=(expected_generation_epoch is None) or any(v is not None and(type(v) is not int or v<1) for v in (expected_conversation_version,expected_generation_epoch)):
            raise DraftError(422,'VALIDATION_ERROR')
        target=await tx.handoff_case(case_id,lock=True) if case_id is not None else None
        if case_id is not None:
            if action_type!='request_handoff' or target is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            if target['status']=='closed':raise DraftError(409,'CASE_CLOSED')
            if payload.contact_email is not None:raise DraftError(422,'VALIDATION_ERROR')
        if locked_conversation is None:
            conversation=await self._conversation(tx,identity,conversation_id,candidate_only=candidate_only)
        else:
            if not candidate_only or locked_conversation.id!=conversation_id:raise TypeError('Same-transaction creation candidate required.')
            conversation=locked_conversation
            ConversationService._owned_candidate(identity,conversation_id,conversation)
        if expected_conversation_version is not None and(conversation.version!=expected_conversation_version or conversation.generation_epoch!=expected_generation_epoch or conversation.mode!='ai'):
            raise DraftError(409,'VERSION_CONFLICT')
        if conversation.case_id is not None:raise DraftError(409,'HANDOFF_ALREADY_REQUESTED')
        if payload.attachment_ids:raise DraftError(422,'VALIDATION_ERROR')
        from airport_assistant.modules.source.private_content import inherited_records
        parent=await tx.source_case_context(case_id) if case_id is not None else None
        if case_id is not None and (parent is None or parent.get('id')!=case_id):raise AuthError(404,'RESOURCE_NOT_FOUND')
        labels=inherited_records(identity.session.tenant_id,list(conversation.resource_security_labels),
            *([parent.get('resource_security_labels')] if parent is not None else []),
            *([source_requirements.to_records()] if source_requirements is not None else []))
        if labels and not candidate_only:raise AuthError(404,'RESOURCE_NOT_FOUND')
        destination=target['destination'] if target else await self._destination(tx,payload)
        now=await tx.now()
        draft=Draft(uuid4(),identity.session.tenant_id,conversation_id,identity.session.principal_id,
            identity.session.id if identity.session.kind=='anonymous' else None,identity.session.id,action_type,
            payload,destination,displayed_summary(action_type,payload,destination,conversation.language,case_id),
            conversation.language,1,'',now,now+timedelta(minutes=15),case_id=case_id,resource_security_labels=tuple(labels))
        if source_evidence is not None:
            try:draft=with_source_evidence(draft,source_evidence)
            except ValueError:raise DraftError(422,'VALIDATION_ERROR') from None
        result=await tx.create_draft(seal(draft))
        if result is None:
            await require_customer_session(tx)
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        require_integrity(result)
        return result

    async def get(self,identity,id):
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        async def load():
            async with customer_transaction(self._repository,identity) as tx:
                conversation,draft=await self._load(tx,identity,id,'read_draft',candidate_only=True)
                current=await self._conversation(tx,identity,draft.conversation_id,candidate_only=True)
                parents={}
                for case_id in (conversation.case_id,current.case_id,draft.case_id,draft.result_case_id):
                    if case_id is not None and case_id not in parents:
                        parent=await tx.source_case_context(case_id)
                        if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                        parents[case_id]=parent
                files=await tx.selected_attachment_context(draft.id,draft.payload.attachment_ids) if draft.payload.attachment_ids else []
                if {row['id'] for row in files}!=set(draft.payload.attachment_ids):raise AuthError(404,'RESOURCE_NOT_FOUND')
                labels=inherited_records(identity.session.tenant_id,list(draft.resource_security_labels),
                    list(conversation.resource_security_labels),list(current.resource_security_labels),
                    *(p.get('resource_security_labels') for p in parents.values()),
                    *(f.get('resource_security_labels') for f in files))
                if labels:
                    # A denied private GET must not write even its expiry marker.
                    # Confirmation/edit still persist/enforce server deadlines.
                    if draft.state=='pending' and draft.expires_at<=await tx.now():
                        draft=replace(draft,state='expired')
                else:draft=await self._expired(tx,draft)
                return PrivateProjection.capture(identity.session.tenant_id,id,value=draft,
                    basis=dict(hash=draft.payload_hash,version=draft.version,state=draft.state,
                        result_case=draft.result_case_id,result_message=draft.result_message_id,result_ref=draft.result_ref,
                        original=asdict(conversation),current=asdict(current),files=files,
                        parents={str(k):v for k,v in parents.items()}),records=labels)
        return await self._private_content.read(identity,load)

    async def _mutation_candidate(self,tx,identity,id,action,*,historical_receipt=False):
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        # All parents before conversation/draft locks; do not discover and lock
        # a new case after taking its child lock (that would invert lock order).
        locked=set(await tx.lock_draft_sources(id))
        conversation,draft=await self._load(tx,identity,id,action,candidate_only=True,parents_locked=True)
        ids={case_id for case_id in (conversation.case_id,draft.case_id,draft.result_case_id) if case_id is not None}
        if not ids<=locked:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        parents={}
        for case_id in sorted(ids):
            parent=await tx.source_case_context(case_id)
            if parent is None or parent.get('id')!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
            parents[str(case_id)]=parent
        files=await tx.selected_attachment_context(draft.id,draft.payload.attachment_ids) if draft.payload.attachment_ids else []
        if {row['id'] for row in files}!=set(draft.payload.attachment_ids):raise AuthError(404,'RESOURCE_NOT_FOUND')
        labels=inherited_records(identity.session.tenant_id,list(draft.resource_security_labels),
            list(conversation.resource_security_labels),*(p.get('resource_security_labels') for p in parents.values()),
            *(f.get('resource_security_labels') for f in files))
        if not (historical_receipt and draft.state=='committed') and labels!=inherited_records(identity.session.tenant_id,list(draft.resource_security_labels)):
            raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        await require_customer_session(tx)
        return PrivateProjection.capture(identity.session.tenant_id,id,value=(conversation,draft),records=labels,
            basis=dict(hash=draft.payload_hash,version=draft.version,state=draft.state,expires_at=draft.expires_at,
                result_case=draft.result_case_id,result_message=draft.result_message_id,result_ref=draft.result_ref,
                conversation=asdict(conversation),parents=parents,files=files))

    async def edit(self,identity,id,expected_version,payload):
        if type(expected_version) is not int or expected_version<1 or type(payload) is not ReportPayload:
            raise DraftError(422,'VALIDATION_ERROR')
        error=None
        current=None
        async def load(tx):
            nonlocal current
            current=await self._mutation_candidate(tx,identity,id,'edit_draft')
            return current
        async with self._private_content.mutation(identity,lambda *,before_commit:customer_transaction(self._repository,identity,before_commit=before_commit),load) as tx:
            conversation,draft=current.value
            draft=await self._expired(tx,draft)
            if draft.state=='expired':error=DraftError(410,'DRAFT_EXPIRED')
            elif draft.state!='pending':error=DraftError(409,'DRAFT_NOT_PENDING',draft.result_ref)
            elif expected_version!=draft.version:error=DraftError(409,'VERSION_CONFLICT')
            else:
                if draft.case_id is not None and payload.attachment_ids:raise DraftError(422,'VALIDATION_ERROR')
                if payload.attachment_ids and not await tx.validate_attachments(draft.id,payload.attachment_ids):raise AuthError(404,'RESOURCE_NOT_FOUND')
                target=await tx.handoff_case(draft.case_id,lock=True) if draft.case_id else None
                if draft.case_id and target is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                if target and target['status']=='closed':raise DraftError(409,'CASE_CLOSED')
                if target and payload.contact_email is not None:raise DraftError(422,'VALIDATION_ERROR')
                destination=target['destination'] if target else await self._destination(tx,payload)
                try:
                    summary=reviewed_summary(draft.action_type,payload,destination,conversation.language,draft.case_id,
                        source_evidence=draft.source_evidence)
                except ValueError:
                    raise DraftError(422,'VALIDATION_ERROR') from None
                proposed=seal(replace(draft,payload=payload,destination=destination,
                    displayed_summary=summary,
                    language=conversation.language,version=draft.version+1))
                result=await tx.edit_draft(proposed)
                await require_customer_session(tx)
                if result is None:
                    draft=await self._expired(tx,draft)
                    error=DraftError(410,'DRAFT_EXPIRED') if draft.state=='expired' else DraftError(409,'VERSION_CONFLICT')
                else:require_integrity(result)
        # Expiration state must commit even though editing an expired draft is410.
        if error is not None:raise error
        return result

    async def cancel(self,identity,id):
        error=None
        current=None
        async def load(tx):
            nonlocal current
            current=await self._mutation_candidate(tx,identity,id,'cancel_draft')
            return current
        async with self._private_content.mutation(identity,lambda *,before_commit:customer_transaction(self._repository,identity,before_commit=before_commit),load) as tx:
            _,draft=current.value
            draft=await self._expired(tx,draft)
            if draft.state=='pending':
                draft=await tx.terminal(id,'cancelled')
                await require_customer_session(tx)
                if draft is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            if draft.state=='expired':error=DraftError(410,'DRAFT_EXPIRED')
            elif draft.state!='cancelled':error=DraftError(409,'DRAFT_NOT_PENDING',draft.result_ref)
        if error is not None:raise error
        return draft
