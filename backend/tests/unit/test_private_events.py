"""Source authorization precedes cursor parsing and durable event disclosure."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.conversations.events import ConversationEvents, EventCursor
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from test_private_content import actor, records


def setup(private=True, grant=True):
    identity, id = actor(), uuid4()
    state = dict(last_event_sequence=1, mode='ai', generation_epoch=1, version=2,
        resource_security_labels=records()[:1] if private else [], case_id=uuid4(),
        case_security_labels=records()[1:] if private else [], case_version=1)
    in_transaction = False
    tx = SimpleNamespace(session_active=AsyncMock(return_value=True),
        event_conversation=AsyncMock(side_effect=lambda *a: deepcopy(state)),
        events=AsyncMock(return_value=[dict(id=uuid4(), sequence=1, event_type='ai_message',
            payload=dict(body='PRIVATE PAYMENT', message_id=uuid4(), language='en', author_type='ai'))]))
    class Repository:
        @asynccontextmanager
        async def transaction(self, context):
            nonlocal in_transaction
            in_transaction = True
            try:
                yield tx
            finally:
                in_transaction = False
    async def check(**kwargs):
        assert not in_transaction
        assert len(kwargs['labels'].labels) == 2
        if isinstance(grant, Exception):
            raise grant
        return grant
    access = SimpleNamespace(allows=AsyncMock(side_effect=check))
    service = ConversationEvents(Repository(), EventCursor(b'test-only-cursor'), access)
    return identity, id, state, tx, access, service


@pytest.mark.parametrize('grant', [False, SourceFailure(503)])
def test_denied_source_cannot_probe_bad_cursor_or_read_events(grant):
    identity, id, state, tx, access, service = setup(grant=grant)
    with pytest.raises((AuthError, SourceFailure)) as error:
        asyncio.run(service.poll(identity, id, after='invalid-cursor'))
    assert error.value.status in {404, 503}
    tx.events.assert_not_awaited()
    assert tx.event_conversation.await_count == 1


def test_positive_event_projection_uses_union_of_parent_and_conversation_grants():
    identity, id, state, tx, access, service = setup()
    result = asyncio.run(service.poll(identity, id))
    assert result['items'][0]['payload']['body'] == 'PRIVATE PAYMENT'
    assert set(result) == {'items', 'next_cursor', 'has_more', 'mode', 'generation_epoch', 'version'}
    assert tx.event_conversation.await_count == 3
    assert service._cursors.read(result['next_cursor'], service._cursors.scope(identity, id, 20)) == 1


def test_case_label_or_version_change_during_grant_wait_is_checked_before_cursor():
    identity, id, state, tx, access, service = setup()
    async def changed(**kwargs):
        state['case_version'] += 1
        return True
    access.allows.side_effect = changed
    with pytest.raises(DraftError) as error:
        asyncio.run(service.poll(identity, id, after='invalid-cursor'))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'
    tx.events.assert_not_awaited()


def test_expired_check_after_database_wait_is_rejected_before_cursor():
    identity, id, state, tx, access, service = setup()
    ticks = iter([0, 3])
    service._private_content.clock = lambda: next(ticks)
    with pytest.raises(SourceFailure) as error:
        asyncio.run(service.poll(identity, id, after='invalid-cursor'))
    assert error.value.status == 503
    tx.events.assert_not_awaited()


def test_public_to_private_change_is_not_disclosed_without_new_source_check():
    identity, id, state, tx, access, service = setup(private=False)
    before = deepcopy(state)
    state['case_security_labels'] = records()
    tx.event_conversation.side_effect = [before, deepcopy(state)]
    with pytest.raises(DraftError) as error:
        asyncio.run(service.poll(identity, id, after='invalid-cursor'))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'
    tx.events.assert_not_awaited()
    access.allows.assert_not_awaited()


def test_live_public_poll_can_observe_new_messages_without_spurious_source_conflict():
    identity, id, state, tx, access, service = setup(private=False)
    before = deepcopy(state)
    state.update(version=3, last_event_sequence=2)
    tx.event_conversation.side_effect = [before, deepcopy(state), deepcopy(state)]
    result = asyncio.run(service.poll(identity, id))
    assert result['version'] == 3
    access.allows.assert_not_awaited()


@pytest.mark.parametrize('failure', [None, AuthError(401, 'AUTH_REQUIRED')])
def test_missing_parent_or_revoked_session_after_source_check_cannot_emit(failure):
    identity, id, state, tx, access, service = setup()
    tx.event_conversation.side_effect = [deepcopy(state), failure]
    with pytest.raises(AuthError):
        asyncio.run(service.poll(identity, id))
    tx.events.assert_not_awaited()


def test_missing_parent_labels_are_not_treated_as_an_empty_public_scope():
    identity, id, state, tx, access, service = setup()
    state['case_security_labels'] = None
    with pytest.raises(AuthError):
        asyncio.run(service.poll(identity, id))
    tx.events.assert_not_awaited()
    access.allows.assert_not_awaited()
