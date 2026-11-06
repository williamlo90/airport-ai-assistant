"""Atomic read-port semantics retain two independent grant-bound projections."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from test_private_conversations import setup,read
from airport_assistant.modules.conversations.service import CustomerReadProjection
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure


def optimized():
    identity,id,state,parent,message,tx,access,service=setup()
    async def packet(id,kind,**kwargs):
        data=message if kind=='message' else (message,) if kind=='history' else None
        return CustomerReadProjection(deepcopy(state['conversation']),deepcopy(data),deepcopy(parent) if parent else None,True)
    tx.customer_read_projection=AsyncMock(side_effect=packet)
    return identity,id,state,parent,message,tx,access,service


@pytest.mark.parametrize('method',['get','customer_message','customer_history'])
def test_atomic_port_runs_twice_across_grants_with_live_entry_exit_sessions(method):
    identity,id,state,parent,message,tx,access,service=optimized()
    read(service,identity,id,message,method)
    assert tx.customer_read_projection.await_count==2
    assert tx.session_active.await_count==4
    tx.find.assert_not_awaited()
    access.allows.assert_awaited_once()


@pytest.mark.parametrize('change',['version','epoch','labels','owner','parent_link','parent_version','missing_parent','message_body'])
def test_committed_change_during_grants_cannot_return_old_private_materialization(change):
    identity,id,state,parent,message,tx,access,service=optimized()
    async def mutate(**kwargs):
        if change=='version':state['conversation']=replace(state['conversation'],version=2)
        elif change=='epoch':state['conversation']=replace(state['conversation'],generation_epoch=2)
        elif change=='labels':state['conversation']=replace(state['conversation'],resource_security_labels=())
        elif change=='owner':state['conversation']=replace(state['conversation'],customer_principal_id=identity.session.id)
        elif change=='parent_link':state['conversation']=replace(state['conversation'],case_id=identity.session.id)
        elif change=='parent_version':parent['version']+=1
        elif change=='missing_parent':parent.clear()
        elif change=='message_body':
            original=tx.customer_read_projection.side_effect
            async def altered(*args,**kwargs):
                packet=await original(*args,**kwargs)
                return replace(packet,data=replace(message,body='Changed private report'))
            tx.customer_read_projection.side_effect=altered
        return True
    access.allows.side_effect=mutate
    with pytest.raises((AuthError,DraftError)):
        read(service,identity,id,message,'customer_message')


def test_post_grant_transaction_exit_still_expires_at_two_seconds():
    identity,id,state,parent,message,tx,access,service=optimized()
    times=iter([0.0,2.01]);service._private_content.clock=lambda:next(times)
    with pytest.raises(SourceFailure):read(service,identity,id,message,'get')
    assert tx.session_active.await_count==4


def test_session_loss_on_second_exit_blocks_projection():
    identity,id,state,parent,message,tx,access,service=optimized()
    tx.session_active.side_effect=[True,True,True,False]
    with pytest.raises(AuthError) as error:read(service,identity,id,message,'get')
    assert error.value.status==401
