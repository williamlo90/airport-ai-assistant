"""Customer append re-read must preserve the object authorization boundary."""
import asyncio
from dataclasses import replace
from uuid import uuid4
import pytest
from test_conversation_rules import setup
from airport_assistant.modules.conversations.service import ConversationError
from airport_assistant.modules.identity.auth import AuthError


def test_authorized_new_case_after_locked_miss_is_conflict_without_write():
    async def scenario():
        identity, conversation, tx, service = setup()
        current = replace(conversation, case_id=uuid4(), mode='waiting_staff', case_access_verified=True)
        tx.find.side_effect = [None, current]
        with pytest.raises(ConversationError) as error:
            await service.append(identity, conversation.id, uuid4(), 'Keep this draft')
        assert error.value.status == 409 and error.value.code == 'VERSION_CONFLICT'
        tx.duplicate.assert_not_awaited(); tx.append.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('change', [
    {'anonymous_session_id': uuid4()}, {'tenant_id': uuid4()}, {'id': uuid4()},
    {'resource_security_labels': ({'source': 'private'},)}, {'case_access_verified': False},
])
def test_foreign_or_unproven_fresh_context_stays_404_without_write(change):
    async def scenario():
        identity, conversation, tx, service = setup()
        current = replace(conversation, case_id=uuid4(), mode='waiting_staff', case_access_verified=True)
        tx.find.side_effect = [None, replace(current, **change)]
        with pytest.raises(AuthError) as error:
            await service.append(identity, conversation.id, uuid4(), 'Do not send across context')
        assert error.value.status == 404
        tx.duplicate.assert_not_awaited(); tx.append.assert_not_awaited()
    asyncio.run(scenario())
