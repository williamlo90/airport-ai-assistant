"""Durable node transitions; checkpoints are metadata, never proof of current access."""
from dataclasses import dataclass,field
from datetime import timedelta,datetime,timezone
import hashlib,json
from typing import Literal
from uuid import UUID,uuid4
from pydantic import Field,ValidationError,model_validator
from airport_assistant.modules.identity.auth import AuthError,normalized_contact
from airport_assistant.modules.drafts.models import DraftError,ReportPayload,require_integrity
from airport_assistant.modules.knowledge.retrieval import Search
from airport_assistant.modules.source.labels import merge_labels
from airport_assistant.modules.source.models import SourceFailure
from .models import Model,AgentError,ToolCall,Decision,DraftProposal


class RunRequest(Model):
    conversation_id:UUID=Field(strict=False)
    message_id:UUID=Field(strict=False)
    request_key:UUID=Field(strict=False)
    mode:Literal['assist','draft','answer']='assist'
    booking_references:tuple[str,...]=Field(default=(),max_length=8,strict=False)
    transaction_references:tuple[str,...]=Field(default=(),max_length=8,strict=False)
    knowledge:Search|None=None
    contact_email:str|None=None
    action_type:Literal['create_case','request_handoff']='create_case'

    @model_validator(mode='after')
    def public_knowledge_only(self):
        if self.mode=='answer' and (self.knowledge is None or self.booking_references or self.transaction_references or self.contact_email is not None):
            raise ValueError('Public answer requires knowledge without private references.')
        # Historical / booking-derived applicability needs the SRC-03B label bridge.
        if self.knowledge and any(getattr(self.knowledge,k) is not None for k in ('policy_reference','terminal','booking_created_at')):
            raise ValueError('Booking-specific knowledge context is not checkpointable yet.')
        return self

    def parameters(self):
        result=self.model_dump(mode='json',exclude={'conversation_id','message_id','request_key','mode'})
        if self.knowledge is not None:
            result['knowledge']=self.knowledge.model_dump(mode='json',include={'service_type','language','document_key','limit'})
        if result['contact_email'] is not None:result['contact_email']=normalized_contact(result['contact_email'])
        return result


@dataclass(frozen=True)
class RunResult:
    run_id:UUID
    status:str
    reason:str
    question:str|None=field(default=None,repr=False)
    draft:object|None=field(default=None,repr=False)
    observations:tuple=field(default=(),repr=False)
    labels:object|None=field(default=None,repr=False)
    grounding:object|None=field(default=None,repr=False)


@dataclass
class Work:
    identity:object=field(repr=False)
    run:dict=field(repr=False)
    scope:object=field(default=None,repr=False)
    result:RunResult|None=None


class AgentWorkflow:
    def __init__(self,foundation,repository,*,private_persistence=False):
        from .checkpoint_access import CheckpointAccess
        self.foundation,self.repository=foundation,repository
        self.checkpoints=CheckpointAccess(self,enabled=private_persistence)
        from .private_bridge import PrivateDraftBridge
        self.private_drafts=PrivateDraftBridge(self)

    @staticmethod
    def knowledge(parameters):
        return Search(query='user_report',as_of=datetime.now(timezone.utc),**parameters) if parameters else None

    async def start(self,identity,request):
        request=RunRequest.model_validate(request.model_dump() if isinstance(request,RunRequest) else request)
        if request.mode=='answer' and self.foundation.evidence is None:raise AgentError(503,'AGENT_KNOWLEDGE_UNAVAILABLE')
        params=request.parameters()
        fingerprint=hashlib.sha256(json.dumps({'conversation_id':str(request.conversation_id),'message_id':str(request.message_id),'mode':request.mode,**params},sort_keys=True).encode()).hexdigest()
        scope=await self.foundation.scope(identity,request.conversation_id,request.message_id,
            booking_references=params['booking_references'],transaction_references=params['transaction_references'],knowledge=self.knowledge(params['knowledge']),include_history=True)
        async with self.checkpoints.start_unit(identity,request) as (tx,conv,old):
            self._matches(conv,scope.conversation_version,scope.generation_epoch)
            if self.foundation._labels(identity,conv)!=scope.labels:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
            if old:
                if old['request_hash']!=fingerprint:raise AgentError(409,'IDEMPOTENCY_CONFLICT')
                return old['id']
            run=await tx.create_run(request,scope,fingerprint,params)
            return run['id']

    @staticmethod
    def _matches(conv,version,epoch):
        if conv.version!=version or conv.generation_epoch!=epoch or conv.mode!='ai' or conv.case_id is not None:
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')

    async def _locked(self,tx,identity,id,*,candidate_only=False):
        cid=await tx.locate_run(id)
        if cid is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        conv=await self.foundation.drafts._conversation(tx,identity,cid,**({'candidate_only':True} if candidate_only else {}))
        run=await tx.run(id)
        if run is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if not candidate_only:await self.checkpoints.public_run(tx,identity,conv,run)
        return conv,run

    async def load(self,identity,id):
        async with self.checkpoints.unit(identity,id) as (tx,conv,run):
            try:self._matches(conv,run['conversation_version'],run['generation_epoch'])
            except AgentError:
                if run['phase'] not in {'done','waiting_user','review','superseded'}:
                    run=await self.checkpoints.update(tx,conv,run,phase='superseded',lease=None,lease_until=None,reason='context_changed')
                return Work(identity,run,result=RunResult(id,'superseded','context_changed'))
            if run['phase'] in {'planning','reading'}:
                if run['lease_until']>await tx.now():raise AgentError(409,'AGENT_RUN_BUSY')
                if run['phase']=='planning':
                    run=await self.checkpoints.update(tx,conv,run,phase='review',lease=None,lease_until=None,reason='model_outcome_unknown')
                else:run=await self.checkpoints.update(tx,conv,run,phase='tool',lease=None,lease_until=None,reason='safe_read_recovery')
        params=run['parameters']
        knowledge=self.knowledge(params['knowledge'])
        scope=await self.foundation.scope(identity,run['conversation_id'],run['message_id'],
            booking_references=params['booking_references'],transaction_references=params['transaction_references'],knowledge=knowledge,include_history=True)
        self._matches(await self.foundation.current(scope),run['conversation_version'],run['generation_epoch'])
        work=Work(identity,run,scope)
        labels=self.checkpoints.result_labels(scope,run)
        if run['phase']=='done':
            draft=await self.foundation.drafts.get(identity,run['result_draft_id'])
            work.result=RunResult(id,'done','draft_already_recorded',draft=draft,
                labels=self.checkpoints.draft_labels(scope,run,draft))
        elif run['phase'] in {'review','superseded','waiting_user'}:
            generic='Mohon tambahkan detail atau minta bantuan petugas.' if run['language']=='id' else 'Please add details or request staff assistance.'
            work.result=RunResult(id,run['phase'],run['reason'],question=generic if run['phase']=='waiting_user' else None,labels=labels)
        return work

    async def _claim(self,work,phase):
        async with self.checkpoints.unit(work.identity,work.run['id']) as (tx,conv,run):
            self._matches(conv,run['conversation_version'],run['generation_epoch'])
            if run['revision']!=work.run['revision']:raise AgentError(409,'AGENT_RUN_BUSY')
            expected='ready' if phase=='planning' else 'tool'
            if run['phase']!=expected:raise AgentError(409,'AGENT_RUN_BUSY')
            field='model_calls' if phase=='planning' else 'read_calls'
            if run[field]>=(3 if phase=='planning' else 12):
                return await self.checkpoints.update(tx,conv,run,phase='review',reason='step_budget_exhausted')
            return await self.checkpoints.update(tx,conv,run,phase=phase,**{field:run[field]+1},lease=uuid4(),lease_until=await tx.now()+timedelta(seconds=120),reason='in_flight')

    async def _finish(self,work,claimed,*,source_labels=None,**changes):
        async with self.checkpoints.unit(work.identity,claimed['id'],additional_labels=source_labels if self.checkpoints.enabled else None) as (tx,conv,run):
            self._matches(conv,run['conversation_version'],run['generation_epoch'])
            if run['revision']!=claimed['revision'] or run['lease']!=claimed['lease'] or run['lease_until']<=await tx.now():
                raise AgentError(409,'AGENT_RUN_BUSY')
            return await self.checkpoints.update(tx,conv,run,additional_labels=source_labels,lease=None,lease_until=None,**changes)

    async def _reserve_refresh(self,work,claimed):
        async with self.checkpoints.unit(work.identity,claimed['id']) as (tx,conv,run):
            self._matches(conv,run['conversation_version'],run['generation_epoch'])
            if run['revision']!=claimed['revision'] or run['lease']!=claimed['lease'] or run['lease_until']<=await tx.now():
                raise AgentError(409,'AGENT_RUN_BUSY')
            if run['read_calls']>=12:raise AgentError(409,'AGENT_STEP_LIMIT')
            claimed.update(await self.checkpoints.update(tx,conv,run,read_calls=run['read_calls']+1))

    async def _refresh(self,work,claimed):
        result=[]
        for saved in work.run['trace']:
            call=ToolCall.model_validate(saved)
            await self._reserve_refresh(work,claimed)
            result.append(await self.foundation.execute_read(work.scope,self.actual_call(work,call)))
        return tuple(result)

    @staticmethod
    def actual_call(work,call):
        return call.model_copy(update={'arguments':{'query':work.scope.report[:1000]}}) if call.name=='search_knowledge' else call

    @staticmethod
    def observation_key(observations):
        values=[]
        for obs in observations:
            if obs.name!='search_knowledge':
                values.append({'name':obs.name,'data':obs.data.model_dump(mode='json',exclude={'observed_at'}),
                    'booking':obs.booking.model_dump(mode='json',exclude={'observed_at'}) if obs.booking is not None else None,
                    'labels':obs.labels.to_records()})
            else:
                values.append({'name':obs.name,'status':obs.data.status,
                    'scope':obs.data.snapshot.scope_token if obs.data.snapshot else None,
                    'chunks':[{'body':c.body,'uri':c.source_uri,'citation':c.citation.model_dump(mode='json',exclude={'score'})} for c in obs.data.chunks]})
        return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _wire(observations):
        result=[]
        for obs in observations:
            if obs.name!='search_knowledge':payload=obs.data.model_dump(mode='json')
            else:
                payload={'status':obs.data.status,'sources':[{'body':c.body,'source_uri':c.source_uri} for c in obs.data.chunks]}
            result.append({'name':obs.name,'authority':'untrusted_tool_data','data':payload})
        return tuple(result)

    @staticmethod
    def report(scope):
        report='\n'.join(scope.memory) if scope.memory else scope.report
        if len(report)>4000:raise AgentError(413,'AGENT_CONTEXT_LIMIT')
        return report

    async def plan(self,work):
        claimed=await self._claim(work,'planning')
        if claimed['phase']=='review':return None
        try:
            await self.foundation.current(work.scope)
            if work.run['mode']=='answer':return await self.answer(work,claimed)
            if work.run['mode']=='draft':
                if self.private_drafts.applies(work):
                    return await self.private_drafts.plan(work,claimed)
                decision=await self.foundation.model.decide(self.report(work.scope),work.scope.language,(),draft=True)
                decision=Decision.model_validate(decision.model_dump())
                if decision.kind!='draft':raise AgentError(502,'AGENT_INVALID_RESPONSE')
                await self.foundation.current(work.scope)
                await self._finish(work,claimed,phase='draft',proposal=decision.proposal.model_dump(),reason='proposal_ready')
                return None
            observations=await self._refresh(work,claimed)
            await self.foundation.current(work.scope)
            if observations:
                decision=await self.foundation.model.advance(work.scope.report,work.scope.language,self.foundation.tools(work.scope),
                    memory=work.scope.memory,observations=self._wire(observations))
            else:
                decision=await self.foundation.model.advance(work.scope.report,work.scope.language,self.foundation.tools(work.scope),memory=work.scope.memory,observations=())
            decision=Decision.model_validate(decision.model_dump())
            await self.foundation.current(work.scope)
            fresh=await self._refresh(work,claimed)
            if self.observation_key(fresh)!=self.observation_key(observations):
                await self._finish(work,claimed,phase='review',reason='source_context_changed')
                return None
            observations=fresh
            if decision.kind=='tool':
                call=decision.tool
                if call.name=='search_knowledge':call=call.model_copy(update={'arguments':{'query':'user_report'}})
                self.foundation.validate_call(work.scope,call)
                if len(work.run['trace'])>=3:
                    await self._finish(work,claimed,phase='review',reason='tool_budget_exhausted')
                else:await self._finish(work,claimed,phase='tool',pending_call=call.model_dump(),reason='read_requested')
                return None
            if decision.kind!='clarify':raise AgentError(502,'AGENT_INVALID_RESPONSE')
            # Recheck grants/source state after the model await, not from cached DTOs.
            bundles=[bundle for bundle in (self.checkpoints.result_labels(work.scope,work.run),*(o.labels for o in observations)) if bundle is not None]
            labels=merge_labels(*bundles) if bundles else None
            await self._finish(work,claimed,phase='waiting_user',reason='clarification_required')
            await self.foundation.current(work.scope)
            return RunResult(claimed['id'],'waiting_user','clarification_required',question=decision.clarification.question,observations=observations,labels=labels)
        except (AgentError,AuthError,DraftError,SourceFailure,ValidationError):
            # Best-effort is not a retry: unknown model work stays in-flight if this
            # metadata write fails, and resume will conservatively require review.
            try:await self._finish(work,claimed,phase='review',reason='planning_failed')
            except (AgentError,AuthError,DraftError):pass
            raise

    async def answer(self,work,claimed):
        from .answers import PublicAnswers,AnswerSelection,Grounding,staff_offer
        answers=PublicAnswers(self.foundation.evidence)
        selection=work.scope.knowledge.model_copy(update={'query':work.scope.report[:1000]})
        answers.public(selection,work.scope.language)
        async def fallback(reason):
            await self.foundation.current(work.scope)
            await self._finish(work,claimed,phase='waiting_user',reason='answer_'+reason)
            await self.foundation.current(work.scope)
            return RunResult(claimed['id'],'waiting_user',reason,question=staff_offer(work.scope.language),labels=self.checkpoints.result_labels(work.scope,work.run))
        await self._reserve_refresh(work,claimed)
        initial=await self.foundation.evidence.collect(work.identity,selection)
        if reason:=answers.usable(initial):return await fallback(reason)
        try:
            await self.foundation.current(work.scope)
            decision=AnswerSelection.model_validate(await self.foundation.model.answer(work.scope.report,work.scope.language,
                answers.candidates(initial),memory=work.scope.memory))
        except AgentError:return await fallback('model_unavailable')
        except ValidationError:return await fallback('invalid_answer')
        await self.foundation.current(work.scope)
        if decision.outcome!='answer':return await fallback(decision.outcome)
        await self._reserve_refresh(work,claimed)
        current=await self.foundation.evidence.consume(work.identity,selection,initial.snapshot)
        await self.foundation.current(work.scope)
        if reason:=answers.usable(current):return await fallback(reason)
        try:body=answers.compiled(work.scope.language,current,decision.source_ids)
        except AgentError:return await fallback('invalid_citation')
        await self._finish(work,claimed,phase='waiting_user',reason='grounded_answer_ready')
        await self.foundation.current(work.scope)
        return RunResult(claimed['id'],'answer','grounded_answer_ready',question=body,
            grounding=Grounding(selection,current.snapshot,decision.source_ids),labels=self.checkpoints.result_labels(work.scope,work.run))

    async def read(self,work):
        claimed=await self._claim(work,'reading')
        if claimed['phase']=='review':return
        try:
            call=ToolCall.model_validate(claimed['pending_call'])
            observation=await self.foundation.execute_read(work.scope,self.actual_call(work,call))
            await self._finish(work,claimed,source_labels=observation.labels,phase='ready',trace=[*claimed['trace'],call.model_dump()],pending_call=None,reason='read_checkpointed')
        except (AgentError,AuthError,DraftError,SourceFailure,ValidationError):
            try:await self._finish(work,claimed,phase='review',reason='read_failed')
            except (AgentError,AuthError,DraftError):pass
            raise

    async def draft(self,work):
        if self.private_drafts.applies(work):return await self.private_drafts.draft(work)
        async with self.checkpoints.unit(work.identity,work.run['id']) as (tx,conv,run):
            self._matches(conv,run['conversation_version'],run['generation_epoch'])
            if run['phase']=='done':
                draft=await tx.find_draft(run['result_draft_id'])
                if draft is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                require_integrity(draft)
                return RunResult(run['id'],'done','draft_already_recorded',draft=draft,
                    labels=self.checkpoints.draft_labels(work.scope,run,draft))
            if run['revision']!=work.run['revision'] or run['phase']!='draft':raise AgentError(409,'AGENT_RUN_BUSY')
            p=DraftProposal.model_validate(run['proposal']);params=run['parameters']
            scope=work.scope
            payload=ReportPayload(category=p.category,service_type=p.service_type,summary=p.summary,customer_report=self.report(scope),
                booking_reference=next(iter(scope.booking_references)) if len(scope.booking_references)==1 else None,
                transaction_reference=next(iter(scope.transaction_references)) if len(scope.transaction_references)==1 else None,contact_email=params['contact_email'])
            draft=await self.foundation.drafts.create_in_transaction(tx,work.identity,run['conversation_id'],params['action_type'],payload,
                expected_conversation_version=run['conversation_version'],expected_generation_epoch=run['generation_epoch'])
            await self.checkpoints.update(tx,conv,run,phase='done',result_draft_id=draft.id,reason='draft_recorded')
        return RunResult(run['id'],'done','draft_recorded',draft=draft,
            labels=self.checkpoints.draft_labels(work.scope,run,draft))
