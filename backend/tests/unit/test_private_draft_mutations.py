"""Editing/cancelling a private preview never bypasses its current source ACL."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.drafts.models import DraftError,seal,with_source_evidence
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.evidence import SourceEvidence,ObservedSource
from airport_assistant.modules.source.models import SourceFailure
from test_draft_rules import setup as base_setup,report
from test_source_evidence import items


def setup(private=True,allowed=True):
    old,conv,draft,tx,outcomes,base=base_setup();principal=uuid4()
    identity=replace(old,session=replace(old.session,kind='customer',principal_id=principal))
    conv=replace(conv,customer_principal_id=principal,anonymous_session_id=None)
    draft=replace(draft,actor_principal_id=principal,actor_session_id=None)
    book,payment,policy=items()
    draft=replace(draft,payload=report(booking_reference=book.reference,transaction_reference=payment.reference))
    if private:
        evidence=SourceEvidence.capture(draft.tenant_id,(ObservedSource(book),ObservedSource(payment),ObservedSource(policy,book)))
        draft=seal(with_source_evidence(draft,evidence))
    else:
        from airport_assistant.modules.drafts.models import displayed_summary
        draft=seal(replace(draft,displayed_summary=displayed_summary(draft.action_type,draft.payload,draft.destination,draft.language)))
    state=dict(draft=draft,conversation=conv,active=True,now=draft.created_at,parent=None,files=[],after_commit=None)
    counts=dict(commits=0,rollbacks=0,writes=0);in_tx=False
    tx.find.side_effect=lambda *a,**kw:deepcopy(state['conversation'])
    tx.find_draft.side_effect=lambda *a:deepcopy(state['draft'])
    tx.session_active.side_effect=lambda:state['active']
    tx.now.side_effect=lambda:state['now']
    tx.lock_draft_sources.side_effect=lambda *a:tuple([state['parent']['id']] if state['parent'] else [])
    tx.source_case_context=AsyncMock(side_effect=lambda *a:deepcopy(state['parent']))
    tx.selected_attachment_context=AsyncMock(side_effect=lambda *a:deepcopy(state['files']))
    tx.validate_attachments=AsyncMock(return_value=True)
    async def edited(value):
        counts['writes']+=1;state['draft']=value
        return value
    async def terminal(id,target):
        counts['writes']+=1
        state['draft']=replace(state['draft'],state='expired' if state['now']>=state['draft'].expires_at else target)
        return state['draft']
    tx.edit_draft.side_effect=edited;tx.terminal.side_effect=terminal
    class Repository:
        @asynccontextmanager
        async def transaction(self,context):
            nonlocal in_tx
            assert not in_tx
            in_tx=True;saved=deepcopy(state)
            try:yield tx
            except BaseException:
                state.clear();state.update(saved);counts['rollbacks']+=1
                raise
            else:
                counts['commits']+=1
                if state['after_commit'] is not None:state['after_commit']()
            finally:in_tx=False
    async def grant(**kwargs):
        assert not in_tx
        if isinstance(allowed,Exception):raise allowed
        return allowed
    access=SimpleNamespace(allows=AsyncMock(side_effect=grant))
    return identity,state,counts,tx,access,DraftService(Repository(),access)


def command(service,identity,state,method,payload=None):
    draft=state['draft']
    if method=='cancel':return asyncio.run(service.cancel(identity,draft.id))
    return asyncio.run(service.edit(identity,draft.id,1,payload or draft.payload.model_copy(update={'summary':'Corrected report'})))


@pytest.mark.parametrize('method',['edit','cancel'])
def test_positive_private_command_checks_all_scopes_and_preserves_snapshot(method):
    identity,state,counts,tx,access,service=setup();original=state['draft']
    result=command(service,identity,state,method)
    assert result.source_evidence==original.source_evidence
    assert result.resource_security_labels==original.resource_security_labels
    assert len(access.allows.await_args.kwargs['labels'].labels)==2
    assert counts==dict(commits=2,rollbacks=0,writes=1)
    assert result.state==('cancelled' if method=='cancel' else 'pending')
    if method=='edit':assert result.version==2 and result.payload_hash!=original.payload_hash


@pytest.mark.parametrize('method',['edit','cancel'])
@pytest.mark.parametrize('allowed',[False,None,'true',SourceFailure(503)])
def test_denied_or_unavailable_private_command_does_not_even_expire_the_draft(method,allowed):
    identity,state,counts,tx,access,service=setup(allowed=allowed)
    state['now']=state['draft'].expires_at
    with pytest.raises((AuthError,SourceFailure)):command(service,identity,state,method)
    assert counts['writes']==0 and state['draft'].state=='pending'
    tx.terminal.assert_not_awaited()


@pytest.mark.parametrize('method',['edit','cancel'])
def test_expiration_after_permission_commits_marker_before_returning_410(method):
    identity,state,counts,tx,access,service=setup();state['now']=state['draft'].expires_at
    with pytest.raises(DraftError) as error:command(service,identity,state,method)
    assert error.value.status==410 and state['draft'].state=='expired'
    assert counts['commits']==2 and counts['rollbacks']==0


def test_cancel_replay_still_rechecks_permission_and_never_writes_twice():
    identity,state,counts,tx,access,service=setup()
    first=command(service,identity,state,'cancel')
    assert command(service,identity,state,'cancel')==first
    assert counts['writes']==1 and access.allows.await_count==2
    access.allows.side_effect=None;access.allows.return_value=False
    with pytest.raises(AuthError):command(service,identity,state,'cancel')
    assert counts['writes']==1


@pytest.mark.parametrize('method',['edit','cancel'])
@pytest.mark.parametrize('change',['hash','owner','session'])
def test_source_wait_cannot_authorize_a_changed_basis_or_identity(method,change):
    identity,state,counts,tx,access,service=setup()
    async def changed(**kwargs):
        if change=='hash':state['draft']=seal(replace(state['draft'],version=2))
        elif change=='owner':state['draft']=seal(replace(state['draft'],actor_principal_id=uuid4()))
        else:state['active']=False
        return True
    access.allows.side_effect=changed
    with pytest.raises((DraftError,AuthError)):command(service,identity,state,method)
    assert counts['writes']==0


def test_private_reference_change_requires_new_server_review_not_old_evidence_reuse():
    identity,state,counts,tx,access,service=setup();original=state['draft']
    with pytest.raises(DraftError) as error:
        command(service,identity,state,'edit',original.payload.model_copy(update={'transaction_reference':'OTHER'}))
    assert error.value.status==422 and state['draft']==original and counts['writes']==0


@pytest.mark.parametrize('method',['edit','cancel'])
def test_session_revoked_by_late_write_rolls_back_private_mutation(method):
    identity,state,counts,tx,access,service=setup();original=state['draft']
    write=tx.edit_draft if method=='edit' else tx.terminal;original_write=write.side_effect
    async def revoked(*args):
        result=await original_write(*args);state['active']=False
        return result
    write.side_effect=revoked
    with pytest.raises(AuthError):command(service,identity,state,method)
    assert state['draft']==original and counts['rollbacks']==1


@pytest.mark.parametrize('method',['edit','cancel'])
def test_public_command_stays_one_transaction_without_source_dependency(method):
    identity,state,counts,tx,access,service=setup(private=False)
    command(service,identity,state,method)
    assert counts==dict(commits=1,rollbacks=0,writes=1)
    access.allows.assert_not_awaited()


def test_parent_scope_missing_from_stored_draft_cannot_be_ignored():
    identity,state,counts,tx,access,service=setup(private=False)
    extra=dict(source='reference',resource_type='booking',reference='OTHER',required_source_scopes=['booking:read'])
    state['conversation']=replace(state['conversation'],resource_security_labels=(extra,))
    with pytest.raises(DraftError):command(service,identity,state,'cancel')
    assert counts['writes']==0
    access.allows.assert_not_awaited()


def test_new_case_binding_after_discovery_is_rejected_without_late_parent_lock():
    identity,state,counts,tx,access,service=setup(private=False)
    async def discover(id):
        state['conversation']=replace(state['conversation'],case_id=uuid4(),case_access_verified=True)
        return ()
    tx.lock_draft_sources.side_effect=discover
    with pytest.raises(DraftError):command(service,identity,state,'cancel')
    tx.source_case_context.assert_not_awaited()
    tx.lock_draft_case.assert_not_awaited()
    assert counts['writes']==0


def test_slow_edit_commit_is_unknown_and_get_recovers_without_a_second_edit():
    identity,state,counts,tx,access,service=setup();clock=[0.0]
    service._private_content.clock=lambda:clock[0]
    state['after_commit']=lambda:clock.__setitem__(0,3.0) if counts['commits']>=2 else None
    with pytest.raises(SourceFailure):command(service,identity,state,'edit')
    assert state['draft'].version==2 and counts['writes']==1
    recovered=asyncio.run(service.get(identity,state['draft'].id))
    assert recovered.version==2 and recovered.payload.summary=='Corrected report'
    assert counts['writes']==1


def test_sql_locks_all_discovered_cases_once_in_sorted_order_before_children():
    from airport_assistant.infrastructure.database.draft_repository import DraftSQL
    identity,state,*_=setup();ids=sorted([uuid4(),uuid4()])
    first=SimpleNamespace(fetchone=AsyncMock(return_value=(ids[1],str(ids[0]),ids[1],True,[],[])))
    second=SimpleNamespace(fetchall=AsyncMock(return_value=[(id,) for id in ids]))
    connection=SimpleNamespace(execute=AsyncMock(side_effect=[first,second]))
    unit=DraftSQL(connection,'app',identity.database_context)
    assert asyncio.run(unit.lock_draft_sources(state['draft'].id))==tuple(ids)
    query,parameters=connection.execute.await_args.args
    assert 'ORDER BY id FOR UPDATE' in query.as_string() and parameters[-1]==ids


def test_sql_cannot_accept_a_partial_set_of_visible_parent_cases():
    from airport_assistant.infrastructure.database.draft_repository import DraftSQL
    identity,state,*_=setup();first=SimpleNamespace(fetchone=AsyncMock(return_value=(uuid4(),None,None,True,[],[])))
    second=SimpleNamespace(fetchall=AsyncMock(return_value=[]))
    connection=SimpleNamespace(execute=AsyncMock(side_effect=[first,second]))
    with pytest.raises(AuthError):
        asyncio.run(DraftSQL(connection,'app',identity.database_context).lock_draft_sources(state['draft'].id))


@pytest.mark.parametrize('private',[False,True])
def test_historical_schema_without_update_privilege_never_enables_private_locks(private):
    from airport_assistant.infrastructure.database.draft_repository import DraftSQL
    identity,state,*_=setup();id=uuid4()
    first=SimpleNamespace(fetchone=AsyncMock(return_value=(id,None,None,False,[],[])))
    second=SimpleNamespace(fetchall=AsyncMock(return_value=[(id,list(state['draft'].resource_security_labels) if private else [])]))
    connection=SimpleNamespace(execute=AsyncMock(side_effect=[first,second]))
    operation=DraftSQL(connection,'app',identity.database_context).lock_draft_sources(state['draft'].id)
    if private:
        with pytest.raises(AuthError):asyncio.run(operation)
    else:assert asyncio.run(operation)==(id,)
    assert 'FOR UPDATE' not in connection.execute.await_args.args[0].as_string()


@pytest.mark.parametrize('private_child',['draft','conversation'])
def test_missing_lock_privilege_cannot_enable_a_private_child_of_a_public_parent(private_child):
    from airport_assistant.infrastructure.database.draft_repository import DraftSQL
    identity,state,*_=setup();labels=list(state['draft'].resource_security_labels)
    first=SimpleNamespace(fetchone=AsyncMock(return_value=(uuid4(),None,None,False,
        labels if private_child=='draft' else [],labels if private_child=='conversation' else [])))
    connection=SimpleNamespace(execute=AsyncMock(return_value=first))
    with pytest.raises(AuthError):
        asyncio.run(DraftSQL(connection,'app',identity.database_context).lock_draft_sources(state['draft'].id))
    assert connection.execute.await_count==1
