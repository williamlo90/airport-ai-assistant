"""Disabling private persistence cannot make an existing private run public."""
import asyncio

import pytest

from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.modules.identity.auth import AuthError
from test_agent_checkpoints_private import setup


@pytest.mark.parametrize('operation', ['load', 'start'])
def test_default_disabled_mode_cannot_bypass_an_existing_private_checkpoint(operation):
    async def scenario():
        fixture, store, model, engine, request = setup()
        run_id = await engine.start(fixture.identity, request)
        result = await AgentGraph(engine).resume(fixture.identity, run_id)
        assert result.labels is not None
        assert store.rows[run_id]['resource_security_labels']
        assert not fixture.conv.resource_security_labels
        fixture.private.allows.side_effect = None
        fixture.private.allows.return_value = False
        restarted = AgentWorkflow(fixture.service, store)
        with pytest.raises(AuthError):
            if operation == 'load':
                await restarted.load(fixture.identity, run_id)
            else:
                await restarted.start(fixture.identity, request)

    asyncio.run(scenario())
