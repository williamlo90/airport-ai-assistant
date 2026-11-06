"""Source authorization between DB units; failed guards roll writes back."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.source.private_content import PrivateProjection, PrivateContentGate
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from test_private_content import actor, records


def setup(private=True, allowed=True):
    identity, id = actor(), uuid4()
    state = dict(version=1, labels=records() if private else [], body='original')
    active = False
    counts = {'commits': 0, 'rollbacks': 0, 'writes': 0}
    clock = [0.0]
    after_commit = None
    @asynccontextmanager
    async def transaction(*,before_commit=None):
        nonlocal active
        assert not active
        active = True
        saved = deepcopy(state)
        try:
            yield state
            if before_commit is not None:before_commit()
        except BaseException:
            state.clear(); state.update(saved)
            counts['rollbacks'] += 1
            raise
        else:
            counts['commits'] += 1
            if counts['commits'] == 2 and hooks.after_commit:
                hooks.after_commit()
        finally:
            active = False
    async def loader(tx):
        assert active
        return PrivateProjection.capture(identity.session.tenant_id,id,value=tx['body'],
            basis=deepcopy(tx),records=deepcopy(tx['labels']))
    async def grants(**kwargs):
        assert not active
        return allowed
    access = SimpleNamespace(allows=AsyncMock(side_effect=grants))
    gate = PrivateContentGate(access,clock=lambda:clock[0])
    hooks = SimpleNamespace(after_commit=after_commit)
    return identity,id,state,counts,clock,hooks,access,gate,transaction,loader


def test_positive_write_uses_fresh_scope_outside_db_and_checks_after_mutation():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup()
    async def run():
        async with gate.mutation(identity,transaction,loader) as tx:
            counts['writes']+=1;tx.update(body='changed',version=2)
    asyncio.run(run())
    assert state['body']=='changed' and counts==dict(commits=2,rollbacks=0,writes=1)
    access.allows.assert_awaited_once()


@pytest.mark.parametrize('verdict',[False,None,1,'true',{'allowed':True}])
def test_denied_or_malformed_source_result_never_enters_write(verdict):
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup(allowed=verdict)
    async def run():
        async with gate.mutation(identity,transaction,loader):
            raise AssertionError('Write cannot start')
    with pytest.raises(AuthError):asyncio.run(run())
    assert state['body']=='original' and counts['commits']==1


def test_state_change_during_grant_wait_does_not_silently_authorize_new_basis():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup()
    async def changed(**kwargs):
        state['version']=2
        return True
    access.allows.side_effect=changed
    async def run():
        async with gate.mutation(identity,transaction,loader):
            raise AssertionError('Stale write')
    with pytest.raises(DraftError):asyncio.run(run())
    assert state['version']==2 and counts['rollbacks']==1


@pytest.mark.parametrize('new_labels',[[],records()[:1],records()+[dict(source='reference',resource_type='booking',reference='OTHER',required_source_scopes=['booking:read'])]])
def test_removing_or_adding_unapproved_requirements_rolls_back_whole_write(new_labels):
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup()
    async def run():
        async with gate.mutation(identity,transaction,loader) as tx:
            tx.update(labels=new_labels,body='must roll back',version=2)
    with pytest.raises(DraftError):asyncio.run(run())
    assert state['body']=='original' and state['labels']==records()


def test_private_write_expiring_before_commit_is_rolled_back():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup()
    async def run():
        async with gate.mutation(identity,transaction,loader) as tx:
            tx.update(body='not committed',version=2)
            clock[0]=3
    with pytest.raises(SourceFailure):asyncio.run(run())
    assert state['body']=='original' and counts['rollbacks']==1


def test_slow_commit_withholds_result_without_replaying_business_work():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup()
    hooks.after_commit=lambda:clock.__setitem__(0,3)
    async def run():
        async with gate.mutation(identity,transaction,loader) as tx:
            counts['writes']+=1;tx.update(body='committed',version=2)
    with pytest.raises(SourceFailure):asyncio.run(run())
    assert state['body']=='committed' and counts['writes']==1
    async def read_status():
        async def load():
            async with transaction() as tx:return await loader(tx)
        return await gate.read(identity,load)
    assert asyncio.run(read_status())=='committed'
    assert counts['writes']==1


def test_new_trusted_source_scopes_are_checked_before_creation_and_required_after():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup(private=False)
    addition=SecurityLabels.from_records(identity.session.tenant_id,records())
    async def run():
        async with gate.mutation(identity,transaction,loader,additional_labels=addition) as tx:
            tx.update(labels=addition.to_records(),body='new source facts',version=2)
    asyncio.run(run())
    assert access.allows.await_args.kwargs['labels']==addition
    assert state['labels']==addition.to_records()


def test_new_child_is_reloaded_under_parent_identity_before_commit():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup(private=False)
    addition=SecurityLabels.from_records(identity.session.tenant_id,records())
    child={}
    async def child_loader(tx):
        return PrivateProjection.capture(identity.session.tenant_id,id,value=child,
            basis={'parent':tx,'child':child},records=child['labels'])
    async def run():
        async with gate.mutation(identity,transaction,loader,additional_labels=addition,after_loader=child_loader):
            child.update(id=uuid4(),labels=addition.to_records())
    asyncio.run(run())
    assert counts['commits']==2


def test_foreign_additional_labels_cannot_authorize_another_tenant():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup(private=False)
    foreign=SecurityLabels.from_records(uuid4(),records())
    async def run():
        async with gate.mutation(identity,transaction,loader,additional_labels=foreign):
            raise AssertionError('Cannot enter foreign scope')
    with pytest.raises(ValueError):asyncio.run(run())
    access.allows.assert_not_awaited()


def test_report_only_public_write_retains_one_atomic_transaction():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup(private=False)
    async def run():
        async with gate.mutation(identity,transaction,loader) as tx:
            tx.update(body='public report',version=2)
    asyncio.run(run())
    assert counts['commits']==1 and state['body']=='public report'
    access.allows.assert_not_awaited()


def test_public_write_cannot_silently_create_a_private_aggregate():
    identity,id,state,counts,clock,hooks,access,gate,transaction,loader=setup(private=False)
    async def run():
        async with gate.mutation(identity,transaction,loader) as tx:
            tx.update(body='unapproved source',labels=records(),version=2)
    with pytest.raises(DraftError):asyncio.run(run())
    assert state['body']=='original' and state['labels']==[] and counts['rollbacks']==1
