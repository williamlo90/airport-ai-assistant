"""Root packet boundaries retain the original write/graph/freshness checks."""
import asyncio,copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from airport_assistant.infrastructure.database.publication import PublicationSQL
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from test_ai_publication_private import captured


def connection(*rows):
    return SimpleNamespace(execute=AsyncMock(side_effect=[SimpleNamespace(fetchone=AsyncMock(return_value=row)) for row in rows]))


@pytest.mark.parametrize('hint',[None,(uuid4(),uuid4())])
def test_absent_or_linked_hint_never_takes_a_root_lock(hint):
    async def run():
        c=connection(hint)
        assert await PublicationSQL(c,'app',SimpleNamespace(tenant_id=uuid4())).ai_root_publication_packet(uuid4()) is None
        assert c.execute.await_count==1
    asyncio.run(run())


def test_root_linked_while_waiting_rejects_without_parent_lock_fallback():
    async def run():
        c=connection((uuid4(),None),None)
        with pytest.raises(DraftError) as error:
            await PublicationSQL(c,'app',SimpleNamespace(tenant_id=uuid4())).ai_root_publication_packet(uuid4())
        assert error.value.code=='SOURCE_CONTEXT_CHANGED' and c.execute.await_count==2
        sql=c.execute.call_args_list[-1].args[0].as_string(None)
        assert 'c.case_id IS NULL' in sql and 'FOR UPDATE OF c' in sql and 'FROM "app".cases' not in sql
    asyncio.run(run())


def test_actual_adapter_packet_uses_a_new_statement_after_lock_barrier():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        conv=store.conv
        fields=('id','tenant_id','customer_principal_id','anonymous_session_id','language','version','generation_epoch','last_sequence','case_id','resource_security_labels','mode')
        run_fields=('id','tenant_id','conversation_id','session_id','principal_id','phase','revision')
        gen_fields=('id','tenant_id','conversation_id','origin_session_id','principal_id','input_version','generation_epoch','language','nonce_hash','state','expires_at','message_id','resource_security_labels')
        row=tuple(conv[k] for k in fields)+(5,)+tuple(store.run[k] for k in run_fields)+(list(proof.requirements.to_records()),)+tuple(store.g[k] for k in gen_fields)+(None,None,store.time,'customer-subject')
        c=connection((conv['id'],None),(conv['id'],ticket.id),row)
        packet=await PublicationSQL(c,'app',SimpleNamespace(tenant_id=identity.session.tenant_id)).ai_root_publication_packet(ticket.id)
        assert c.execute.await_count==3
        lock=c.execute.call_args_list[1].args[0].as_string(None)
        read=c.execute.call_args_list[2].args[0].as_string(None)
        assert 'SELECT root.id' in lock and 'clock_timestamp()' not in lock
        assert 'clock_timestamp()' in read and 'jsonb_agg' in read
        assert packet['generation']==store.g and packet['graph'] is None
        assert packet['context']['conversation']['id']==conv['id']
        assert packet['context']['parent'] is None and packet['now']==store.time
    asyncio.run(run())


@pytest.mark.parametrize('corrupt',[None,'message','event','generation','counter','expired'])
def test_packet_path_keeps_actual_graph_verification_and_original_expiry(corrupt):
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        context=store.ai_publication_context;graph=store.ai_publication_result;generation=store.ai_generation_context
        async def packet(gid,cid=None):
            value=await context(store.conv['id'],gid)
            result=await graph(gid) if store.g and store.g['state']=='published' else None
            if corrupt=='expired' and result is not None:store.clock=2.01
            return dict(context=value,generation=copy.deepcopy(store.g),graph=result,now=store.time)
        store.ai_root_publication_packet=packet
        store.ai_publication_context=AsyncMock(side_effect=AssertionError('Unexpected split read'))
        store.ai_generation_context=AsyncMock(side_effect=AssertionError('Unexpected split generation read'))
        store.corrupt=corrupt
        if corrupt:
            with pytest.raises((DraftError,SourceFailure)):await service.publish(identity,ticket,body,private_input=proof)
            assert store.graph is None and store.g['state']=='pending'
        else:
            saved=await service.publish(identity,ticket,body,private_input=proof)
            assert saved['state']=='published'
        store.ai_publication_context.assert_not_awaited()
        store.ai_generation_context.assert_not_awaited()
        if not corrupt:
            store.ai_generation_context=generation
            assert await service.receipt(identity,ticket)==saved
    asyncio.run(run())
