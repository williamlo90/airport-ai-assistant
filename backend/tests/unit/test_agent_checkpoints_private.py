"""Opt-in checkpoint guards; controlled repository, no private SQL claim."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from test_agent_workflow import setup as workflow_setup
from test_agent_private_context import labels


def setup():
    f,store,model,_,request=workflow_setup()
    engine=AgentWorkflow(f.service,store,private_persistence=True)
    async def allowed(**kwargs):
        assert not store.in_tx,'Source network is never under checkpoint DB lock'
        return True
    f.private.allows.side_effect=allowed
    return f,store,model,engine,request


def test_source_checkpoint_keeps_only_references_and_labels_and_rechecks_after_restart():
    async def run():
        f,store,model,engine,request=setup();graph=AgentGraph(engine)
        id=await graph.start(f.identity,request);result=await graph.resume(f.identity,id)
        assert result.status=='waiting_user' and result.labels is not None
        checkpoint=store.rows[id]
        assert checkpoint['resource_security_labels']==result.labels.to_records()
        text=json.dumps(checkpoint,default=str)
        assert 'PK-A-001' in text and 'booking:read' in text
        for raw in ('confirmed','service_timezone','source_updated_at','Please clarify your booking issue.'):
            assert raw not in text
        before=len(model.calls)
        restored=await AgentGraph(AgentWorkflow(f.service,store,private_persistence=True)).resume(f.identity,id)
        assert restored.labels==result.labels and restored.observations==() and len(model.calls)==before
        assert restored.question!=result.question
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,None,'true',SourceFailure(503)])
def test_revoked_unavailable_or_invalid_grants_block_private_run_load_without_model(verdict):
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        await AgentGraph(engine).resume(f.identity,id);before=len(model.calls)
        async def denied(**kwargs):
            assert not store.in_tx
            if isinstance(verdict,Exception):raise verdict
            return verdict
        f.private.allows.side_effect=denied
        with pytest.raises((AuthError,SourceFailure)):await engine.load(f.identity,id)
        assert len(model.calls)==before
    asyncio.run(run())


def test_duplicate_start_of_a_private_checkpoint_is_not_a_grant_bypass():
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        await AgentGraph(engine).resume(f.identity,id)
        assert await engine.start(f.identity,request)==id
        f.private.allows.side_effect=None;f.private.allows.return_value=False
        with pytest.raises(AuthError):await engine.start(f.identity,request)
        assert len(store.rows)==1
    asyncio.run(run())


def test_private_root_start_copies_the_current_whole_classification():
    async def run():
        f,store,model,engine,request=setup();f.conv.resource_security_labels=labels()
        id=await engine.start(f.identity,request)
        assert store.rows[id]['resource_security_labels']==list(labels())
        assert store.commits==2 and model.calls==[]
        f.private.allows.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('change',['session','principal','tenant','id','labels'])
def test_run_metadata_mismatch_is_rejected_before_source_permission(change):
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        field={'session':'session_id','principal':'principal_id','tenant':'tenant_id','id':'id','labels':'resource_security_labels'}[change]
        store.rows[id][field]=None if change=='labels' else uuid4()
        f.private.allows.reset_mock()
        with pytest.raises(AuthError):await engine.load(f.identity,id)
        f.private.allows.assert_not_awaited()
    asyncio.run(run())


def test_checkpoint_revision_changed_during_grants_cannot_be_used_for_a_claim():
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        store.rows[id]['resource_security_labels']=list(labels())
        async def changed(**kwargs):
            store.rows[id]['revision']+=1
            return True
        f.private.allows.side_effect=changed
        with pytest.raises(DraftError):await engine.load(f.identity,id)
        assert model.calls==[]
    asyncio.run(run())


def test_write_that_drops_the_new_classification_is_rolled_back():
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        work=await engine.load(f.identity,id);await engine.plan(work)
        work=await engine.load(f.identity,id)
        update=store.update_run
        async def corrupt(row,**changes):
            result=await update(row,**changes)
            if 'resource_security_labels' in changes:store.rows[id]['resource_security_labels']=[]
            return result
        store.update_run=corrupt
        with pytest.raises(DraftError):await engine.read(work)
        assert store.rows[id]['trace']==[] and store.rows[id]['resource_security_labels']==[]
        assert len(model.calls)==1
    asyncio.run(run())


def test_public_checkpoint_start_retains_one_transaction_and_no_grant_call():
    async def run():
        f,store,model,engine,request=setup()
        await engine.start(f.identity,request)
        assert store.commits==1
        f.private.allows.assert_not_awaited()
    asyncio.run(run())


def test_private_persistence_is_off_by_default_until_sql_graph_qualification():
    f,store,model,engine,request=workflow_setup()
    assert engine.checkpoints.enabled is False


@pytest.mark.parametrize('malformed',[None,'[]',{}])
def test_off_mode_does_not_treat_malformed_checkpoint_classification_as_public(malformed):
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        store.rows[id]['resource_security_labels']=malformed
        disabled=AgentWorkflow(f.service,store)
        with pytest.raises(AuthError):await disabled.load(f.identity,id)
        assert model.calls==[]
    asyncio.run(run())


@pytest.mark.parametrize('operation',['load','start'])
def test_off_mode_rejects_a_private_result_draft_even_when_its_run_header_is_empty(operation):
    async def run():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request);draft_id=uuid4()
        store.drafts.append(SimpleNamespace(id=draft_id,tenant_id=f.identity.session.tenant_id,conversation_id=f.conv.id,
            actor_principal_id=f.identity.session.principal_id,actor_session_id=None,created_session_id=f.identity.session.id,
            payload_hash='controlled-fixture-hash',version=1,resource_security_labels=list(labels())))
        store.rows[id].update(phase='done',result_draft_id=draft_id)
        disabled=AgentWorkflow(f.service,store)
        with pytest.raises(AuthError):
            if operation=='load':await disabled.load(f.identity,id)
            else:await disabled.start(f.identity,request)
        assert model.calls==[]
    asyncio.run(run())
