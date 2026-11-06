"""No private transcript/message reaches the caller or model before fresh grants."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.conversations.service import Conversation, ConversationService, Message
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from test_private_content import actor, records


def setup(private=True, parent=True, allowed=True):
    identity, id = actor(), uuid4()
    state = {'conversation': Conversation(id, identity.session.tenant_id, identity.session.principal_id,
        None, 'en', 1, 1, 1, case_id=uuid4() if parent else None,
        resource_security_labels=tuple(records()[:1]) if private else (),
        case_access_verified=parent)}
    case = dict(id=state['conversation'].case_id, version=1,
                resource_security_labels=records()[1:] if private else [])
    message = Message(uuid4(), 1, 'Customer report with protected context', 'en')
    active = False
    tx = SimpleNamespace(session_active=AsyncMock(return_value=True),
        find=AsyncMock(side_effect=lambda *a, **k: deepcopy(state['conversation'])),
        source_case_context=AsyncMock(side_effect=lambda id: deepcopy(case)),
        customer_message=AsyncMock(return_value=message), customer_history=AsyncMock(return_value=(message,)))
    class Repository:
        @asynccontextmanager
        async def transaction(self, context):
            nonlocal active
            active = True
            try:
                yield tx
            finally:
                active = False
    async def allows(**kwargs):
        assert not active
        if isinstance(allowed, Exception):
            raise allowed
        return allowed
    access = SimpleNamespace(allows=AsyncMock(side_effect=allows))
    return identity, id, state, case, message, tx, access, ConversationService(Repository(), access)


def read(service, identity, id, message, method):
    args = (identity, id, message.id) if method == 'customer_message' else (identity, id)
    return asyncio.run(getattr(service, method)(*args))


@pytest.mark.parametrize('method', ['get', 'customer_message', 'customer_history'])
def test_positive_read_checks_conversation_and_parent_union_before_emission(method):
    identity, id, state, case, message, tx, access, service = setup()
    value = read(service, identity, id, message, method)
    assert len(access.allows.await_args.kwargs['labels'].labels) == 2
    assert tx.find.await_count == 6
    expected = (state['conversation'], message) if method == 'customer_message' else (message,) if method == 'customer_history' else state['conversation']
    assert value == expected


@pytest.mark.parametrize('method', ['get', 'customer_message', 'customer_history'])
@pytest.mark.parametrize('allowed', [False, SourceFailure(503)])
def test_denied_or_unavailable_source_cannot_return_transcript(method, allowed):
    identity, id, state, case, message, tx, access, service = setup(allowed=allowed)
    with pytest.raises((AuthError, SourceFailure)):
        read(service, identity, id, message, method)
    assert tx.find.await_count == 3


@pytest.mark.parametrize('method', ['get', 'customer_message', 'customer_history'])
def test_parent_version_changed_during_source_await_rejects_old_context(method):
    identity, id, state, case, message, tx, access, service = setup()
    async def changed(**kwargs):
        case['version'] += 1
        return True
    access.allows.side_effect = changed
    with pytest.raises(DraftError) as error:
        read(service, identity, id, message, method)
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'


def test_public_to_private_during_transcript_fetch_requires_new_source_permission():
    identity, id, state, case, message, tx, access, service = setup(private=False, allowed=False)
    async def changed(*args):
        state['conversation'] = replace(state['conversation'], version=2,
            resource_security_labels=tuple(records()))
        return (message,)
    tx.customer_history.side_effect = changed
    with pytest.raises(AuthError):
        read(service, identity, id, message, 'customer_history')
    access.allows.assert_awaited_once()


def test_source_grant_snapshot_does_not_survive_session_revocation():
    identity, id, state, case, message, tx, access, service = setup()
    async def revoked(**kwargs):
        tx.session_active.return_value = False
        return True
    access.allows.side_effect = revoked
    with pytest.raises(AuthError) as error:
        read(service, identity, id, message, 'customer_history')
    assert error.value.status == 401
    assert tx.find.await_count == 3


@pytest.mark.parametrize('change', ['foreign_owner', 'foreign_tenant', 'unverified_parent', 'missing_parent'])
def test_current_ownership_and_parent_acl_are_required_before_source_checks(change):
    identity, id, state, case, message, tx, access, service = setup()
    if change == 'missing_parent':
        tx.source_case_context.side_effect = None
        tx.source_case_context.return_value = None
    else:
        field = {'foreign_owner': 'customer_principal_id', 'foreign_tenant': 'tenant_id',
                 'unverified_parent': 'case_access_verified'}[change]
        state['conversation'] = replace(state['conversation'], **{field: False if change == 'unverified_parent' else uuid4()})
    with pytest.raises(AuthError):
        read(service, identity, id, message, 'get')
    access.allows.assert_not_awaited()


def test_unlabeled_customer_report_needs_no_source_provider():
    identity, id, state, case, message, tx, access, service = setup(private=False, parent=False)
    service._private_content.source_access = None
    assert read(service, identity, id, message, 'customer_history') == (message,)
    access.allows.assert_not_awaited()


def test_public_head_can_advance_during_parent_lookup_without_false_source_failure():
    identity, id, state, case, message, tx, access, service = setup(private=False, allowed=False)
    async def changed(case_id):
        state['conversation'] = replace(state['conversation'],version=2)
        return deepcopy(case)
    tx.source_case_context.side_effect = changed
    assert read(service,identity,id,message,'get').version == 2
    access.allows.assert_not_awaited()
