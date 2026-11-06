"""New fact collection cannot refresh an old grant or bypass publication guards."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from airport_assistant.modules.agent.private_publication import PrivatePublicationPreparation,PreparedPublication
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from test_ai_publication_private import captured
from test_agent_private_publication import setup as preparation_setup


def test_slow_commit_cannot_disclose_after_original_grant_deadline_or_repeat_collection():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        original=store.transaction;slow=[True];calls=[]
        @asynccontextmanager
        async def transaction(context):
            async with original(context) as tx:yield tx
            if slow[0] and store.graph is not None:store.clock=2.5
        store.transaction=transaction
        async def collect():
            calls.append(True);store.clock=1.5
            return PreparedPublication(body,replace(proof,checked_at=1.5))
        with pytest.raises(SourceFailure):await service.publish_prepared(identity,ticket,collect,proof.requirements)
        # Commit may exist; only the response is unavailable. A fresh receipt
        # authorizes and reads that one result without recollecting or writing.
        assert store.graph is not None and store.g['state']=='published'
        slow[0]=False
        saved=await service.receipt(identity,ticket)
        assert saved['state']=='published' and calls==[True]
    asyncio.run(run())


@pytest.mark.parametrize('change',[None,'delayed_grant','expired_fact','revision','session','subject','body_hash','requirements','postwrite_expiry'])
def test_publisher_keeps_grant_fact_identity_and_actual_write_boundaries(change):
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        store.subject='one'
        original_context=store.ai_publication_context
        async def context(*args):return dict(await original_context(*args),source_subject_ref=store.subject)
        store.ai_publication_context=context
        if change=='postwrite_expiry':
            original_write=store.ai_write_publication
            async def late(*args,**kwargs):
                result=await original_write(*args,**kwargs);store.clock=2.01;return result
            store.ai_write_publication=late
        calls=[]
        async def collect():
            assert not store.in_tx and access.allows.await_count>0
            calls.append(True)
            if change in {'delayed_grant','expired_fact'}:store.clock=2.01
            if change=='revision':store.run['revision']+=1
            if change=='session':store.active=False
            if change=='subject':store.subject='two'
            current=replace(proof,checked_at=store.clock)
            if change=='expired_fact':current=proof
            if change=='body_hash':current=replace(current,body_hash='a'*64)
            if change=='requirements':
                records=current.requirements.to_records()[:1]
                current=replace(current,requirements=SecurityLabels.from_records(identity.session.tenant_id,records))
            return PreparedPublication(body,current)
        if change:
            with pytest.raises((DraftError,AuthError,SourceFailure)):
                await service.publish_prepared(identity,ticket,collect,proof.requirements)
            assert store.graph is None and store.g['state']=='pending'
        else:
            result=await service.publish_prepared(identity,ticket,collect,proof.requirements)
            assert result['state']=='published' and store.graph['message']['body']==body
        assert calls==[True] and proof.checked_at==0
    asyncio.run(run())


async def preparation():
    f,store,model,engine,id,result=await preparation_setup()
    book,payment,policy=[item.data for item in result.observations]
    source=SimpleNamespace(booking=AsyncMock(return_value=book),transaction=AsyncMock(return_value=payment),policy=AsyncMock(return_value=policy))
    engine.foundation.source=source
    service=PrivatePublicationPreparation(engine)
    plan=await service.reserve(f.identity,id,result)
    return f,store,model,engine,id,result,source,service,plan


def test_reservation_precedes_new_typed_reads_and_collector_is_single_use():
    async def run():
        f,store,model,engine,id,result,source,service,plan=await preparation()
        assert store.rows[id]['read_calls']==3 and not source.booking.await_count
        prepared=await service.collect(f.identity,plan)
        assert prepared.body==result.question and prepared.proof.run_revision==store.rows[id]['revision']
        assert prepared.proof.kind=='live' and len(model.calls)==0
        assert source.booking.await_count==2 and source.transaction.await_count==1 and source.policy.await_count==1
        with pytest.raises(AgentError):await service.collect(f.identity,plan)
        assert source.booking.await_count==2 and store.rows[id]['read_calls']==3
    asyncio.run(run())


@pytest.mark.parametrize('change',['facts','reference','scope','policy','source_failure'])
def test_new_collection_rejects_changes_and_never_refunds_or_retries(change):
    async def run():
        f,store,model,engine,id,result,source,service,plan=await preparation()
        if change=='facts':source.booking.return_value=source.booking.return_value.model_copy(update={'status':'cancelled'})
        if change=='reference':source.booking.return_value=source.booking.return_value.model_copy(update={'reference':'OTHER'})
        if change=='scope':
            from airport_assistant.modules.source.models import Transaction
            old=source.transaction.return_value
            source.transaction.return_value=Transaction(reference=old.reference,source_updated_at=old.source_updated_at,
                observed_at=old.observed_at,status=old.status,booking_reference=old.booking_reference,allowed_scopes=('payment_status:read',))
        if change=='policy':source.policy.return_value=source.policy.return_value.model_copy(update={'service_type':'lounge'})
        if change=='source_failure':source.booking.side_effect=SourceFailure(503)
        with pytest.raises((AgentError,SourceFailure)):await service.collect(f.identity,plan)
        calls=source.booking.await_count+source.transaction.await_count+source.policy.await_count
        with pytest.raises(AgentError):await service.collect(f.identity,plan)
        assert calls==source.booking.await_count+source.transaction.await_count+source.policy.await_count
        assert store.rows[id]['read_calls']==3 and len(model.calls)==0
    asyncio.run(run())


def test_durable_reservation_limit_is_not_reset_by_failed_or_abandoned_collection():
    async def run():
        f,store,model,engine,id,result,source,service,plan=await preparation()
        for _ in range(3):await service.reserve(f.identity,id,result)
        assert store.rows[id]['read_calls']==12
        with pytest.raises(AgentError) as error:await service.reserve(f.identity,id,result)
        assert error.value.code=='AGENT_STEP_LIMIT' and store.rows[id]['read_calls']==12
        assert not source.booking.await_count and len(model.calls)==0
    asyncio.run(run())
