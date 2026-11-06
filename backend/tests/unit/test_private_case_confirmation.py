"""Source-bearing consent, replay and real SQL parameters stay bound together.

The repository below models the future positive graph contract, NOT a private
SQL activation proof. Actual constraints remain closed until its migration.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.cases.models import CommitResult
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.drafts.models import DraftError,seal,reviewed_summary
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from test_private_draft_mutations import setup as draft_setup


def setup(allowed=True,*,action='create_case',private=True):
    identity,state,counts,tx,access,drafts=draft_setup(allowed=allowed,private=private)
    draft=state['draft'];payload=draft.payload.model_copy(update={'contact_email':'customer@example.com'})
    state['draft']=seal(replace(draft,action_type=action,payload=payload,displayed_summary=reviewed_summary(action,payload,
        draft.destination,draft.language,source_evidence=draft.source_evidence)))
    state.update(case=None,result=None,keys={},consents=[],messages=[],jobs=[])
    tx.lock_key=AsyncMock();tx.idempotency=AsyncMock(side_effect=lambda key:state['keys'].get(key))
    tx.lock_destination=AsyncMock(side_effect=lambda destination:destination)
    tx.verified_contact=AsyncMock(return_value=uuid4())
    async def create(draft,case_id,message_id,*args):
        counts['writes']+=1
        state['case']=dict(id=case_id,origin_draft_id=draft.id,origin_conversation_id=draft.conversation_id,
            source_observations=draft.source_evidence.to_records() if draft.source_evidence is not None else [],
            resource_security_labels=list(draft.resource_security_labels))
        state['parent']=dict(id=case_id,version=1,resource_security_labels=list(draft.resource_security_labels))
        state['result']=CommitResult(case_id=case_id,result_ref='CASE-'+case_id.hex,message_id=message_id)
        state['consents'].append(draft.id);state['messages'].append(message_id);state['jobs'].append(case_id)
        return state['result']
    async def committed(draft,result):
        state['draft']=replace(draft,state='committed',result_case_id=result.case_id,result_ref=result.result_ref,
            result_message_id=result.message_id,committed_at=state['now'])
        state['conversation']=replace(state['conversation'],case_id=result.case_id,case_access_verified=True,
            resource_security_labels=draft.resource_security_labels,mode='waiting_staff',version=2,generation_epoch=2)
        return True
    async def record(key,digest,*args):state['keys'][key]=digest
    tx.create_case=AsyncMock(side_effect=create);tx.mark_committed=AsyncMock(side_effect=committed)
    tx.record_key=AsyncMock(side_effect=record)
    tx.case_result=AsyncMock(side_effect=lambda id:state['result'] if state['result'] and id==state['result'].case_id else None)
    tx.confirmation_context=AsyncMock(side_effect=lambda id:deepcopy(state['case']))
    tx.receipt=AsyncMock(side_effect=lambda id:None if state['result'] is None else dict(draft_id=state['draft'].id,
        state='committed',case_id=state['result'].case_id,result_ref=state['result'].result_ref,version=state['draft'].version,expires_at=None))
    @asynccontextmanager
    async def savepoint():
        saved=deepcopy(state)
        try:yield
        except BaseException:
            state.clear();state.update(saved)
            raise
    tx.savepoint=savepoint
    return identity,state,counts,tx,access,CaseService(drafts._repository,access)


def existing_handoff_setup(allowed=True):
    identity,state,counts,tx,access,service=setup(allowed,action='request_handoff')
    draft=state['draft'];case_id=uuid4();message_id=uuid4()
    payload=draft.payload.model_copy(update={'contact_email':None})
    state['case']=dict(id=case_id,origin_draft_id=uuid4(),origin_conversation_id=uuid4(),
        source_observations=draft.source_evidence.to_records(),resource_security_labels=list(draft.resource_security_labels))
    state['parent']=dict(id=case_id,version=3,resource_security_labels=list(draft.resource_security_labels))
    state['result']=CommitResult(case_id=case_id,result_ref='CASE-'+case_id.hex,message_id=message_id)
    state['messages']=[message_id]
    state['draft']=seal(replace(draft,case_id=case_id,case_access_verified=True,source_evidence=None,payload=payload,
        displayed_summary=reviewed_summary(draft.action_type,payload,draft.destination,draft.language,case_id)))
    tx.handoff_case=AsyncMock(return_value=dict(status='new',result_ref=state['result'].result_ref,message_id=message_id))
    tx.active_handoff=AsyncMock(return_value=False)
    async def consent(draft,request_id):state['consents'].append(draft.id)
    tx.handoff_consent=AsyncMock(side_effect=consent)
    return identity,state,counts,tx,access,service


def confirm(service,identity,state,key=None,version=None,hash_value=None):
    draft=state['draft']
    return asyncio.run(service.confirm(identity,draft.id,version or draft.version,hash_value or draft.payload_hash,key or str(uuid4()),uuid4()))


@pytest.mark.parametrize('action',['create_case','request_handoff'])
@pytest.mark.parametrize('private',[False,True])
def test_positive_confirmation_keeps_the_approved_source_snapshot_and_labels(action,private):
    identity,state,counts,tx,access,service=setup(action=action,private=private);approved=state['draft']
    result=confirm(service,identity,state)
    assert result==state['result'] and state['draft'].state=='committed'
    assert state['case']['source_observations']==(approved.source_evidence.to_records() if private else [])
    assert state['case']['resource_security_labels']==list(approved.resource_security_labels)
    assert tx.create_case.await_args.args[0]==approved
    assert counts==dict(commits=2 if private else 1,rollbacks=0,writes=1)
    if private:assert len(access.allows.await_args.kwargs['labels'].labels)==2
    else:access.allows.assert_not_awaited()


@pytest.mark.parametrize('allowed',[False,None,'true',SourceFailure(503)])
@pytest.mark.parametrize('action',['create_case','request_handoff'])
def test_unavailable_or_denied_source_cannot_create_case_consent_or_outbox(allowed,action):
    identity,state,counts,tx,access,service=setup(allowed,action=action)
    with pytest.raises((AuthError,SourceFailure)):confirm(service,identity,state)
    assert state['case'] is None and state['keys']=={} and counts['writes']==0
    tx.create_case.assert_not_awaited();tx.record_key.assert_not_awaited()


@pytest.mark.parametrize('change',['hash','version'])
def test_confirmation_still_requires_the_exact_human_preview(change):
    identity,state,counts,tx,access,service=setup()
    with pytest.raises(DraftError):confirm(service,identity,state,version=2 if change=='version' else None,
        hash_value='0'*64 if change=='hash' else None)
    assert counts['writes']==0 and state['case'] is None


@pytest.mark.parametrize('action',['create_case','request_handoff'])
def test_committed_retries_require_current_grants_and_never_repeat_case_creation(action):
    identity,state,counts,tx,access,service=setup(action=action);key=str(uuid4())
    result=confirm(service,identity,state,key)
    state['now']=state['draft'].expires_at
    assert confirm(service,identity,state,key)==result
    assert confirm(service,identity,state)==result
    assert counts['writes']==1 and access.allows.await_count==3
    access.allows.side_effect=None;access.allows.return_value=False
    with pytest.raises(AuthError):confirm(service,identity,state,key)
    assert counts['writes']==1


@pytest.mark.parametrize('action',['create_case','request_handoff'])
@pytest.mark.parametrize('change',['missing_case','id','source_observations','resource_security_labels','origin_draft_id','origin_conversation_id'])
def test_missing_or_mismatched_persisted_graph_fails_before_commit(change,action):
    identity,state,counts,tx,access,service=setup(action=action);original=tx.mark_committed.side_effect
    before=deepcopy(state)
    async def corrupt(*args):
        result=await original(*args)
        if change=='missing_case':state['case']=None
        else:state['case'][change]=[] if change in {'source_observations','resource_security_labels'} else uuid4()
        return result
    tx.mark_committed.side_effect=corrupt
    with pytest.raises(DraftError) as error:confirm(service,identity,state)
    assert error.value.status==409 and error.value.code=='SOURCE_CONTEXT_CHANGED'
    # Includes controlled consent/message/job effects, receipt, parent and link.
    assert state==before
    assert counts['rollbacks']==1


def test_existing_handoff_preserves_old_case_and_replays_under_current_grants():
    identity,state,counts,tx,access,service=existing_handoff_setup();key=str(uuid4())
    original=deepcopy(state['case']);approved=state['draft'];messages=list(state['messages'])
    result=confirm(service,identity,state,key)
    state['now']=approved.expires_at
    assert confirm(service,identity,state,key)==result
    assert confirm(service,identity,state)==result
    assert state['case']==original and state['messages']==messages and state['jobs']==[]
    assert state['consents']==[approved.id] and state['draft'].payload_hash==approved.payload_hash
    assert state['draft'].source_evidence is None and counts['writes']==0
    tx.create_case.assert_not_awaited();tx.confirmation_context.assert_not_awaited()
    tx.handoff_consent.assert_awaited_once()
    assert access.allows.await_count==3
    before=deepcopy(state)
    access.allows.side_effect=None;access.allows.return_value=False
    with pytest.raises(AuthError):confirm(service,identity,state,key)
    assert state==before


@pytest.mark.parametrize('allowed',[False,SourceFailure(503)])
def test_existing_handoff_denied_before_initial_consent(allowed):
    identity,state,counts,tx,access,service=existing_handoff_setup(allowed)
    before=deepcopy(state)
    with pytest.raises((AuthError,SourceFailure)):confirm(service,identity,state)
    assert state==before
    tx.handoff_consent.assert_not_awaited();tx.create_case.assert_not_awaited()


def test_source_wait_must_not_authorize_a_newer_draft_hash():
    identity,state,counts,tx,access,service=setup()
    async def changed(**kwargs):state['draft']=seal(replace(state['draft'],version=2));return True
    access.allows.side_effect=changed
    with pytest.raises(DraftError):confirm(service,identity,state)
    assert counts['writes']==0 and state['case'] is None


def test_revoked_session_after_writes_rolls_back_the_entire_case_graph():
    identity,state,counts,tx,access,service=setup();original=tx.mark_committed.side_effect
    async def revoked(*args):
        result=await original(*args);state['active']=False
        return result
    tx.mark_committed.side_effect=revoked
    with pytest.raises(AuthError):confirm(service,identity,state)
    assert state['case'] is None and state['draft'].state=='pending' and state['keys']=={}


def test_slow_commit_status_recovers_the_original_result_without_a_second_case():
    identity,state,counts,tx,access,service=setup();clock=[0.0]
    service._private_content.clock=lambda:clock[0]
    state['after_commit']=lambda:clock.__setitem__(0,3.0) if counts['commits']>=2 else None
    with pytest.raises(SourceFailure):confirm(service,identity,state)
    assert state['draft'].state=='committed' and counts['writes']==1
    recovered=asyncio.run(service.status(identity,state['draft'].id))
    assert recovered['case_id']==state['result'].case_id and recovered['state']=='committed'
    assert counts['writes']==1


@pytest.mark.parametrize('private',[False,True])
def test_sql_case_message_and_outbox_parameters_preserve_source_and_public_column_contract(private):
    from airport_assistant.infrastructure.database.case_repository import CaseSQL
    identity,state,counts,tx,access,service=setup();draft=state['draft']
    if not private:
        from test_case_rules import case_setup
        identity,draft,*_=case_setup()
    phases=[]
    @asynccontextmanager
    async def pipeline():
        phases.append('entered')
        try:yield
        finally:phases.append('synchronized')
    connection=SimpleNamespace(execute=AsyncMock(),pipeline=pipeline)
    result=asyncio.run(CaseSQL(connection,'app',identity.database_context).create_case(draft,uuid4(),uuid4(),uuid4(),uuid4(),uuid4()))
    assert result.case_id
    assert phases==['entered','synchronized']
    first,second,*_,last=connection.execute.await_args_list
    if private:assert first.args[1][-2].obj==draft.source_evidence.to_records()
    else:
        assert 'source_observations' not in first.args[0].as_string()
        assert len(first.args[1])==20
    for call in (first,last):
        assert ('resource_security_labels' in call.args[0].as_string()) is private
        if private:assert call.args[1][-1].obj==list(draft.resource_security_labels)
    if not private:assert len(last.args[1])==5
    assert ('resource_security_labels' in second.args[0].as_string()) is private
    if private:assert second.args[1][-1].obj==list(draft.resource_security_labels)
    else:assert len(second.args[1])==9
