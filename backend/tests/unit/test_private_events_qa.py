"""Independent materialization-race checks; Lead executes without providers."""
import asyncio
from uuid import uuid4

import pytest

from airport_assistant.modules.drafts.models import DraftError
from test_private_content import records
from test_private_events import setup


@pytest.mark.parametrize('initially_private', [False, True])
def test_label_or_basis_change_during_event_query_cannot_release_old_projection(initially_private):
    identity, conversation_id, state, tx, access, service = setup(private=initially_private)

    async def events(*args):
        # Source checks and the materializer's first metadata check already ran.
        # A concurrent writer commits a new protected parent basis while rows load.
        state['case_version'] += 1
        if not initially_private:
            state['case_security_labels'] = records()
        return [dict(id=uuid4(), sequence=1, event_type='ai_message',
                     payload={'body': 'Internal candidate content'})]

    tx.events.side_effect = events
    with pytest.raises(DraftError) as error:
        asyncio.run(service.poll(identity, conversation_id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'
    tx.events.assert_awaited_once()
