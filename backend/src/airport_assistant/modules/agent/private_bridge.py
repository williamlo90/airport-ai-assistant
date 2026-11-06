"""Opt-in typed-source proposal bridge; metadata checkpoints, atomic draft/CAS.

Source reads and ALL grants happen outside DB locks. The proposal remains a
summary of the customer's report; the server appends exact source review facts.
"""
import time
from .models import AgentError,Decision,DraftProposal,ToolCall
from airport_assistant.modules.drafts.models import DraftError,ReportPayload,require_integrity
from airport_assistant.modules.source.evidence import SourceEvidence,ObservedSource
from airport_assistant.modules.source.labels import merge_labels
from airport_assistant.modules.source.models import SourceFailure


class PrivateDraftBridge:
    def __init__(self,engine):
        self.engine=engine
        self.clock=time.monotonic

    def applies(self,work):
        return self.engine.checkpoints.enabled and (work.scope.labels is not None
            or bool(work.scope.booking_references or work.scope.transaction_references)
            or bool(work.run.get('resource_security_labels')))

    async def collect(self,work,reserve):
        # A report supports one booking/payment pair. Ambiguity must be reviewed,
        # never silently trimmed to whichever reference happens to sort first.
        scope=work.scope
        if len(scope.booking_references)>1 or len(scope.transaction_references)>1:
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        calls=[('read_booking',r) for r in sorted(scope.booking_references)]
        calls += [('read_booking_policy',r) for r in sorted(scope.booking_references)]
        calls += [('read_transaction',r) for r in sorted(scope.transaction_references)]
        if not calls:raise AgentError(409,'PRIVATE_PERSISTENCE_REQUIRED')
        observations=[]
        for name,reference in calls:
            await reserve()
            observation=await self.engine.foundation.execute_read(scope,ToolCall(
                call_id='draft_source',name=name,arguments={'reference':reference}))
            observations.append(ObservedSource(observation.data,observation.booking))
        try:evidence=SourceEvidence.capture(work.identity.session.tenant_id,tuple(observations))
        except (ValueError,TypeError):raise AgentError(409,'AGENT_CONTEXT_CHANGED') from None
        return evidence,self.clock()

    def fresh(self,checked):
        if not 0<=self.clock()-checked<2:raise SourceFailure(503)

    async def plan(self,work,claimed):
        reserve=lambda:self.engine._reserve_refresh(work,claimed)
        before,_=await self.collect(work,reserve)
        decision=Decision.model_validate(await self.engine.foundation.model.decide(
            self.engine.report(work.scope),work.scope.language,(),draft=True))
        if decision.kind!='draft':raise AgentError(502,'AGENT_INVALID_RESPONSE')
        await self.engine.foundation.current(work.scope)
        evidence,checked=await self.collect(work,reserve)
        if evidence.fact_hash!=before.fact_hash:
            await self.engine._finish(work,claimed,phase='review',reason='source_context_changed')
            return
        self.fresh(checked)
        # No raw source records, timestamps or private source text checkpointed.
        await self.engine._finish(work,claimed,source_labels=evidence.labels,phase='draft',
            proposal={'draft':decision.proposal.model_dump(),'source_fact_hash':evidence.fact_hash},reason='proposal_ready')

    async def draft(self,work):
        from .workflow import RunResult
        engine=self.engine
        # A concurrent success is recovered through load/get, not another insert.
        if work.run['phase']=='done':
            loaded=await engine.load(work.identity,work.run['id'])
            return loaded.result
        proposal=work.run.get('proposal')
        if (type(proposal) is not dict or set(proposal)!={'draft','source_fact_hash'}
            or type(proposal['source_fact_hash']) is not str or len(proposal['source_fact_hash'])!=64):
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        p=DraftProposal.model_validate(proposal['draft'])
        async def reserve():
            async with engine.checkpoints.unit(work.identity,work.run['id']) as (tx,conv,run):
                engine._matches(conv,run['conversation_version'],run['generation_epoch'])
                if run['phase']!='draft' or run['revision']!=work.run['revision']:
                    raise AgentError(409,'AGENT_RUN_BUSY')
                if run['read_calls']>=12:raise AgentError(409,'AGENT_STEP_LIMIT')
                updated=await engine.checkpoints.update(tx,conv,run,read_calls=run['read_calls']+1)
            work.run=updated
        try:evidence,checked=await self.collect(work,reserve)
        except AgentError as error:
            if error.code!='AGENT_RUN_BUSY':raise
            loaded=await engine.load(work.identity,work.run['id'])
            if loaded.run['phase']=='done':return loaded.result
            raise
        if evidence.fact_hash!=proposal['source_fact_hash']:
            async with engine.checkpoints.unit(work.identity,work.run['id']) as (tx,conv,run):
                if run['revision']!=work.run['revision']:raise AgentError(409,'AGENT_RUN_BUSY')
                await engine.checkpoints.update(tx,conv,run,phase='review',reason='source_context_changed')
            return RunResult(work.run['id'],'review','source_context_changed')
        requirements=merge_labels(evidence.labels,engine.checkpoints.result_labels(work.scope,work.run))
        result=None
        async def verify(tx):
            self.fresh(checked)
            current=await tx.run(work.run['id'])
            if (current is None or current['phase']!='done' or current['result_draft_id']!=result.id
                or current['proposal']!=proposal):raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            stored=await tx.find_draft(result.id)
            if stored is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            require_integrity(stored)
            if stored!=result:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            # The last repository await may consume the source freshness bound.
            # Reject inside the write unit, before it can commit an expired basis.
            self.fresh(checked)
        async with engine.checkpoints.unit(work.identity,work.run['id'],additional_labels=requirements,after_check=verify,
                before_commit=lambda:self.fresh(checked)) as (tx,conv,run):
            engine._matches(conv,run['conversation_version'],run['generation_epoch'])
            if run['revision']!=work.run['revision'] or run['phase']!='draft':raise AgentError(409,'AGENT_RUN_BUSY')
            self.fresh(checked)
            scope=work.scope
            payload=ReportPayload(category=p.category,service_type=p.service_type,summary=p.summary,
                customer_report=engine.report(scope),booking_reference=next(iter(scope.booking_references),None),
                transaction_reference=next(iter(scope.transaction_references),None),contact_email=run['parameters']['contact_email'])
            result=await engine.foundation.drafts.create_source_in_transaction(tx,work.identity,run['conversation_id'],
                run['parameters']['action_type'],payload,source_evidence=evidence,requirements=requirements,
                expected_conversation_version=run['conversation_version'],expected_generation_epoch=run['generation_epoch'])
            require_integrity(result)
            if (result.source_evidence!=evidence or list(result.resource_security_labels)!=requirements.to_records()
                or result.payload!=payload or result.action_type!=run['parameters']['action_type']
                or result.case_id is not None or result.state!='pending'):
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            await engine.checkpoints.update(tx,conv,run,additional_labels=requirements,
                phase='done',result_draft_id=result.id,reason='draft_recorded')
        self.fresh(checked)
        return RunResult(run['id'],'done','draft_recorded',draft=result,labels=requirements)
