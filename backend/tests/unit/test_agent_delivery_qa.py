"""Independent delivery replay/fidelity regressions; Lead executes, all IO mocked."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_agent_workflow import setup
from test_agent_delivery import Publication
from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.agent.delivery import AgentDelivery
from airport_assistant.modules.agent.models import AgentError, Clarification, Decision
from airport_assistant.modules.agent.translation import preserve_values
from airport_assistant.modules.drafts.models import DraftError


@pytest.mark.parametrize('original,changed', [
    ('The price is USD 100.', 'Harganya IDR 100.'),
    ('The price is S$100.', 'Harganya $100.'),
    ('The price is S$100.', 'Harganya US$100.'),
])
def test_currency_identity_cannot_change_when_numeric_amount_is_unchanged(original, changed):
    with pytest.raises(AgentError) as failure:
        preserve_values(original, changed)
    assert failure.value.code == 'AGENT_TRANSLATION_VALUES_CHANGED'


def test_concurrent_generated_and_recovery_questions_return_same_publication_receipt():
    async def scenario():
        fixture, store, model, engine, request = setup()
        request=request.model_copy(update={'booking_references':()})
        model.advance = AsyncMock(return_value=Decision(
            kind='clarify', clarification=Clarification(question='Which service needs help?'),
        ))
        held = asyncio.Event()
        release = asyncio.Event()

        class RacingPublication(Publication):
            async def publish(self, identity, ticket, body):
                if body == 'Which service needs help?':
                    held.set()
                    await release.wait()
                if ticket.id in self.saved:
                    # Reproduce the existing publication port's immutable-body rule.
                    if self.bodies[0] != body:
                        raise DraftError(409, 'MESSAGE_CONFLICT')
                    return self.saved[ticket.id]
                return await super().publish(identity, ticket, body)

        publication = RacingPublication(fixture)
        delivery = AgentDelivery(AgentGraph(engine), publication, b'qa-only-placeholder')
        run_id = await delivery.start(fixture.identity, request)
        first = asyncio.create_task(delivery.resume(fixture.identity, run_id))
        await asyncio.wait_for(held.wait(), timeout=2)
        try:
            recovered = await delivery.resume(fixture.identity, run_id)
        finally:
            release.set()
        original = await first
        assert recovered == original and original['state'] == 'published'
        assert len(publication.bodies) == 1
        model.advance.assert_awaited_once()

    asyncio.run(scenario())
