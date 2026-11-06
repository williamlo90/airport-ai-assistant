"""Real draft builder and atomic controlled run store; no SQL/provider IO."""
import asyncio,json
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest

from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.conversations.service import Conversation
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.drafts.models import DraftError,require_integrity,seal
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from test_agent_workflow import setup as workflow_setup
from test_draft_rules import setup as draft_setup
from test_source_evidence import items


def setup():
    f,store,model,_,request=workflow_setup()
    f.conv=Conversation(f.conv.id,f.identity.session.tenant_id,f.identity.session.principal_id,None,
        'en',f.conv.version,f.conv.generation_epoch,1)
    destination=draft_setup()[2].destination
    store.find=AsyncMock(side_effect=lambda *a,**k:f.conv)
    store.lock_draft_case=AsyncMock()
    store.locate_draft=AsyncMock(side_effect=lambda id:f.conv.id if any(d.id==id for d in store.drafts) else None)
    original_context=store.run_result_context
    async def context(id):
        result=await original_context(id)
        if result is not None:result['resource_security_labels']=list(result['resource_security_labels'])
        return result
    store.run_result_context=context
    store.destination=AsyncMock(return_value=destination)
    async def create(draft):store.drafts.append(draft);return draft
    store.create_draft=AsyncMock(side_effect=create)
    f.service.drafts=DraftService(store,f.private)
    book,payment,policy=items()
    async def source(value):
        assert not store.in_tx
        return value
    async def booking(*a):return await source(book)
    async def transaction(*a):return await source(payment)
    async def rules(*a):return await source(policy)
    f.source.booking.side_effect=booking;f.source.transaction.side_effect=transaction;f.source.policy.side_effect=rules
    async def grants(**kw):assert not store.in_tx;return True
    f.private.allows.side_effect=grants
    engine=AgentWorkflow(f.service,store,private_persistence=True)
    request=request.model_copy(update={'mode':'draft','transaction_references':('TX-A-001',)})
    return f,store,model,engine,request


async def planned():
    f,store,model,engine,request=setup()
    id=await engine.start(f.identity,request)
    await engine.plan(await engine.load(f.identity,id))
    return f,store,model,engine,id


def test_positive_bridge_exact_source_review_atomic_association_and_restart():
    async def run():
        f,store,model,engine,id=await planned()
        serialized=json.dumps(store.rows[id],default=str)
        for raw in ('confirmed','source_updated_at','instructions','amount_minor'):assert raw not in serialized
        result=await engine.draft(await engine.load(f.identity,id))
        draft=result.draft;require_integrity(draft)
        assert len(draft.source_evidence.to_records())==3
        assert draft.source_evidence.display('en') in draft.displayed_summary
        assert list(draft.resource_security_labels)==result.labels.to_records()==store.rows[id]['resource_security_labels']
        assert store.rows[id]['result_draft_id']==draft.id and store.rows[id]['phase']=='done'
        assert store.rows[id]['read_calls']==9
        restored=await AgentWorkflow(f.service,store,private_persistence=True).load(f.identity,id)
        assert restored.result.draft==draft and restored.result.labels==result.labels
        again=await engine.draft(restored)
        assert again.draft==draft and again.labels==result.labels and len(store.drafts)==1 and len(model.calls)==1
    asyncio.run(run())


def test_failed_run_cas_rolls_back_draft_then_restart_uses_same_model_proposal():
    async def run():
        f,store,model,engine,id=await planned();store.fail_receipt=True
        with pytest.raises(RuntimeError):await engine.draft(await engine.load(f.identity,id))
        assert store.drafts==[] and store.rows[id]['phase']=='draft' and store.rows[id]['result_draft_id'] is None
        restored=AgentWorkflow(f.service,store,private_persistence=True)
        result=await restored.draft(await restored.load(f.identity,id))
        assert len(store.drafts)==1 and store.rows[id]['result_draft_id']==result.draft.id and len(model.calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('when',['model','draft'])
def test_changed_facts_require_review_without_creating_draft(when):
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        async def change():
            original=await f.source.booking(f.identity,'PK-A-001')
            async def updated(*a):return original.model_copy(update={'status':'cancelled'})
            f.source.booking.side_effect=updated
        if when=='model':model.hook=change
        await engine.plan(await engine.load(f.identity,id))
        if when=='draft':
            await change()
            result=await engine.draft(await engine.load(f.identity,id))
            assert result.status=='review'
        assert store.rows[id]['phase']=='review' and not store.drafts
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,None,'true',SourceFailure(503)])
def test_revoked_or_outage_before_draft_cannot_insert(verdict):
    async def run():
        f,store,model,engine,id=await planned();work=await engine.load(f.identity,id)
        async def denied(**kw):
            assert not store.in_tx
            if isinstance(verdict,Exception):raise verdict
            return verdict
        f.private.allows.side_effect=denied
        with pytest.raises((AuthError,SourceFailure)):await engine.draft(work)
        assert not store.drafts and store.rows[id]['phase']=='draft'
    asyncio.run(run())


@pytest.mark.parametrize('change',['evidence','requirements','association','stored_payload'])
def test_corrupt_actual_draft_or_run_association_rolls_back(change):
    async def run():
        f,store,model,engine,id=await planned();original=store.update_run
        async def corrupt(row,**changes):
            result=await original(row,**changes)
            if changes.get('phase')=='done':
                draft=store.drafts[0]
                if change=='association':store.rows[id]['result_draft_id']=None
                elif change=='evidence':store.drafts[0]=replace(draft,source_evidence=None)
                elif change=='requirements':store.drafts[0]=replace(draft,resource_security_labels=())
                else:
                    store.drafts[0]=seal(replace(draft,payload=draft.payload.model_copy(update={'summary':'Changed after write'}),
                        displayed_summary=draft.displayed_summary.replace('Please help','Changed after write')))
            return result
        store.update_run=corrupt
        with pytest.raises((DraftError,AuthError)):await engine.draft(await engine.load(f.identity,id))
        assert store.drafts==[] and store.rows[id]['phase']=='draft' and store.rows[id]['result_draft_id'] is None
    asyncio.run(run())


def test_whole_historical_requirements_survive_fresh_dto_scope_narrowing():
    async def run():
        f,store,model,engine,id=await planned()
        original=await f.source.transaction(f.identity,'TX-A-001')
        # A later reduction changes facts, so it cannot quietly replace this proposal.
        data=original.model_dump(exclude={'currency','amount_minor','currency_exponent','refund_status'})
        data['allowed_scopes']=('payment_status:read',)
        from airport_assistant.modules.source.models import Transaction
        narrow=Transaction.model_validate(data)
        async def read(*a):return narrow
        f.source.transaction.side_effect=read
        result=await engine.draft(await engine.load(f.identity,id))
        assert result.status=='review' and not store.drafts
        transaction=next(x for x in store.rows[id]['resource_security_labels'] if x['resource_type']=='transaction')
        assert 'payment_details:read' in transaction['required_source_scopes']
    asyncio.run(run())


def test_stale_source_at_final_write_does_not_leave_orphan():
    async def run():
        f,store,model,engine,id=await planned();clock=[0.0];engine.private_drafts.clock=lambda:clock[0]
        original=store.create_draft.side_effect
        async def slow(draft):
            result=await original(draft);clock[0]=3.0;return result
        store.create_draft.side_effect=slow
        with pytest.raises(SourceFailure):await engine.draft(await engine.load(f.identity,id))
        assert store.drafts==[] and store.rows[id]['result_draft_id'] is None
    asyncio.run(run())


def test_stale_work_after_another_success_recovers_labelled_draft_without_insert():
    async def run():
        f,store,model,engine,id=await planned();stale=await engine.load(f.identity,id)
        first=await engine.draft(await engine.load(f.identity,id))
        recovered=await engine.draft(stale)
        assert recovered.draft==first.draft and recovered.labels==first.labels and len(store.drafts)==1
    asyncio.run(run())


def test_extra_root_requirements_are_kept_in_new_draft_and_result():
    async def run():
        f,store,model,engine,request=setup()
        extra=dict(source='reference',resource_type='booking',reference='HISTORICAL-BOOKING',required_source_scopes=['booking:read'])
        f.conv=replace(f.conv,resource_security_labels=(extra,))
        id=await engine.start(f.identity,request);await engine.plan(await engine.load(f.identity,id))
        result=await engine.draft(await engine.load(f.identity,id))
        assert extra in result.labels.to_records() and extra in result.draft.resource_security_labels
        assert result.labels.to_records()==store.rows[id]['resource_security_labels']
    asyncio.run(run())


def test_recovery_union_uses_actual_child_even_if_old_header_did_not_inherit_it():
    async def run():
        f,store,model,engine,id=await planned()
        first=await engine.draft(await engine.load(f.identity,id))
        store.rows[id]['resource_security_labels']=[]
        recovered=await engine.load(f.identity,id)
        assert recovered.result.labels==first.labels
        f.private.allows.side_effect=None;f.private.allows.return_value=False
        with pytest.raises(AuthError):await engine.load(f.identity,id)
    asyncio.run(run())


def test_postloader_last_read_crossing_source_deadline_rolls_back_before_commit():
    async def run():
        f,store,model,engine,id=await planned()
        work=await engine.load(f.identity,id)
        clock=[0.0]
        engine.private_drafts.clock=lambda:clock[0]
        engine.checkpoints.gate.clock=lambda:clock[0]
        collected=False
        original_collect=engine.private_drafts.collect
        async def collect(*args):
            nonlocal collected
            result=await original_collect(*args)
            collected=True
            return result
        engine.private_drafts.collect=collect
        async def grants(**kw):
            assert not store.in_tx
            if collected:clock[0]=0.75
            return True
        f.private.allows.side_effect=grants
        original_find=store.find_draft
        postloader_reads=0
        async def delayed(id):
            nonlocal postloader_reads
            draft=await original_find(id)
            if collected and store.rows[work.run['id']]['phase']=='done':
                postloader_reads+=1
                # First read is the shared child projection; second is the
                # operation-specific exact draft read after verify's precheck.
                if postloader_reads==2:clock[0]=2.25
            return draft
        store.find_draft=delayed
        with pytest.raises(SourceFailure):await engine.draft(work)
        assert clock[0]==2.25 and postloader_reads==2
        assert not store.drafts and store.rows[id]['phase']=='draft'
        assert store.rows[id]['result_draft_id'] is None
    asyncio.run(run())
