"""Opt-in staff translation storage/review; recipient grants belong to send gate."""
import copy,hashlib,json,time
from datetime import timedelta
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection,inherited_records
from .models import AgentError


class PrivateStaffTranslations:
    def __init__(self,owner,*,clock=time.monotonic):
        self.owner,self.clock=owner,clock
        self.gate=PrivateContentGate(clock=clock)

    @staticmethod
    def _row(identity,case_id,cid,row):
        if (type(row) is not dict or row.get('tenant_id')!=identity.session.tenant_id
            or row.get('principal_id')!=identity.session.principal_id or row.get('session_id')!=identity.session.id
            or row.get('case_id')!=case_id or row.get('conversation_id')!=cid):raise AuthError(404,'RESOURCE_NOT_FOUND')

    async def snapshot(self,tx,identity,case_id,*,request=None,id=None):
        case=await tx.find_case(case_id,lock=True)
        if case is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        discovered=await tx.translation(id) if id is not None else None
        cid=request.conversation_id if request is not None else discovered.get('conversation_id') if discovered else None
        if cid is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        chat=await tx.chat(cid,lock=True)
        if chat is None or chat.get('case_id')!=case_id:raise AuthError(404,'RESOURCE_NOT_FOUND')
        context=await tx.translation_source_context(case_id,cid)
        if (type(context) is not dict or context.get('tenant_id')!=identity.session.tenant_id
            or context.get('case_id')!=case_id or context.get('conversation_id')!=cid):raise AuthError(404,'RESOURCE_NOT_FOUND')
        row=await tx.translation(id,lock=True) if id is not None else await tx.translation_request(request.request_key)
        if row is not None:self._row(identity,case_id,cid,row)
        now=await tx.now();checked=self.clock()
        final=await tx.translation_source_context(case_id,cid)
        if final!=context:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        records=inherited_records(identity.session.tenant_id,context.get('case_labels'),context.get('conversation_labels'),
            *([row.get('resource_security_labels')] if row is not None else []))
        root_records=inherited_records(identity.session.tenant_id,context.get('case_labels'),context.get('conversation_labels'))
        source_hash=PrivateProjection.capture(identity.session.tenant_id,cid,value=None,basis=context,records=root_records).basis_hash
        return PrivateProjection.capture(identity.session.tenant_id,cid,
            value=dict(row=copy.deepcopy(row),context=copy.deepcopy(final),now=now,checked=checked,source_hash=source_hash),
            basis=dict(row=row,context=final),records=records)

    def factory(self,identity):
        return lambda *,before_commit:lifecycle_transaction(self.owner.repository,identity,staff=True,before_commit=before_commit)

    def deadline(self,value,expires,*,committed=False):
        if not 0<=self.clock()-value['checked']<(expires-value['now']).total_seconds():
            if committed:raise SourceFailure(503)
            raise AgentError(409,'AGENT_TRANSLATION_EXPIRED')

    async def get(self,identity,case_id,id):
        async def load():
            async with lifecycle_transaction(self.owner.repository,identity,staff=True) as tx:
                return await self.snapshot(tx,identity,case_id,id=id)
        value=await self.gate.read(identity,load,purpose='staff')
        if value['row'] is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return value['row']

    async def prepare(self,identity,case_id,request):
        from .translation import TranslationText,preserve_values
        digest=hashlib.sha256(json.dumps(request.model_dump(mode='json',exclude={'request_key'}),sort_keys=True).encode()).hexdigest()
        current=None;created=None
        async def load(tx):
            nonlocal current
            await self.owner.messages.target(tx,identity,case_id,request)
            if request.target_language not in await tx.languages():raise DraftError(422,'VALIDATION_ERROR')
            current=await self.snapshot(tx,identity,case_id,request=request)
            return current
        async def after(tx):
            if created is None:
                result=await load(tx)
                if result.basis_hash!=initial.basis_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return result
            result=await self.snapshot(tx,identity,case_id,id=created['id'])
            if result.value['row']!=created or result.value['context']!=initial.value['context']:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self.gate.mutation(identity,self.factory(identity),load,purpose='staff',after_loader=after) as tx:
            initial=current;value=current.value;previous=value['row']
            if previous is not None:
                if previous['request_hash']!=digest:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
                if previous['state']=='generating':raise AgentError(409,'AGENT_TRANSLATION_IN_FLIGHT')
                response=previous
            else:
                created=await tx.create_translation(case_id,request,digest,requirements=current.labels,source_context_hash=value['source_hash'])
                self._row(identity,case_id,request.conversation_id,created)
                required=current.labels.to_records() if current.labels else []
                expected=dict(request_key=request.request_key,request_hash=digest,original_body=request.body,
                    source_language=request.language,target_language=request.target_language,channel=request.channel,
                    case_version=request.expected_version,conversation_version=request.expected_conversation_version,generation_epoch=request.expected_epoch,
                    state='generating',revision=1,resource_security_labels=required,source_context_hash=value['source_hash'],
                    translated_body=None,reviewed_body=None,message_id=None,send_key=None)
                if any(created.get(k)!=v for k,v in expected.items()):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                if not created['created_at']<created['expires_at']<=created['created_at']+timedelta(minutes=16):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                created=copy.deepcopy(created);response=created
        if created is None:return response
        try:
            # Single external call, outside reservation/write transactions.
            translated=TranslationText(text=await self.owner.model.translate(request.body,request.language,request.target_language)).text
            preserve_values(request.body,translated)
            return await self.finish(identity,case_id,created,translated)
        except Exception:
            try:await self.fail(identity,case_id,created['id'])
            except (AuthError,DraftError):pass
            raise

    async def finish(self,identity,case_id,reserved,translated):
        current=None;expected=None;deadline_value=None
        async def load(tx):
            nonlocal current,deadline_value
            await self.owner.messages.target(tx,identity,case_id,self.owner.command(reserved))
            current=await self.snapshot(tx,identity,case_id,id=reserved['id']);value=current.value
            if value['row']['source_context_hash']!=value['source_hash']:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if reserved['created_at']+timedelta(seconds=120)<=value['now']:raise AgentError(409,'AGENT_TRANSLATION_EXPIRED')
            deadline_value=value
            return current
        def check():
            if deadline_value is not None:self.deadline(deadline_value,reserved['created_at']+timedelta(seconds=120),committed=True)
        async def after(tx):
            result=await self.snapshot(tx,identity,case_id,id=reserved['id'])
            if result.value['row']!=expected or result.value['source_hash']!=reserved['source_context_hash']:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self.gate.mutation(identity,self.factory(identity),load,purpose='staff',after_loader=after,before_commit=check) as tx:
            row=current.value['row']
            if row!=reserved or row['state']!='generating':raise DraftError(409,'VERSION_CONFLICT')
            check();expected=dict(row,state='ready',translated_body=translated,revision=row['revision']+1)
            await tx.finish_translation(row,translated)
        check()
        return expected

    async def fail(self,identity,case_id,id):
        current=None;expected=None
        async def load(tx):
            nonlocal current
            current=await self.snapshot(tx,identity,case_id,id=id)
            if current.value['row'] is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            return current
        async def after(tx):
            result=await load(tx)
            if result.value['row']!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return result
        async with self.gate.mutation(identity,self.factory(identity),load,purpose='staff',after_loader=after) as tx:
            row=current.value['row'];expected=row
            if row['state']=='generating':
                expected=dict(row,state='failed',revision=row['revision']+1)
                await tx.fail_translation(row)

    async def approve(self,identity,case_id,id,approval,key):
        from .translation import TranslationApproval
        if type(approval) is not TranslationApproval or approval.approved is not True:raise DraftError(422,'VALIDATION_ERROR')
        if getattr(self.owner.messages,'_disclosure',None) is None:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        row=await self.get(identity,case_id,id)
        command=self.owner.command(row,translated=True,body=approval.text)
        digest=hashlib.sha256(json.dumps({'case_id':str(case_id),**command.model_dump(mode='json')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        participant=TranslationApprovalParticipant(self,identity,case_id,id,approval,key)
        result=await self.owner.messages._guarded_send(identity,case_id,command,key,digest,participant=participant)
        participant.before_commit(committed=True)
        return result


class TranslationApprovalParticipant:
    """Internal transaction participant; no network/model/authorization bypass."""
    def __init__(self,service,identity,case_id,id,approval,key):
        self.service,self.identity,self.case_id,self.id=service,identity,case_id,id
        self.approval,self.key=approval,key
        self.pending=None

    def before_commit(self,*,committed=False):
        if self.pending is not None:self.service.deadline(self.pending,self.pending['row']['expires_at'],committed=committed)

    async def load(self,tx,previous):
        projection=await self.service.snapshot(tx,self.identity,self.case_id,id=self.id)
        value=projection.value;row=value['row']
        if row is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if row['state']=='sent':
            if (row['reviewed_body']!=self.approval.text or row['send_key']!=self.key
                or previous is None or previous['id']!=row['message_id']):raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            self.pending=None
        else:
            if previous is not None:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
            if row['state']!='ready' or row['revision']!=self.approval.expected_revision:raise DraftError(409,'VERSION_CONFLICT')
            if row['source_context_hash']!=value['source_hash']:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            self.pending=value;self.before_commit()
        return dict(row=row,context=value['context'],resource_security_labels=projection.labels.to_records() if projection.labels else [])

    async def commit(self,tx,snapshot,result):
        await tx.sent_translation(snapshot['row'],self.approval.text,result['id'],self.key)

    async def verify(self,tx,snapshot,result):
        event=await tx.translation_message_event(result['id'])
        projection=await self.service.snapshot(tx,self.identity,self.case_id,id=self.id)
        before=snapshot['row'];expected=dict(before,state='sent',reviewed_body=self.approval.text,message_id=result['id'],
            send_key=self.key,revision=before['revision']+1)
        context=dict(snapshot['context'])
        context['conversation_version']+=1;context['last_sequence']+=1;context['last_event_sequence']+=1
        expected_event=dict(tenant_id=self.identity.session.tenant_id,conversation_id=before['conversation_id'],message_id=result['id'],
            event_type='staff_message',language=before['target_language'],sequence=context['last_event_sequence'],last_event_sequence=context['last_event_sequence'])
        if projection.value['row']!=expected or projection.value['context']!=context or event!=expected_event:
            raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        self.before_commit()
