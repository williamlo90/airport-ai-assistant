"""Independent checkpoint provenance review; tests are executed only by Lead."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from airport_assistant.modules.agent.models import AgentError, ToolCall
from airport_assistant.modules.knowledge.retrieval import Search
from airport_assistant.integrations.agent.graph import AgentGraph
from test_agent_workflow import setup


def test_booking_specific_knowledge_context_is_not_checkpointed_without_private_labels():
    async def scenario():
        fixture, store, model, engine, request = setup()
        context = Search(
            query='Parking policy', service_type='parking', language='en',
            as_of='2026-01-01T00:00:00Z', policy_reference='PRIVATE-BOOKING-POLICY',
            terminal='PRIVATE_GATE', booking_created_at='2002-03-04T05:06:07Z',
        )
        request = request.model_copy(update={'knowledge': context})
        try:
            run_id = await engine.start(fixture.identity, request)
        except (AgentError, ValidationError):
            # Refusing unsupported source-derived checkpoint context is safe.
            assert not store.rows
            return
        checkpoint = json.dumps(store.rows[run_id]['parameters'], default=str)
        assert 'PRIVATE-BOOKING-POLICY' not in checkpoint
        assert 'PRIVATE_GATE' not in checkpoint
        assert '2002-03-04' not in checkpoint
        assert not model.calls

    asyncio.run(scenario())


def test_durable_read_accounting_includes_both_model_context_refreshes():
    async def scenario():
        fixture, store, model, engine, request = setup()
        model.always_read = True
        run_id = await engine.start(fixture.identity, request)
        result = await AgentGraph(engine).resume(fixture.identity, run_id)
        actual_reads = fixture.source.booking.await_count
        assert result.status == 'review'
        assert actual_reads > len(store.rows[run_id]['trace'])
        assert store.rows[run_id]['read_calls'] == actual_reads
        assert actual_reads <= 12

    asyncio.run(scenario())


def test_exhausted_budget_blocks_refresh_before_any_new_source_or_model_call():
    async def scenario():
        fixture, store, model, engine, request = setup()
        run_id = await engine.start(fixture.identity, request)
        store.rows[run_id].update(
            read_calls=12,
            trace=[ToolCall(call_id='qa_previous_read', name='read_booking',
                            arguments={'reference': 'PK-A-001'}).model_dump()],
        )
        with pytest.raises(AgentError) as failure:
            await AgentGraph(engine).resume(fixture.identity, run_id)
        assert failure.value.code == 'AGENT_STEP_LIMIT'
        fixture.source.booking.assert_not_awaited()
        assert not model.calls
        assert store.rows[run_id]['read_calls'] == 12

    asyncio.run(scenario())
