"""A fresh read after a handoff race must retain object authorization."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from test_draft_rules import setup
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


def test_fresh_linked_conversation_read_returns_conflict_without_new_draft():
    async def scenario():
        actor, conversation, draft, tx, _, service = setup()
        linked = replace(conversation, case_id=uuid4(), mode='waiting_staff', case_access_verified=True)
        tx.find.side_effect = [None, linked]
        with pytest.raises(DraftError) as error:
            await service.create(actor, conversation.id, 'request_handoff', draft.payload)
        assert error.value.status == 409 and error.value.code == 'HANDOFF_ALREADY_REQUESTED'
        assert tx.find.await_count == 2
        tx.create_draft.assert_not_awaited()
    asyncio.run(scenario())


def test_fresh_read_never_bypasses_ownership_after_first_lookup_misses():
    async def scenario():
        actor, conversation, draft, tx, _, service = setup()
        tx.find.side_effect = [None, replace(conversation, anonymous_session_id=uuid4())]
        with pytest.raises(AuthError) as error:
            await service.create(actor, conversation.id, 'request_handoff', draft.payload)
        assert error.value.status == 404
        tx.create_draft.assert_not_awaited()
    asyncio.run(scenario())
