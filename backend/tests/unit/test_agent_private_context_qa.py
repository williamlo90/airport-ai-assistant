"""Late knowledge reads must not return results from a revoked private root."""
import asyncio

import pytest

from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.modules.identity.auth import AuthError
from test_agent_answers import setup
from test_agent_private_context import labels


def test_private_root_revoked_during_post_model_evidence_consumption_cannot_return_answer():
    async def scenario():
        fixture, store, model, engine, request, knowledge = setup()
        fixture.conv.resource_security_labels = labels()
        engine = AgentWorkflow(fixture.service, store, private_persistence=True)
        consume = fixture.service.evidence.consume

        async def revoked(*args, **kwargs):
            result = await consume(*args, **kwargs)
            # collect() also calls consume(); isolate the second read after
            # the model response so the earlier pre-model guard cannot mask it.
            if model.answer.await_count:
                fixture.conversations.customer_message.side_effect = AuthError(404, 'RESOURCE_NOT_FOUND')
            return result

        fixture.service.evidence.consume = revoked
        run_id = await engine.start(fixture.identity, request)
        with pytest.raises(AuthError):
            await AgentGraph(engine).resume(fixture.identity, run_id)
        model.answer.assert_awaited_once()

    asyncio.run(scenario())
