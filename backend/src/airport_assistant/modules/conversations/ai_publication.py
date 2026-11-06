"""Explicit customer-private publication capability; not wired by composition.

The adapter must provide current RLS-scoped ai_* projections and atomic graph
writes. This module does not relax existing SQL guards or enable a runtime.
"""
from dataclasses import dataclass,field
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID,uuid4
import hashlib,hmac,secrets,time

from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session,CustomerSessionLost,run_before_commit
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection,inherited_records
from airport_assistant.modules.source.models import SourceFailure
from .publication import GenerationTicket,GenerationExpired
from airport_assistant.modules.agent.answers import KnowledgeAnswerUnavailable,staff_offer


def text_hash(body):return hashlib.sha256(body.encode('utf-8')).hexdigest()


RESTORED_TEMPLATES={'en':'Please add details or request staff assistance.',
    'id':'Mohon tambahkan detail atau minta bantuan petugas.'}


@dataclass(frozen=True)
class PublicationInput:
    tenant_id:UUID
    session_id:UUID
    principal_id:UUID
    run_id:UUID
    conversation_id:UUID
    version:int
    epoch:int
    language:str
    body_hash:str
    requirements:SecurityLabels=field(repr=False)
    checked_at:float=field(repr=False)
    kind:str='live'
    source_fact_hash:str|None=None
    run_revision:int|None=None


class PrivateAIPublication:
    """Internal opt-in capability. All public response shapes remain metadata."""
    def __init__(self,repository,source_access,*,clock=time.monotonic,answers=None):
        self.repository,self.clock,self.answers=repository,clock,answers
        self.gate=PrivateContentGate(source_access,clock=clock)

    def _fresh(self,proof):
        if proof is not None and not 0<=self.clock()-proof.checked_at<2:raise SourceFailure(503)

    @asynccontextmanager
    async def _mutation(self,identity,loader,after_loader,*,requirements=None,proof=None,proof_provider=None,after_grants=None):
        # Include transaction exit's awaited session recheck in the pre-commit
        # freshness boundary. Do not mutate the shared/frozen gate implementation.
        checked=None
        owner=self
        class Access:
            async def allows(self,**kwargs):
                nonlocal checked
                allowed=False if owner.gate.source_access is None else await owner.gate.source_access.allows(**kwargs)
                if allowed is True:
                    # This timestamp belongs to the ACTUAL grants. A later
                    # fact collector must never refresh this older clock.
                    checked=owner.clock()
                    if after_grants is not None:await after_grants(kwargs['labels'])
                    if not 0<=owner.clock()-checked<2:raise SourceFailure(503)
                return allowed
        @asynccontextmanager
        async def transaction(*,before_commit=None):
            if identity.session.kind not in {'customer','anonymous'}:raise AuthError(403,'ACTION_FORBIDDEN')
            try:
                async with owner.repository.transaction(identity.database_context) as tx:
                    await require_customer_session(tx)
                    yield tx
                    await require_customer_session(tx)
                    if checked is not None and not 0<=owner.clock()-checked<2:raise SourceFailure(503)
                    owner._fresh(proof_provider() if proof_provider is not None else proof)
                    run_before_commit(before_commit)
            except CustomerSessionLost:raise AuthError(401,'AUTH_REQUIRED') from None
        gate=PrivateContentGate(Access(),clock=self.clock)
        async with gate.mutation(identity,transaction,loader,additional_labels=requirements,after_loader=after_loader) as tx:
            yield tx
        # The gate's own clock starts after its Access callback. When that
        # callback collects NEW facts, preserve the older grant boundary even
        # across an awaited commit, not just immediately before committing.
        if checked is not None and not 0<=owner.clock()-checked<2:raise SourceFailure(503)
        owner._fresh(proof_provider() if proof_provider is not None else proof)

    @staticmethod
    def _owned(identity,context):
        if type(context) is not dict:raise AuthError(404,'RESOURCE_NOT_FOUND')
        conv=context.get('conversation')
        if (type(conv) is not dict or conv.get('tenant_id')!=identity.session.tenant_id
            or not isinstance(conv.get('id'),UUID)):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        owner=(conv.get('customer_principal_id')==identity.session.principal_id and identity.session.kind=='customer'
            or conv.get('anonymous_session_id')==identity.session.id and identity.session.kind=='anonymous')
        if not owner or conv.get('case_id') is not None and context.get('participant_active') is not True:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        return conv

    @staticmethod
    def _generation(identity,ticket,generation,conversation_id):
        if (type(ticket) is not GenerationTicket or type(generation) is not dict
            or generation.get('id')!=ticket.id or generation.get('tenant_id')!=identity.session.tenant_id
            or generation.get('conversation_id')!=conversation_id
            or generation.get('origin_session_id')!=identity.session.id
            or generation.get('principal_id')!=identity.session.principal_id
            or not isinstance(generation.get('nonce_hash'),str)
            or not hmac.compare_digest(generation['nonce_hash'],text_hash(ticket.nonce))):
            raise AuthError(404,'RESOURCE_NOT_FOUND')

    @staticmethod
    def _graph(generation,graph):
        if type(graph) is not dict or graph.get('generation')!=generation:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        message,event=graph.get('message'),graph.get('event')
        if (type(message) is not dict or type(event) is not dict or generation.get('state')!='published'
            or generation.get('message_id')!=message.get('id') or message.get('generation_id')!=generation.get('id')
            or message.get('tenant_id')!=generation.get('tenant_id') or event.get('tenant_id')!=generation.get('tenant_id')
            or message.get('conversation_id')!=generation.get('conversation_id') or event.get('conversation_id')!=generation.get('conversation_id')
            or message.get('created_session_id')!=generation.get('origin_session_id')
            or message.get('author_type')!='ai' or message.get('source_kind')!='ai_response'
            or message.get('basis_conversation_version')!=generation.get('input_version')
            or message.get('basis_epoch')!=generation.get('generation_epoch')
            or message.get('language')!=generation.get('language') or event.get('language')!=generation.get('language')
            or event.get('event_type')!='ai_message' or event.get('message_id')!=message.get('id')):
            raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        return message

    async def _load(self,tx,identity,ticket,conversation_id=None):
        packet_reader=getattr(tx,'ai_root_publication_packet',None)
        packet=await packet_reader(ticket.id,conversation_id) if packet_reader is not None else None
        generation=packet['generation'] if packet is not None else await tx.ai_generation_context(ticket.id)
        if generation is None and conversation_id is None:return None
        cid=conversation_id if conversation_id is not None else generation['conversation_id']
        context=packet['context'] if packet is not None else await tx.ai_publication_context(cid,ticket.id)
        conv=self._owned(identity,context)
        if conv['id']!=cid:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if context.get('generation')!=generation:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        run=context.get('run')
        if run is not None and (type(run) is not dict or run.get('id')!=ticket.id
            or run.get('tenant_id')!=identity.session.tenant_id or run.get('session_id')!=identity.session.id
            or run.get('principal_id')!=identity.session.principal_id or run.get('conversation_id')!=cid):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        if generation is not None:self._generation(identity,ticket,generation,cid)
        graph=None
        if generation is not None and generation.get('state')=='published':
            graph=packet['graph'] if packet is not None else await tx.ai_publication_result(ticket.id)
            self._graph(generation,graph)
        now=packet['now'] if packet is not None else await tx.now()
        # Q06: context must be read AGAIN after every graph/deadline await.
        # The root packet instead reads all fields in a NEW statement after
        # acquiring locks; no graph/deadline await splits that projection.
        final=context if packet is not None else await tx.ai_publication_context(cid,ticket.id)
        self._owned(identity,final)
        if final!=context:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        groups=[conv.get('resource_security_labels'),context.get('run_labels')]
        parent=context.get('parent')
        if conv.get('case_id') is not None:
            if type(parent) is not dict or parent.get('id')!=conv['case_id']:raise AuthError(404,'RESOURCE_NOT_FOUND')
            groups.append(parent.get('resource_security_labels'))
        if generation is not None:groups.append(generation.get('resource_security_labels'))
        if graph is not None:groups.append(graph['message'].get('resource_security_labels'))
        records=inherited_records(identity.session.tenant_id,*groups)
        return PrivateProjection.capture(identity.session.tenant_id,cid,
            value=dict(context=final,generation=generation,graph=graph,now=now),
            basis=dict(context=final,graph=graph),records=records)

    @staticmethod
    def _pending(value):
        conv=value['context']['conversation'];g=value['generation']
        return (g is not None and g['state']=='pending' and g['expires_at']>value['now'] and conv['mode']=='ai'
            and conv['case_id'] is None and (conv['version'],conv['generation_epoch'],conv['language'])
                ==(g['input_version'],g['generation_epoch'],g['language']))

    @staticmethod
    def _receipt(value):
        generation=value['generation']
        if generation is None:return None
        if generation['state']=='published':
            message=value['graph']['message']
            return dict(state='published',message_id=message['id'],sequence=message['sequence'])
        if generation['state']=='stale':return dict(state='discarded',reason='STALE_GENERATION')
        return None

    async def capture(self,identity,conversation_id,*,requirements=None):
        ticket=GenerationTicket(uuid4(),secrets.token_urlsafe(32))
        return await self.capture_bound(identity,conversation_id,ticket,None,None,requirements=requirements)

    async def capture_bound(self,identity,conversation_id,ticket,version,epoch,*,requirements=None):
        current=None;created=False;original_context=None
        async def load(tx):
            nonlocal current
            current=await self._load(tx,identity,ticket,conversation_id)
            return current
        async def after(tx):
            result=await load(tx);g=result.value['generation']
            if g is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if created:
                conv=result.value['context']['conversation']
                if (g['state']!='pending' or g['input_version']!=expected_version or g['generation_epoch']!=expected_epoch
                    or g['language']!=conv['language'] or g['expires_at']<=result.value['now']
                    or g['expires_at']>result.value['now']+timedelta(minutes=5)
                    or g['resource_security_labels']!=expected_labels):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                expected=dict(original_context['conversation'],resource_security_labels=expected_labels)
                if conv!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self._mutation(identity,load,after,requirements=requirements) as tx:
            value=current.value;conv=value['context']['conversation'];g=value['generation']
            expected_version=conv['version'] if version is None else version
            expected_epoch=conv['generation_epoch'] if epoch is None else epoch
            if g is not None:
                if (g['input_version'],g['generation_epoch'])!=(expected_version,expected_epoch):raise AuthError(404,'RESOURCE_NOT_FOUND')
            else:
                if conv['mode']!='ai' or conv['case_id'] is not None or (conv['version'],conv['generation_epoch'])!=(expected_version,expected_epoch):
                    raise DraftError(409,'AI_PUBLICATION_BLOCKED')
                expected_labels=inherited_records(identity.session.tenant_id,
                    current.labels.to_records() if current.labels else [],requirements.to_records() if requirements else [])
                original_context=value['context']
                await tx.ai_create_generation(ticket,conv,expected_labels)
                created=True
        return ticket

    async def receipt(self,identity,ticket,*,discard=False):
        current=None;initial=None
        async def load(tx):
            nonlocal current
            current=await self._load(tx,identity,ticket)
            if current is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            return current
        # Even discard=False may expire pending work; use the mutation boundary.
        async def after(tx):
            result=await load(tx)
            if changed and result.value['generation']['state']!='stale':raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if not changed and result.basis_hash!=initial.basis_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if changed:
                expected=dict(initial.value['context'])
                expected['generation']=dict(initial.value['generation'],state='stale')
                if result.value['context']!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        # Missing ticket means no content, but session must still be current.
        async with customer_transaction(self.repository,identity) as tx:
            if await tx.ai_generation_context(ticket.id) is None:return None
        changed=False
        async with self._mutation(identity,load,after) as tx:
            initial=current
            value=current.value;result=self._receipt(value)
            if result is None and (discard or not self._pending(value)):
                await tx.ai_stale_generation(ticket.id);changed=True
                result=dict(state='discarded',reason='STALE_GENERATION')
        return result

    def _proof(self,identity,ticket,body,proof,value):
        if (type(proof) is not PublicationInput or proof.tenant_id!=identity.session.tenant_id
            or proof.session_id!=identity.session.id or proof.principal_id!=identity.session.principal_id
            or proof.run_id!=ticket.id or proof.body_hash!=text_hash(body)
            or proof.kind not in {'live','generic_restore'} or type(proof.requirements) is not SecurityLabels
            or proof.requirements.tenant_id!=identity.session.tenant_id):raise AuthError(404,'RESOURCE_NOT_FOUND')
        conv=value['context']['conversation']
        if (proof.conversation_id,proof.version,proof.epoch,proof.language)!=(conv['id'],conv['version'],conv['generation_epoch'],conv['language']):
            raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        if proof.kind=='live' and not proof.source_fact_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        if proof.kind=='generic_restore' and body!=RESTORED_TEMPLATES.get(proof.language):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        run=value['context'].get('run')
        if (type(run) is not dict or run.get('id')!=proof.run_id or run.get('phase')!='waiting_user'
            or run.get('revision')!=proof.run_revision):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        self._fresh(proof)

    async def publish_prepared(self,identity,ticket,collector,requirements):
        """Internal single-operation collector; never an HTTP-supplied hook."""
        if not callable(collector) or type(requirements) is not SecurityLabels or requirements.tenant_id!=identity.session.tenant_id:
            raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        return await self.publish(identity,ticket,None,_collector=collector,_requirements=requirements)

    async def publish(self,identity,ticket,body,*,private_input=None,grounding=None,_collector=None,_requirements=None):
        if _collector is None:
            if type(body) is not str or not 1<=len(body)<=4000 or not body.strip() or '\0' in body:raise DraftError(422,'VALIDATION_ERROR')
        elif body is not None or private_input is not None or grounding is not None:
            raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        # Public grounding retains its transaction validation. Private booking
        # applicability is a separate gate and cannot piggyback on this path.
        if private_input is not None and type(private_input) is not PublicationInput:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        if grounding is not None and private_input is not None:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        current=None;before=None;initial=None;inserted=False;staled=False
        output_body=body;metadata=None
        async def collect(authorized):
            nonlocal private_input,body,output_body
            from airport_assistant.modules.agent.private_publication import PreparedPublication
            prepared=await _collector()
            if (type(prepared) is not PreparedPublication or type(prepared.proof) is not PublicationInput
                or prepared.proof.requirements!=authorized or prepared.proof.tenant_id!=identity.session.tenant_id
                or type(prepared.body) is not str or not 1<=len(prepared.body)<=4000 or not prepared.body.strip() or '\0' in prepared.body):
                raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            private_input=prepared.proof;body=prepared.body;output_body=body
            self._fresh(private_input)
        async def load(tx):
            nonlocal current
            current=await self._load(tx,identity,ticket)
            if current is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            return current
        async def after(tx):
            result=await load(tx)  # includes actual graph + FINAL context re-read
            if inserted:
                value=result.value;g=value['generation'];message=self._graph(g,value['graph'])
                conv=value['context']['conversation'];old=before['context']['conversation']
                stable={key:val for key,val in before['generation'].items() if key not in {'state','message_id','resource_security_labels'}}
                actual={key:val for key,val in g.items() if key not in {'state','message_id','resource_security_labels'}}
                if (stable!=actual or message['body']!=output_body or message.get('knowledge_evidence')!=metadata or message['sequence']!=old['last_sequence']+1
                    or conv['version']!=old['version']+1 or conv['last_sequence']!=old['last_sequence']+1
                    or conv['last_event_sequence']!=old['last_event_sequence']+1
                    or value['graph']['event']['sequence']!=conv['last_event_sequence']
                    or g['resource_security_labels']!=expected_labels or message['resource_security_labels']!=expected_labels
                    or conv['resource_security_labels']!=expected_labels or g['expires_at']<=value['now']):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                expected_conv=dict(old,version=old['version']+1,last_sequence=old['last_sequence']+1,
                    last_event_sequence=old['last_event_sequence']+1,resource_security_labels=expected_labels)
                expected_context=dict(before['context'],conversation=expected_conv,generation=g)
                if value['context']!=expected_context:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                self._fresh(private_input)
            elif staled and result.value['generation']['state']!='stale':raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            elif not inserted and not staled and result.basis_hash!=initial.basis_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if staled:
                expected=dict(initial.value['context']);expected['generation']=dict(initial.value['generation'],state='stale')
                if result.value['context']!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self._mutation(identity,load,after,
            requirements=_requirements if _collector is not None else (private_input.requirements if private_input else None),
            proof=private_input,proof_provider=(lambda:private_input) if _collector is not None else None,
            after_grants=collect if _collector is not None else None) as tx:
            initial=current
            value=current.value;result=self._receipt(value)
            if current.labels is not None and private_input is None:raise DraftError(409,'PRIVATE_PERSISTENCE_REQUIRED')
            if result is not None:
                if value['generation']['state']=='published' and value['graph']['message']['body']!=body:raise DraftError(409,'MESSAGE_CONFLICT')
            elif not self._pending(value):
                await tx.ai_stale_generation(ticket.id);staled=True
                result=dict(state='discarded',reason='STALE_GENERATION')
            else:
                if private_input is not None:self._proof(identity,ticket,body,private_input,value)
                before=value
                expected_labels=inherited_records(identity.session.tenant_id,current.labels.to_records() if current.labels else [],
                    private_input.requirements.to_records() if private_input else [])
                try:
                    async with tx.savepoint():
                        if grounding is not None:
                            if self.answers is None:raise KnowledgeAnswerUnavailable()
                            metadata=await self.answers.validate_transaction(identity,grounding,tx,value['generation']['language'],body)
                        message=await tx.ai_write_publication(ticket.id,body,expected_labels,knowledge_evidence=metadata)
                    result=dict(state='published',message_id=message['id'],sequence=message['sequence']);inserted=True
                except GenerationExpired:
                    await tx.ai_stale_generation(ticket.id);staled=True
                    result=dict(state='discarded',reason='STALE_GENERATION')
                except KnowledgeAnswerUnavailable:
                    # Only this PUBLIC grounding path can render a safe fallback.
                    # It never retries a private body or weakens source labels.
                    metadata=None;output_body=staff_offer(value['generation']['language'])
                    if value['generation']['expires_at']<=await tx.now():
                        await tx.ai_stale_generation(ticket.id);staled=True
                        result=dict(state='discarded',reason='STALE_GENERATION')
                    else:
                        try:
                            async with tx.savepoint():message=await tx.ai_write_publication(ticket.id,output_body,expected_labels)
                            result=dict(state='published',message_id=message['id'],sequence=message['sequence']);inserted=True
                        except GenerationExpired:
                            await tx.ai_stale_generation(ticket.id);staled=True
                            result=dict(state='discarded',reason='STALE_GENERATION')
        if inserted:self._fresh(private_input)
        return result

    async def citations(self,identity,conversation_id,message_id):
        async def load():
            async with customer_transaction(self.repository,identity) as tx:
                value=await tx.ai_citation_context(conversation_id,message_id)
                if value is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                context=value['context'];conv=self._owned(identity,context);message=value['message']
                if message.get('id')!=message_id or message.get('conversation_id')!=conversation_id or message.get('tenant_id')!=identity.session.tenant_id:
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                if conv.get('case_id') is not None and (type(context.get('parent')) is not dict or context['parent'].get('id')!=conv['case_id']):
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                records=inherited_records(identity.session.tenant_id,conv.get('resource_security_labels'),
                    context.get('run_labels'),message.get('resource_security_labels'),value.get('generation_labels'),
                    *([context['parent'].get('resource_security_labels')] if conv.get('case_id') is not None and context.get('parent') else []))
                final=await tx.ai_publication_context(conversation_id,value.get('generation_id'))
                self._owned(identity,final)
                if final!=context:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return PrivateProjection.capture(identity.session.tenant_id,message_id,value=value,basis=value,records=records)
        value=await self.gate.read(identity,load)
        metadata=value['message'].get('knowledge_evidence')
        return dict(message_id=message_id,status='historical_publication',issued_at=metadata['issued_at'] if metadata else None,
            citations=metadata['citations'] if metadata else [])
