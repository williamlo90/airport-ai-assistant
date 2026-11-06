"""Conversation commands bind source permission to one current atomic write."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.conversations.service import Conversation,ConversationService,ConversationError,Message
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from test_private_content import actor,records


def setup(private=True,allowed=True):
    identity=actor(); id=uuid4(); case_id=uuid4()
    state=dict(conversation=Conversation(id,identity.session.tenant_id,identity.session.principal_id,
        None,'en',1,1,0,case_id=case_id,case_access_verified=True,
        resource_security_labels=tuple(records()) if private else ()),
        parent=dict(id=case_id,version=1,resource_security_labels=records() if private else []),
        messages={},active=True)
    counts=dict(commits=0,rollbacks=0,writes=0)
    active=False
    tx=SimpleNamespace(session_active=AsyncMock(side_effect=lambda:state['active']),
        find=AsyncMock(side_effect=lambda *a,**k:deepcopy(state['conversation'])),
        source_case_context=AsyncMock(side_effect=lambda *a:deepcopy(state['parent'])),
        lock_conversation_case=AsyncMock(side_effect=lambda *a:state['conversation'].case_id),
        languages=AsyncMock(return_value=['en','id']))
    async def append(conv,message_id,client_id,body):
        counts['writes']+=1
        state['conversation']=replace(conv,last_sequence=conv.last_sequence+1,version=conv.version+1)
        message=Message(message_id,conv.last_sequence+1,body,conv.language)
        state['messages'][client_id]=message
        return message
    async def change_language(conv,language):
        counts['writes']+=1
        state['conversation']=replace(conv,language=language,version=conv.version+1,
            generation_epoch=conv.generation_epoch+1)
        return state['conversation']
    tx.append=AsyncMock(side_effect=append)
    tx.change_language=AsyncMock(side_effect=change_language)
    tx.duplicate=AsyncMock(side_effect=lambda id,client_id:state['messages'].get(client_id))
    class Repository:
        @asynccontextmanager
        async def transaction(self,context):
            nonlocal active
            assert not active
            active=True; saved=deepcopy(state)
            try:yield tx
            except BaseException:
                state.clear();state.update(saved);counts['rollbacks']+=1
                raise
            else:counts['commits']+=1
            finally:active=False
    async def grants(**kwargs):
        assert not active, 'No source I/O under DB locks'
        if isinstance(allowed,Exception):raise allowed
        return allowed
    access=SimpleNamespace(allows=AsyncMock(side_effect=grants))
    return identity,id,state,counts,tx,access,ConversationService(Repository(),access)


def command(service,identity,id,method,client_id=None):
    if method=='append':return asyncio.run(service.append(identity,id,client_id or uuid4(),'Customer report'))
    return asyncio.run(service.change_language(identity,id,'id',1))


@pytest.mark.parametrize('method',['append','change_language'])
def test_private_commands_require_all_scopes_outside_db_and_preserve_labels(method):
    identity,id,state,counts,tx,access,service=setup()
    value=command(service,identity,id,method)
    assert value
    assert counts==dict(commits=2,rollbacks=0,writes=1)
    assert len(access.allows.await_args.kwargs['labels'].labels)==2
    assert list(state['conversation'].resource_security_labels)==records()
    assert state['conversation'].version==2


@pytest.mark.parametrize('method',['append','change_language'])
@pytest.mark.parametrize('allowed',[False,'true',SourceFailure(503)])
def test_denied_unavailable_or_invalid_source_never_enters_command(method,allowed):
    identity,id,state,counts,tx,access,service=setup(allowed=allowed)
    with pytest.raises((AuthError,SourceFailure)):command(service,identity,id,method)
    assert counts['writes']==0 and state['conversation'].version==1
    tx.duplicate.assert_not_awaited()


def test_duplicate_receipt_is_not_a_source_permission_bypass():
    identity,id,state,counts,tx,access,service=setup();client_id=uuid4()
    original=command(service,identity,id,'append',client_id)
    assert command(service,identity,id,'append',client_id)==original
    assert counts['writes']==1 and access.allows.await_count==2
    access.allows.side_effect=None;access.allows.return_value=False
    with pytest.raises(AuthError):command(service,identity,id,'append',client_id)
    assert counts['writes']==1 and len(state['messages'])==1


@pytest.mark.parametrize('method',['append','change_language'])
@pytest.mark.parametrize('change',['parent_version','conversation_version','session','owner','case_binding'])
def test_context_or_access_change_during_grants_cannot_authorize_new_write(method,change):
    identity,id,state,counts,tx,access,service=setup()
    async def changed(**kwargs):
        if change=='parent_version':state['parent']['version']+=1
        elif change=='conversation_version':state['conversation']=replace(state['conversation'],version=2)
        elif change=='session':state['active']=False
        elif change=='owner':state['conversation']=replace(state['conversation'],customer_principal_id=uuid4())
        else:state['conversation']=replace(state['conversation'],case_id=uuid4())
        return True
    access.allows.side_effect=changed
    with pytest.raises((DraftError,AuthError)):command(service,identity,id,method)
    assert counts['writes']==0


def test_stored_child_cannot_omit_a_parent_source_scope():
    identity,id,state,counts,tx,access,service=setup()
    state['conversation']=replace(state['conversation'],resource_security_labels=tuple(records()[:1]))
    with pytest.raises(DraftError):command(service,identity,id,'append')
    assert counts['writes']==0
    access.allows.assert_not_awaited()


@pytest.mark.parametrize('method',['append','change_language'])
def test_current_session_loss_after_write_rolls_back_the_command(method):
    identity,id,state,counts,tx,access,service=setup()
    original=tx.append.side_effect if method=='append' else tx.change_language.side_effect
    async def revoked(*args):
        value=await original(*args);state['active']=False
        return value
    (tx.append if method=='append' else tx.change_language).side_effect=revoked
    with pytest.raises(AuthError):command(service,identity,id,method)
    assert state['conversation'].version==1 and state['messages']=={}
    assert counts['rollbacks']==1


@pytest.mark.parametrize('method',['append','change_language'])
def test_public_commands_remain_one_transaction_without_source_calls(method):
    identity,id,state,counts,tx,access,service=setup(private=False)
    command(service,identity,id,method)
    assert counts==dict(commits=1,rollbacks=0,writes=1)
    access.allows.assert_not_awaited()


def test_private_language_noop_still_requires_current_permission_and_version():
    identity,id,state,counts,tx,access,service=setup()
    value=asyncio.run(service.change_language(identity,id,'en',1))
    assert value.version==1 and counts['writes']==0
    access.allows.assert_awaited_once()
    with pytest.raises(ConversationError):asyncio.run(service.change_language(identity,id,'id',2))
    assert counts['writes']==0


@pytest.mark.parametrize('private',[False,True])
def test_sql_message_insert_classifies_private_rows_and_preserves_public_column_grants(private):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    identity,id,state,counts,tx,access,service=setup()
    if not private:state['conversation']=replace(state['conversation'],resource_security_labels=())
    results=iter([(1,),(uuid4(),1,'Customer report','en')])
    cursor=SimpleNamespace(fetchone=AsyncMock(side_effect=lambda:next(results)))
    connection=SimpleNamespace(execute=AsyncMock(return_value=cursor))
    unit=ConversationSQL(connection,'app',identity.database_context)
    asyncio.run(unit.append(state['conversation'],uuid4(),uuid4(),'Customer report'))
    query,parameters=connection.execute.await_args.args
    assert ('resource_security_labels' in query.as_string()) is private
    if private:assert parameters[-1].obj==records()
    else:assert len(parameters)==10
