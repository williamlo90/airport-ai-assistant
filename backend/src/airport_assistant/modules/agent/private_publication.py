"""Build an operation-bound private publication input; never a checkpoint DTO.

Only current server run/scope and typed observations can supply source refs.
Recovery without live observations uses a closed server template, not old text.
"""
import time
from dataclasses import dataclass,field
from uuid import uuid4
from .models import AgentError,ToolCall,ToolObservation
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import Booking,Transaction,Policy,SourceFailure
from airport_assistant.modules.source.evidence import ObservedSource,SourceEvidence
from airport_assistant.modules.source.labels import SecurityLabels,merge_labels
from airport_assistant.modules.conversations.ai_publication import PublicationInput,text_hash,RESTORED_TEMPLATES


@dataclass(frozen=True)
class PreparedPublication:
    body:str=field(repr=False)
    proof:PublicationInput=field(repr=False)


@dataclass(frozen=True)
class ReservedPublication:
    token:object
    run_id:object
    scope:object=field(repr=False)
    revision:int
    requirements:SecurityLabels=field(repr=False)
    body:str=field(repr=False)
    fact_hash:str|None=field(repr=False)
    calls:tuple=field(repr=False)


class PrivatePublicationPreparation:
    def __init__(self,engine,*,clock=time.monotonic):
        self.engine,self.clock=engine,clock
        self._reserved={}

    async def reserve(self,identity,run_id,result):
        """Commit the bounded read reservation before publication preflight.

        This does not mint a fact proof or preserve any new source DTO. The
        resulting collector is single-use, bound to this request and revision.
        """
        if not self.engine.checkpoints.enabled:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        if result.run_id!=run_id or result.status!='waiting_user' or result.grounding is not None:
            raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        work=await self.engine.load(identity,run_id);scope=work.scope
        if work.run['phase']!='waiting_user' or scope is None:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        bundles=[]
        current=self.engine.checkpoints.result_labels(scope,work.run)
        if current is not None:bundles.append(current)
        if result.labels is not None:
            if type(result.labels) is not SecurityLabels or result.labels.tenant_id!=identity.session.tenant_id:
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            bundles.append(result.labels)
        original,calls=self.evidence(identity.session.tenant_id,result.observations)
        if original is None:body=RESTORED_TEMPLATES[scope.language]
        else:
            body=result.question
            if type(body) is not str or not body.strip():raise AgentError(502,'AGENT_INVALID_RESPONSE')
            bundles.append(original.labels)
        if not bundles:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        for call in calls:
            self.engine.foundation.validate_call(scope,call)
            async with self.engine.checkpoints.unit(identity,run_id) as (tx,conv,run):
                self.engine._matches(conv,run['conversation_version'],run['generation_epoch'])
                if run['phase']!='waiting_user' or run['revision']!=work.run['revision']:
                    raise AgentError(409,'AGENT_RUN_BUSY')
                if run['read_calls']>=12:raise AgentError(409,'AGENT_STEP_LIMIT')
                updated=await self.engine.checkpoints.update(tx,conv,run,read_calls=run['read_calls']+1)
            work.run=updated
        plan=ReservedPublication(uuid4(),run_id,scope,work.run['revision'],merge_labels(*bundles),body,
            original.fact_hash if original is not None else None,calls)
        self._reserved[plan.token]=plan
        return plan

    async def collect(self,identity,plan):
        """NEW typed GETs, outside publisher SQL locks, after current grants.

        Durable context is checked by publisher preflight and its final write
        projection. Repeating those SQL loads between each GET would consume
        the operation's freshness window. No previous fact timestamp is reused.
        """
        if type(plan) is not ReservedPublication or self._reserved.pop(plan.token,None) is not plan:
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        scope=plan.scope
        if (identity.session.tenant_id,identity.session.id,identity.session.principal_id)!=(
                scope.identity.session.tenant_id,scope.identity.session.id,scope.identity.session.principal_id):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        observations=[]
        for call in plan.calls:
            self.engine.foundation.validate_call(scope,call)
            reference=call.arguments['reference'];booking=None
            if call.name=='read_transaction':
                value=await self.engine.foundation.source.transaction(identity,reference)
                if type(value) is not Transaction or value.reference!=reference:raise AgentError(502,'AGENT_INVALID_RESPONSE')
            else:
                booking=await self.engine.foundation.source.booking(identity,reference)
                if type(booking) is not Booking or booking.reference!=reference:raise AgentError(502,'AGENT_INVALID_RESPONSE')
                if call.name=='read_booking_policy':
                    value=await self.engine.foundation.source.policy(identity,booking.policy_reference)
                    if type(value) is not Policy or value.reference!=booking.policy_reference or value.service_type!=booking.service_type:
                        raise AgentError(502,'AGENT_INVALID_RESPONSE')
                else:value=booking;booking=None
            observations.append(ObservedSource(value,booking))
        if observations:
            try:latest=SourceEvidence.capture(identity.session.tenant_id,tuple(observations))
            except (ValueError,TypeError):raise AgentError(409,'AGENT_CONTEXT_CHANGED') from None
            if latest.fact_hash!=plan.fact_hash or merge_labels(plan.requirements,latest.labels).to_records()!=plan.requirements.to_records():
                raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        elif plan.fact_hash is not None or plan.body!=RESTORED_TEMPLATES[scope.language]:
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        checked=self.clock()  # NEW collection complete; no SQL await follows.
        proof=PublicationInput(identity.session.tenant_id,identity.session.id,identity.session.principal_id,
            plan.run_id,scope.conversation_id,scope.conversation_version,scope.generation_epoch,scope.language,
            text_hash(plan.body),plan.requirements,checked,'live' if observations else 'generic_restore',
            plan.fact_hash,plan.revision)
        return PreparedPublication(plan.body,proof)

    @staticmethod
    def evidence(tenant_id,observations):
        typed=[];calls=[]
        for obs in observations:
            if type(obs) is not ToolObservation:raise AgentError(502,'AGENT_INVALID_RESPONSE')
            if obs.name=='search_knowledge':continue
            expected={'read_booking':Booking,'read_transaction':Transaction,'read_booking_policy':Policy}.get(obs.name)
            if expected is None or type(obs.data) is not expected:raise AgentError(502,'AGENT_INVALID_RESPONSE')
            if obs.name=='read_booking_policy':
                if type(obs.booking) is not Booking:raise AgentError(502,'AGENT_INVALID_RESPONSE')
                reference=obs.booking.reference
            else:reference=obs.data.reference
            typed.append(ObservedSource(obs.data,obs.booking))
            calls.append(ToolCall(call_id='publication_source',name=obs.name,arguments={'reference':reference}))
        if not typed:return None,()
        try:return SourceEvidence.capture(tenant_id,tuple(typed)),tuple(calls)
        except (ValueError,TypeError):raise AgentError(409,'AGENT_CONTEXT_CHANGED') from None

    async def prepare(self,identity,run_id,result):
        if not self.engine.checkpoints.enabled:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        if result.run_id!=run_id or result.status!='waiting_user' or result.grounding is not None:
            raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        work=await self.engine.load(identity,run_id)
        scope=work.scope
        if work.run['phase']!='waiting_user' or scope is None:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        bundles=[]
        current=self.engine.checkpoints.result_labels(scope,work.run)
        if current is not None:bundles.append(current)
        if result.labels is not None:
            if type(result.labels) is not SecurityLabels or result.labels.tenant_id!=identity.session.tenant_id:
                raise AuthError(404,'RESOURCE_NOT_FOUND')
            bundles.append(result.labels)
        original,calls=self.evidence(identity.session.tenant_id,result.observations)
        kind='generic_restore';fact_hash=None
        if original is None:
            # Ignore any supplied question when no live source proof exists.
            # This is a server-owned recovery body, still fully source labelled.
            body=RESTORED_TEMPLATES.get(scope.language)
            if body is None:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
            await self.engine.foundation.current(scope)
        else:
            body=result.question
            if type(body) is not str or not body.strip() or not 1<=len(body)<=4000:raise AgentError(502,'AGENT_INVALID_RESPONSE')
            fresh=[]
            for call in calls:
                self.engine.foundation.validate_call(scope,call)
                async with self.engine.checkpoints.unit(identity,run_id) as (tx,conv,run):
                    self.engine._matches(conv,run['conversation_version'],run['generation_epoch'])
                    if run['phase']!='waiting_user' or run['revision']!=work.run['revision']:
                        raise AgentError(409,'AGENT_RUN_BUSY')
                    if run['read_calls']>=12:raise AgentError(409,'AGENT_STEP_LIMIT')
                    updated=await self.engine.checkpoints.update(tx,conv,run,read_calls=run['read_calls']+1)
                work.run=updated
                fresh.append(await self.engine.foundation.execute_read(scope,call))
            latest,_=self.evidence(identity.session.tenant_id,tuple(fresh))
            if latest is None or latest.fact_hash!=original.fact_hash:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
            bundles.extend((original.labels,latest.labels))
            fact_hash=latest.fact_hash;kind='live'
            await self.engine.foundation.current(scope)
        checked=self.clock()  # collection_complete, not oldest DTO time
        if not bundles:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        labels=merge_labels(*bundles)
        # Recheck the exact durable run after all remote reads, outside provider IO.
        async with self.engine.checkpoints.unit(identity,run_id) as (tx,conv,run):
            self.engine._matches(conv,run['conversation_version'],run['generation_epoch'])
            if run['phase']!='waiting_user' or run['revision']!=work.run['revision']:raise AgentError(409,'AGENT_RUN_BUSY')
        if not 0<=self.clock()-checked<2:raise SourceFailure(503)
        return PreparedPublication(body,PublicationInput(identity.session.tenant_id,identity.session.id,
            identity.session.principal_id,run_id,scope.conversation_id,scope.conversation_version,scope.generation_epoch,
            scope.language,text_hash(body),labels,checked,kind,fact_hash,work.run['revision']))
