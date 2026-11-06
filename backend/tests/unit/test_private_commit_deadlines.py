"""Q08: no awaited exit-session gap before a private write commits."""
import asyncio,copy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest

from airport_assistant.modules.identity.customer_transaction import customer_transaction
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection
from airport_assistant.modules.conversations.disclosure import RecipientDisclosureGate,RecipientContext,DisclosureCandidate
from test_staff_disclosure import labels


class Store:
    def __init__(self,private=True):
        self.state=dict(body='original',version=1,labels=list(labels()) if private else [])
        self.clock=0.;self.in_tx=False;self.commits=0;self.rollbacks=0
        self.exit_time=None;self.after_commit=None;self.exit_verdict=True
    @asynccontextmanager
    async def transaction(self,context):
        assert not self.in_tx;self.in_tx=True;before=copy.deepcopy(self.state)
        try:yield self
        except BaseException:
            self.state=before;self.rollbacks+=1;raise
        else:
            self.commits+=1
            if self.state['body']=='changed' and self.after_commit:self.after_commit()
        finally:self.in_tx=False
    async def session_active(self):
        if self.state['body']=='changed':
            if self.exit_time is not None:self.clock=self.exit_time
            return self.exit_verdict
        return True


def setup(kind='customer',private=True):
    identity=SimpleNamespace(session=SimpleNamespace(kind=kind,tenant_id=uuid4(),id=uuid4(),principal_id=uuid4()),
        memberships=(object(),) if kind=='staff' else (),database_context=None)
    store=Store(private);id=uuid4();recipient=uuid4()
    async def allowed(**kw):
        assert not store.in_tx
        assert kw['principal_id']==(recipient if kind=='staff' else identity.session.principal_id)
        return True
    access=SimpleNamespace(allows=AsyncMock(side_effect=allowed))
    gate=(RecipientDisclosureGate(access,clock=lambda:store.clock) if kind=='staff'
        else PrivateContentGate(access,clock=lambda:store.clock))
    def transaction(*,before_commit):
        if kind=='staff':return lifecycle_transaction(store,identity,staff=True,before_commit=before_commit)
        return customer_transaction(store,identity,before_commit=before_commit)
    async def loader(tx):
        projection=PrivateProjection.capture(identity.session.tenant_id,id,value=copy.deepcopy(store.state),
            basis=store.state,records=store.state['labels'])
        if kind=='staff':return DisclosureCandidate(projection,RecipientContext(identity.session.tenant_id,recipient,{}))
        return projection
    return identity,store,access,gate,transaction,loader


@pytest.mark.parametrize('kind',['customer','staff'])
def test_true_exit_session_crossing_grant_deadline_rolls_back(kind):
    async def run():
        identity,store,access,gate,transaction,loader=setup(kind);store.exit_time=3.
        with pytest.raises(SourceFailure):
            if kind=='staff':
                async with gate.mutation(identity,transaction,loader,loader):store.state.update(body='changed',version=2)
            else:
                async with gate.mutation(identity,transaction,loader):store.state.update(body='changed',version=2)
        assert store.state['body']=='original' and store.commits==1 and store.rollbacks==1 and not store.in_tx
    asyncio.run(run())


@pytest.mark.parametrize('kind',['customer','staff'])
def test_public_one_unit_keeps_no_source_call_even_with_late_session(kind):
    async def run():
        identity,store,access,gate,transaction,loader=setup(kind,private=False);store.exit_time=3.
        if kind=='staff':
            async with gate.mutation(identity,transaction,loader,loader):store.state.update(body='changed',version=2)
        else:
            async with gate.mutation(identity,transaction,loader):store.state.update(body='changed',version=2)
        assert store.state['body']=='changed' and store.commits==1 and not store.in_tx
        access.allows.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('kind',['customer','staff'])
def test_slow_commit_holds_response_but_keeps_single_stored_operation(kind):
    async def run():
        identity,store,access,gate,transaction,loader=setup(kind)
        store.after_commit=lambda:setattr(store,'clock',3.)
        with pytest.raises(SourceFailure):
            if kind=='staff':
                async with gate.mutation(identity,transaction,loader,loader):store.state.update(body='changed',version=2)
            else:
                async with gate.mutation(identity,transaction,loader):store.state.update(body='changed',version=2)
        assert store.state['body']=='changed' and store.state['version']==2 and store.commits==2 and store.rollbacks==0
    asyncio.run(run())


@pytest.mark.parametrize('kind',['customer','staff'])
def test_session_loss_and_cancellation_rollback_cleanup_without_hook_bypass(kind):
    async def run():
        identity,store,access,gate,transaction,loader=setup(kind)
        store.exit_verdict=False
        with pytest.raises(AuthError) as error:
            async with transaction(before_commit=lambda:None):store.state['body']='changed'
        assert error.value.status==401 and store.state['body']=='original' and not store.in_tx
        store.exit_verdict=True
        with pytest.raises(asyncio.CancelledError):
            async with transaction(before_commit=lambda:None):
                store.state['body']='changed'
                raise asyncio.CancelledError()
        assert store.state['body']=='original' and not store.in_tx and store.rollbacks==2
    asyncio.run(run())


@pytest.mark.parametrize('kind',['customer','staff'])
def test_async_before_commit_hook_is_rejected_without_await_or_commit(kind):
    async def run():
        identity,store,access,gate,transaction,loader=setup(kind)
        async def forbidden():raise AssertionError('Async hook must not run')
        with pytest.raises(TypeError):
            async with transaction(before_commit=forbidden):store.state['body']='changed'
        assert store.state['body']=='original' and store.commits==0
    asyncio.run(run())


def test_unsupported_old_factory_does_not_get_an_unguarded_fallback():
    async def run():
        identity,store,access,gate,transaction,loader=setup()
        @asynccontextmanager
        async def old_factory():
            raise AssertionError('Old factory must not be entered')
            yield
        with pytest.raises(TypeError):
            async with gate.mutation(identity,old_factory,loader):raise AssertionError('No write')
        assert store.state['body']=='original' and store.commits==0
        access.allows.assert_not_awaited()
    asyncio.run(run())


def test_bridge_keeps_earlier_collection_anchor_after_later_grants_and_exit_await():
    async def run():
        from test_agent_private_bridge import planned
        f,store,model,engine,id=await planned();work=await engine.load(f.identity,id)
        clock=[0.];engine.private_drafts.clock=lambda:clock[0];engine.checkpoints.gate.clock=lambda:clock[0]
        collected=False;reads=0
        original_collect=engine.private_drafts.collect
        async def collect(*args):
            nonlocal collected
            result=await original_collect(*args);collected=True;return result
        engine.private_drafts.collect=collect
        async def grants(**kw):
            assert not store.in_tx
            if collected:clock[0]=.75
            return True
        f.private.allows.side_effect=grants
        original_find=store.find_draft
        async def find(id):
            nonlocal reads
            result=await original_find(id)
            if store.rows[work.run['id']]['phase']=='done':reads+=1
            return result
        store.find_draft=find
        original_session=store.session_active
        async def exit_session():
            result=await original_session()
            if reads>=2 and store.rows[id]['phase']=='done':clock[0]=2.25
            return result
        store.session_active=exit_session
        with pytest.raises(SourceFailure):await engine.draft(work)
        assert reads==2 and clock[0]==2.25  # grants age1.5, source facts age2.25
        assert not store.drafts and store.rows[id]['phase']=='draft' and store.rows[id]['result_draft_id'] is None
    asyncio.run(run())
