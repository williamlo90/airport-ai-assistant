"""Staff chat writes and INTERNAL AI publication, never a model-callable tool."""
from dataclasses import dataclass,field
import hashlib
import hmac
import json
import re
import secrets
from typing import Literal
from uuid import UUID,uuid4
from pydantic import BaseModel,ConfigDict,Field,field_validator
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.conversations.service import ConversationService,ConversationError
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.agent.answers import KnowledgeAnswerUnavailable,staff_offer


class StaffMessage(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    conversation_id:UUID=Field(strict=False)
    channel:Literal['chat','email']
    body:str=Field(min_length=1,max_length=4000)
    language:Literal['en','id']
    expected_version:int=Field(ge=1)
    expected_conversation_version:int=Field(ge=1)
    expected_epoch:int=Field(ge=1)

    @field_validator('body')
    @classmethod
    def nonblank(cls,value):
        if not value.strip() or '\x00' in value:raise ValueError('Nonempty text required.')
        return value


class StaffMessages:
    def __init__(self,repository,private_access=None,*,private_disclosure=False):
        if type(private_disclosure) is not bool:raise ValueError('Explicit private disclosure switch required.')
        from .disclosure import RecipientDisclosureGate
        self._repository=repository
        self._disclosure=RecipientDisclosureGate(private_access) if private_disclosure else None
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._history=PrivateContentGate(private_access)

    async def receipt(self,identity,case_id,key,membership):
        """Recover a human send without sending again or requiring old write versions."""
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):
            raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        async def load():
            async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
                case=await tx.find_case(case_id)
                if case is None or await tx.staff_workspace(case_id,membership) is None:
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                row=await tx.staff_receipt(key)
                if row is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                # Resolve the stored parent; the URL must never supply a receipt's case.
                stored=await tx.staff_disclosure_result(row['id'])
                message=stored['message'] if stored else None
                if (message is None or message.get('case_id')!=case_id
                    or message.get('tenant_id')!=identity.session.tenant_id
                    or message.get('author_principal_id')!=identity.session.principal_id
                    or message.get('staff_request_key')!=key):raise AuthError(404,'RESOURCE_NOT_FOUND')
                chat=None
                if message['conversation_id'] is not None:
                    chat=await tx.chat(message['conversation_id'])
                    if chat is None or chat['case_id']!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
                ancestry=await tx.staff_receipt_ancestry(message['id'],case_id)
                if ancestry is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                records=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),
                    message.get('resource_security_labels'),
                    ancestry.get('case_labels'),ancestry.get('conversation_labels'),
                    ancestry.get('origin_labels'),
                    *([stored['job'].get('resource_security_labels')] if stored['job'] is not None else []))
                if await tx.staff_workspace(case_id,membership) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                return PrivateProjection.capture(identity.session.tenant_id,message['id'],
                    value=tx.message_view(message,case_id),
                    basis=dict(case=case,chat=chat,stored=stored,ancestry=ancestry,membership=membership),records=records)
        return await self._history.read(identity,load,purpose='staff')

    async def send(self,identity,case_id,command,key):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
        digest=hashlib.sha256(json.dumps({'case_id':str(case_id),**command.model_dump(mode='json')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if self._disclosure is not None:
            return await self._guarded_send(identity,case_id,command,key,digest)
        async with lifecycle_transaction(self._repository,identity,staff=True) as tx:
            return await self.send_in_transaction(tx,identity,case_id,command,key,digest)

    async def target(self,tx,identity,case_id,command):
        case=await tx.find_case(case_id,lock=True)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        chat=await tx.chat(command.conversation_id,lock=True)
        if chat is None or chat['case_id']!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if case['status']=='closed' or chat['mode']!='human' or case['version']!=command.expected_version or chat['version']!=command.expected_conversation_version or chat['generation_epoch']!=command.expected_epoch:
            raise DraftError(409,'STALE_MESSAGE')
        member=await tx.target_membership(chat['handler_membership_id'],case)
        if member is None or UUID(member['principal_id'])!=identity.session.principal_id or case['assigned_membership_id']!=chat['handler_membership_id']:raise DraftError(409,'STALE_MESSAGE')
        if command.language not in await tx.languages():raise DraftError(422,'VALIDATION_ERROR')
        return case,chat

    async def send_in_transaction(self,tx,identity,case_id,command,key,digest):
        case=await tx.find_case(case_id,lock=True)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        chat=await tx.chat(command.conversation_id,lock=True)
        if chat is None or chat['case_id']!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
        from airport_assistant.modules.source.private_content import inherited_records
        records=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),
            *([chat['resource_security_labels']] if 'resource_security_labels' in chat else []))
        if records:
            raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        await tx.send_key(key)
        previous=await tx.staff_receipt(key)
        if previous is not None:
            if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            if inherited_records(identity.session.tenant_id,previous.get('resource_security_labels')):
                raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            return tx.message_view(previous,case_id)
        case,chat=await self.target(tx,identity,case_id,command)
        if command.channel=='email' and not await tx.mail_available(case_id):raise DraftError(503,'MAIL_UNAVAILABLE')
        result=await tx.staff_message(case,chat,command,key,digest)
        if command.channel=='email':await tx.enqueue_email(case_id,result['id'])
        return tx.message_view(result,case_id)

    async def _guarded_send(self,identity,case_id,command,key,digest,*,participant=None):
        if participant is not None:
            from airport_assistant.modules.agent.private_translation import TranslationApprovalParticipant
            if type(participant) is not TranslationApprovalParticipant:raise TypeError('Internal typed translation participant required.')
            if (participant.identity.session!=identity.session or participant.case_id!=case_id
                or participant.key!=key or participant.approval.text!=command.body):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        from .disclosure import DisclosureCandidate,RecipientContext
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        candidate=None;result=None;job_id=None
        async def load(tx):
            nonlocal candidate
            case=await tx.find_case(case_id,lock=True)
            if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            chat=await tx.chat(command.conversation_id,lock=True)
            if chat is None or chat['case_id']!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
            context=await tx.staff_disclosure_context(case_id,command.conversation_id,command.channel)
            if (context is None or context.get('tenant_id')!=identity.session.tenant_id
                or context.get('case_id')!=case_id or context.get('conversation_id')!=command.conversation_id):
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            await tx.send_key(key)
            previous=await tx.staff_receipt(key)
            saved=None
            if previous is not None:
                if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                saved=await tx.staff_disclosure_result(previous['id'])
                if saved is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                message=saved['message']
                if (message.get('id')!=previous['id'] or message.get('conversation_id')!=command.conversation_id
                    or message.get('case_id')!=case_id or message.get('tenant_id')!=identity.session.tenant_id
                    or message.get('author_principal_id')!=identity.session.principal_id
                    or message.get('staff_request_key')!=key or message.get('request_hash')!=digest
                    or message.get('body')!=command.body or message.get('language')!=command.language
                    or message.get('delivery_channel')!=command.channel):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            else:await self.target(tx,identity,case_id,command)
            part=await participant.load(tx,previous) if participant is not None else None
            # Recipient rows need not be locked by case/conversation locks.
            # Re-read AFTER receipt/result/target awaits, including replay.
            final_context=await tx.staff_disclosure_context(case_id,command.conversation_id,command.channel)
            if final_context!=context:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            context=final_context
            groups=[context.get('case_labels'),context.get('conversation_labels')]
            if part is not None:groups.append(part.get('resource_security_labels'))
            if saved is not None:
                groups.append(saved['message'].get('resource_security_labels'))
                if saved['job'] is not None:groups.append(saved['job'].get('resource_security_labels'))
            records=inherited_records(identity.session.tenant_id,*groups)
            recipient=RecipientContext.from_record(identity.session.tenant_id,context.get('recipient'),command.channel) if records else None
            projection=PrivateProjection.capture(identity.session.tenant_id,command.conversation_id,
                value=dict(case=case,chat=chat,context=context,previous=previous,saved=saved,participant=part),
                basis=dict(context=context,previous=previous,saved=saved,digest=digest,participant=part),records=records)
            candidate=DisclosureCandidate(projection,recipient)
            return candidate
        async def after(tx):
            before=candidate.projection.value
            if before['previous'] is not None:
                after=await load(tx)
                if after.projection.basis_hash!=candidate_basis:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return after
            expected=dict(before['context'])
            expected['conversation_version']+=1;expected['last_sequence']+=1
            # Recheck the actual handler/membership after writes, while keeping
            # the command's original pending versions out of historical replay.
            await self.target(tx,identity,case_id,command.model_copy(update={
                'expected_conversation_version':command.expected_conversation_version+1}))
            stored=await tx.staff_disclosure_result(result['id'])
            receipt=await tx.staff_receipt(key)
            records=candidate.projection.labels.to_records() if candidate.projection.labels is not None else []
            if stored is None or receipt is None or receipt['id']!=result['id'] or receipt['request_hash']!=digest:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            message=stored['message'];job=stored['job']
            expected_message=dict(id=result['id'],tenant_id=identity.session.tenant_id,conversation_id=command.conversation_id,
                case_id=case_id,author_principal_id=identity.session.principal_id,created_session_id=identity.session.id,
                body=command.body,language=command.language,staff_request_key=key,request_hash=digest,
                sequence=expected['last_sequence'],basis_case_version=command.expected_version,
                basis_conversation_version=command.expected_conversation_version,basis_epoch=command.expected_epoch,
                resource_security_labels=records,author_type='staff',source_kind='staff_reply',delivery_channel=command.channel,
                sender_membership_id=before['chat']['handler_membership_id'])
            if message!=expected_message:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if command.channel=='email':
                if job!=dict(id=job_id,case_id=case_id,source_message_id=result['id'],resource_security_labels=records):
                    raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            elif job is not None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if participant is not None:await participant.verify(tx,before['participant'],result)
            # Last awaited DB operation: verify current routing/participation
            # again after all actual message/job/receipt reads.
            context=await tx.staff_disclosure_context(case_id,command.conversation_id,command.channel)
            if context!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            recipient=RecipientContext.from_record(identity.session.tenant_id,context.get('recipient'),command.channel) if records else None
            return DisclosureCandidate(PrivateProjection.capture(identity.session.tenant_id,command.conversation_id,
                value=stored,basis=dict(context=context,stored=stored),records=records),recipient)
        def transaction(*,before_commit):
            def check():
                before_commit()
                if participant is not None:participant.before_commit()
            return lifecycle_transaction(self._repository,identity,staff=True,before_commit=check)
        async with self._disclosure.mutation(identity,transaction,load,after) as (tx,current):
            candidate_basis=current.projection.basis_hash
            value=current.projection.value
            if value['previous'] is not None:return tx.message_view(value['previous'],case_id)
            if command.channel=='email' and not await tx.mail_available(case_id):raise DraftError(503,'MAIL_UNAVAILABLE')
            labels=current.projection.labels
            result=await tx.staff_message(value['case'],value['chat'],command,key,digest,requirements=labels)
            if command.channel=='email':job_id=await tx.enqueue_email(case_id,result['id'],requirements=labels)
            if participant is not None:await participant.commit(tx,value['participant'],result)
            response=tx.message_view(result,case_id)
        return response


@dataclass(frozen=True)
class GenerationTicket:
    id:UUID
    nonce:str=field(repr=False)


class GenerationExpired(Exception):
    """A deadline crossed at SQL commit guard after the service precheck."""


class PublicationContextChanged(Exception):
    """Release the conversation lock before looking up its newly linked case."""


class AIPublication:
    """Internal publication port used by explicit agent workflows; no model authority."""
    def __init__(self,repository,answers=None,*,private_capability=None):
        self._repository,self.answers=repository,answers
        self.private_capability=private_capability

    @staticmethod
    def _public_records(row):
        # Old report-only test/adapters may lack this optional projection;
        # the current SQL adapter always projects the actual stored labels.
        records=row.get('resource_security_labels',[])
        if type(records) is not list or records:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')

    @staticmethod
    async def _conversation(tx,identity,id,*,publishing=False):
        locked=await tx.lock_conversation_case(id)
        conversation=await tx.find(id,lock=True)
        if conversation is None and publishing:
            fresh=await tx.find(id)
            if fresh is not None and fresh.case_id!=locked:raise PublicationContextChanged()
        await require_customer_session(tx)
        ConversationService._require(identity,id,conversation,'read_conversation')
        if conversation.case_id!=locked:
            if publishing:raise PublicationContextChanged()
            raise ConversationError(409,'VERSION_CONFLICT')
        return conversation

    async def capture(self,identity,id):
        if self.private_capability is not None:return await self.private_capability.capture(identity,id)
        async with customer_transaction(self._repository,identity) as tx:
            conversation=await self._conversation(tx,identity,id)
            if conversation.mode!='ai' or conversation.case_id is not None or conversation.language not in await tx.languages():raise DraftError(409,'AI_PUBLICATION_BLOCKED')
            ticket=GenerationTicket(uuid4(),secrets.token_urlsafe(32))
            await tx.create_generation(ticket.id,conversation,hashlib.sha256(ticket.nonce.encode()).hexdigest())
            return ticket

    async def capture_bound(self,identity,conversation_id,ticket,version,epoch,*,requirements=None):
        """Internal deterministic run ticket; never accept a browser/model capability."""
        if self.private_capability is not None:
            return await self.private_capability.capture_bound(identity,conversation_id,ticket,version,epoch,requirements=requirements)
        if requirements is not None:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        async with customer_transaction(self._repository,identity) as tx:
            conversation=await self._conversation(tx,identity,conversation_id)
            existing=await tx.generation(ticket.id)
            digest=hashlib.sha256(ticket.nonce.encode()).hexdigest()
            if existing is not None:
                self._public_records(existing)
                if (existing['conversation_id'],existing['input_version'],existing['generation_epoch'],existing['nonce_hash'])!=(conversation_id,version,epoch,digest):
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                return ticket
            if conversation.mode!='ai' or conversation.case_id is not None or (conversation.version,conversation.generation_epoch)!=(version,epoch):
                raise DraftError(409,'AI_PUBLICATION_BLOCKED')
            await tx.create_generation(ticket.id,conversation,digest)
            return ticket

    async def receipt(self,identity,ticket,*,discard=False):
        if self.private_capability is not None:return await self.private_capability.receipt(identity,ticket,discard=discard)
        async with customer_transaction(self._repository,identity) as tx:
            generation=await tx.generation(ticket.id)
            if generation is None:return None
            self._public_records(generation)
            if not hmac.compare_digest(generation['nonce_hash'],hashlib.sha256(ticket.nonce.encode()).hexdigest()):raise AuthError(404,'RESOURCE_NOT_FOUND')
            conversation=await self._conversation(tx,identity,generation['conversation_id'])
            generation=await tx.generation(ticket.id,lock=True)
            if generation is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            self._public_records(generation)
            if generation['state']=='published':
                saved=await tx.generated_message(ticket.id)
                if saved is None:raise DraftError(500,'INTERNAL_ERROR')
                self._public_records(saved)
                return dict(state='published',message_id=saved['id'],sequence=saved['sequence'])
            if generation['state']=='stale':return dict(state='discarded',reason='STALE_GENERATION')
            if discard or generation['expires_at']<=await tx.now() or conversation.mode!='ai' or conversation.case_id is not None or (conversation.version,conversation.generation_epoch,conversation.language)!=(generation['input_version'],generation['generation_epoch'],generation['language']):
                await tx.stale_generation(ticket.id)
                return dict(state='discarded',reason='STALE_GENERATION')
            return None

    async def publish_prepared(self,identity,ticket,collector,requirements):
        if self.private_capability is None:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        return await self.private_capability.publish_prepared(identity,ticket,collector,requirements)

    async def publish(self,identity,ticket,body,*,grounding=None,private_input=None):
        if self.private_capability is not None:
            return await self.private_capability.publish(identity,ticket,body,grounding=grounding,private_input=private_input)
        if private_input is not None:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        if type(ticket) is not GenerationTicket or type(body) is not str or not 1<=len(body)<=4000 or not body.strip() or '\x00' in body:raise DraftError(422,'VALIDATION_ERROR')
        try:
            return await self._publish_once(identity,ticket,body,grounding=grounding)
        except PublicationContextChanged:
            # Only re-authorize and discard pending work after releasing locks.
            # Never replay a message write or lock case after conversation.
            return await self._publish_once(identity,ticket,body,discard_only=True)

    async def _publish_once(self,identity,ticket,body,*,discard_only=False,grounding=None):
        async with customer_transaction(self._repository,identity) as tx:
            generation=await tx.generation(ticket.id)
            if generation is None or not hmac.compare_digest(generation['nonce_hash'],hashlib.sha256(ticket.nonce.encode()).hexdigest()):raise AuthError(404,'RESOURCE_NOT_FOUND')
            self._public_records(generation)
            conversation=await self._conversation(tx,identity,generation['conversation_id'],publishing=True)
            generation=await tx.generation(ticket.id,lock=True)
            if generation is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            self._public_records(generation)
            if generation['state']=='published':
                saved=await tx.generated_message(ticket.id)
                if saved is None:raise DraftError(500,'INTERNAL_ERROR')
                self._public_records(saved)
                if saved['body']!=body:raise DraftError(409,'MESSAGE_CONFLICT')
                return dict(state='published',message_id=saved['id'],sequence=saved['sequence'])
            if generation['state']=='stale':return dict(state='discarded',reason='STALE_GENERATION')
            if discard_only or conversation.mode!='ai' or conversation.case_id is not None or conversation.version!=generation['input_version'] or conversation.generation_epoch!=generation['generation_epoch'] or conversation.language!=generation['language'] or generation['language'] not in await tx.languages() or generation['expires_at']<=await tx.now():
                await tx.stale_generation(ticket.id)
                return dict(state='discarded',reason='STALE_GENERATION')
            try:
                async with tx.savepoint():
                    if grounding is not None:
                        if self.answers is None:raise KnowledgeAnswerUnavailable()
                        metadata=await self.answers.validate_transaction(identity,grounding,tx,generation['language'],body)
                        saved=await tx.ai_message(conversation,generation,body,knowledge_evidence=metadata)
                    else:saved=await tx.ai_message(conversation,generation,body)
                    await tx.published_generation(ticket.id,saved['id'])
            except GenerationExpired:
                await tx.stale_generation(ticket.id)
                return dict(state='discarded',reason='STALE_GENERATION')
            except KnowledgeAnswerUnavailable:
                # The failed source write/savepoint has rolled back, including
                # counters and partial NOWAIT locks. Keep the same authorized
                # generation receipt, but publish only a deterministic staff offer.
                if generation['expires_at']<=await tx.now():
                    await tx.stale_generation(ticket.id)
                    return dict(state='discarded',reason='STALE_GENERATION')
                try:
                    async with tx.savepoint():
                        saved=await tx.ai_message(conversation,generation,staff_offer(generation['language']))
                        await tx.published_generation(ticket.id,saved['id'])
                except GenerationExpired:
                    await tx.stale_generation(ticket.id)
                    return dict(state='discarded',reason='STALE_GENERATION')
            return dict(state='published',message_id=saved['id'],sequence=saved['sequence'])

    async def citations(self,identity,conversation_id,message_id):
        if self.private_capability is not None:return await self.private_capability.citations(identity,conversation_id,message_id)
        async with customer_transaction(self._repository,identity) as tx:
            await self._conversation(tx,identity,conversation_id)
            value=await tx.message_knowledge(conversation_id,message_id)
            if value is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            self._public_records(value)
            metadata=value['knowledge_evidence']
            return dict(message_id=message_id,status='historical_publication',issued_at=metadata['issued_at'] if metadata else None,
                citations=metadata['citations'] if metadata else [])
