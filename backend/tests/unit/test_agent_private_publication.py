"""Typed live refetch and labelled generic recovery; no provider requests."""
import asyncio,json
from dataclasses import replace
import pytest
from airport_assistant.modules.agent.private_publication import PrivatePublicationPreparation,RESTORED_TEMPLATES
from airport_assistant.modules.agent.workflow import RunResult
from airport_assistant.modules.agent.models import AgentError,ToolObservation
from airport_assistant.modules.source.evidence import SourceEvidence,ObservedSource
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from test_agent_private_bridge import setup as bridge_setup
from test_source_evidence import items


async def setup():
    f,store,model,engine,request=bridge_setup()
    id=await engine.start(f.identity,request)
    book,payment,policy=items()
    evidence=SourceEvidence.capture(f.identity.session.tenant_id,(ObservedSource(book),ObservedSource(payment),ObservedSource(policy,book)))
    store.rows[id].update(phase='waiting_user',resource_security_labels=evidence.labels.to_records())
    observations=(ToolObservation('read_booking',book,evidence.labels),ToolObservation('read_transaction',payment,evidence.labels),
        ToolObservation('read_booking_policy',policy,evidence.labels,booking=book))
    result=RunResult(id,'waiting_user','clarification_required',question='Please clarify the source discrepancy.',observations=observations,labels=evidence.labels)
    return f,store,model,engine,id,result


def test_live_source_refetch_body_binding_union_and_durable_budget():
    async def run():
        f,store,model,engine,id,result=await setup()
        prepared=await PrivatePublicationPreparation(engine).prepare(f.identity,id,result)
        assert prepared.body==result.question and prepared.proof.kind=='live'
        assert prepared.proof.requirements.to_records()==store.rows[id]['resource_security_labels']
        assert prepared.proof.run_revision==store.rows[id]['revision'] and store.rows[id]['read_calls']==3
        assert model.calls==[]
        serialized=json.dumps(store.rows[id],default=str)
        for raw in (result.question,'amount_minor','instructions','source_updated_at'):assert raw not in serialized
    asyncio.run(run())


def test_restored_or_unproven_text_uses_only_server_template_with_same_labels():
    async def run():
        f,store,model,engine,id,result=await setup()
        restored=replace(result,question='Old private model text must not replay',observations=())
        prepared=await PrivatePublicationPreparation(engine).prepare(f.identity,id,restored)
        assert prepared.body==RESTORED_TEMPLATES['en'] and prepared.proof.kind=='generic_restore'
        assert prepared.proof.requirements==result.labels and prepared.proof.source_fact_hash is None
        assert store.rows[id]['read_calls']==0 and model.calls==[]
    asyncio.run(run())


def test_changed_source_facts_cannot_authorize_old_model_text():
    async def run():
        f,store,model,engine,id,result=await setup()
        book=await f.source.booking(f.identity,'PK-A-001')
        async def changed(*a):return book.model_copy(update={'status':'cancelled'})
        f.source.booking.side_effect=changed
        with pytest.raises(AgentError):await PrivatePublicationPreparation(engine).prepare(f.identity,id,result)
        assert store.rows[id]['phase']=='waiting_user' and model.calls==[]
    asyncio.run(run())


def test_extra_publication_read_uses_existing_budget_without_reset():
    async def run():
        f,store,model,engine,id,result=await setup();store.rows[id]['read_calls']=12
        f.source.booking.reset_mock()
        with pytest.raises(AgentError) as error:await PrivatePublicationPreparation(engine).prepare(f.identity,id,result)
        assert error.value.code=='AGENT_STEP_LIMIT' and store.rows[id]['read_calls']==12
        f.source.booking.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,SourceFailure(503)])
def test_current_grants_are_checked_even_for_generic_restore(verdict):
    async def run():
        f,store,model,engine,id,result=await setup()
        async def denied(**kw):
            assert not store.in_tx
            if isinstance(verdict,Exception):raise verdict
            return verdict
        f.private.allows.side_effect=denied
        with pytest.raises((AuthError,SourceFailure)):
            await PrivatePublicationPreparation(engine).prepare(f.identity,id,replace(result,observations=()))
    asyncio.run(run())


def test_full_policy_booking_binding_is_not_a_reference_only_snapshot():
    async def run():
        f,store,model,engine,id,result=await setup()
        policy=result.observations[-1]
        bad=replace(policy,booking=policy.booking.model_copy(update={'location':'Another checked location'}))
        with pytest.raises(AgentError):
            await PrivatePublicationPreparation(engine).prepare(f.identity,id,replace(result,observations=(*result.observations[:-1],bad)))
    asyncio.run(run())


def test_agent_delivery_forwards_live_private_input_only_with_explicit_capability():
    async def run():
        from airport_assistant.modules.agent.delivery import AgentDelivery
        from airport_assistant.modules.agent.workflow import AgentWorkflow
        from airport_assistant.integrations.agent.graph import AgentGraph
        from test_agent_workflow import setup as workflow_setup
        from test_agent_delivery import Publication
        f,store,model,_,request=workflow_setup()
        engine=AgentWorkflow(f.service,store,private_persistence=True)
        class Enabled(Publication):
            private_capability=object()
            async def publish_prepared(self,identity,ticket,collector,requirements):
                prepared=await collector()
                assert prepared.proof.requirements==requirements
                return await self.publish(identity,ticket,prepared.body,private_input=prepared.proof)
            async def capture_bound(self,identity,cid,ticket,version,epoch,*,requirements=None):
                if requirements is not None:self.labels=requirements
                await super().capture_bound(identity,cid,ticket,version,epoch)
            async def publish(self,identity,ticket,body,*,private_input=None):
                self.proof=private_input
                assert private_input.requirements==self.labels
                return await super().publish(identity,ticket,body)
        publication=Enabled(f);delivery=AgentDelivery(AgentGraph(engine),publication,b'internal-test')
        id=await delivery.start(f.identity,request)
        first=await delivery.resume(f.identity,id)
        assert first['state']=='published' and publication.proof.kind=='live'
        before=len(model.calls)
        assert await delivery.resume(f.identity,id)==first and len(model.calls)==before
        assert len(publication.bodies)==1 and store.rows[id]['read_calls']<=12
    asyncio.run(run())
