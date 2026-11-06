"""Independent late-source fallback checks; no DB or provider execution by QA."""
import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_conversation_rules import setup
from airport_assistant.modules.agent.answers import KnowledgeAnswerUnavailable
from airport_assistant.modules.conversations.publication import AIPublication, GenerationTicket, GenerationExpired


@pytest.mark.parametrize('language,staff_word', [('en', 'staff'), ('id', 'petugas')])
def test_late_invalid_source_publishes_only_localized_safe_fallback_when_chat_is_current(language, staff_word):
    async def scenario():
        identity, conversation, tx, conversations = setup()
        conversation = replace(conversation, language=language)
        tx.find.return_value = conversation
        now = datetime.now(timezone.utc)
        ticket = GenerationTicket(uuid4(), 'qa-internal-placeholder')
        generation = dict(
            conversation_id=conversation.id, input_version=conversation.version,
            generation_epoch=conversation.generation_epoch, language=language,
            nonce_hash=hashlib.sha256(ticket.nonce.encode()).hexdigest(), state='pending',
            expires_at=now + timedelta(minutes=5), id=ticket.id,
        )
        tx.generation = AsyncMock(return_value=generation)
        tx.now = AsyncMock(return_value=now)
        tx.ai_message = AsyncMock(return_value={'id': uuid4(), 'sequence': 1})
        tx.published_generation = AsyncMock()
        tx.stale_generation = AsyncMock()

        @asynccontextmanager
        async def savepoint():
            yield

        tx.savepoint = savepoint
        answers = SimpleNamespace(validate_transaction=AsyncMock(side_effect=KnowledgeAnswerUnavailable()))
        publication = AIPublication(conversations._repository, answers)
        result = await publication.publish(identity, ticket, 'Factual body that became stale', grounding=object())
        assert result['state'] == 'published'
        tx.ai_message.assert_awaited_once()
        arguments = tx.ai_message.await_args
        fallback = arguments.args[2]
        assert staff_word in fallback.lower()
        assert 'Factual body that became stale' not in fallback
        assert arguments.kwargs.get('knowledge_evidence') is None
        tx.published_generation.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize('expiry_stage', ['before_fallback', 'fallback_insert'])
def test_late_source_failure_does_not_publish_fallback_after_generation_expires(expiry_stage):
    async def scenario():
        identity, conversation, tx, conversations = setup()
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(seconds=1)
        ticket = GenerationTicket(uuid4(), 'qa-internal-placeholder')
        generation = dict(
            conversation_id=conversation.id, input_version=conversation.version,
            generation_epoch=conversation.generation_epoch, language=conversation.language,
            nonce_hash=hashlib.sha256(ticket.nonce.encode()).hexdigest(), state='pending',
            expires_at=deadline, id=ticket.id,
        )
        tx.generation = AsyncMock(return_value=generation)
        tx.now = AsyncMock(side_effect=[now, deadline] if expiry_stage == 'before_fallback' else [now, now])
        tx.ai_message = AsyncMock(side_effect=GenerationExpired())
        tx.published_generation = AsyncMock()
        tx.stale_generation = AsyncMock()

        @asynccontextmanager
        async def savepoint():
            yield

        tx.savepoint = savepoint
        answers = SimpleNamespace(validate_transaction=AsyncMock(side_effect=KnowledgeAnswerUnavailable()))
        result = await AIPublication(conversations._repository, answers).publish(
            identity, ticket, 'Expired factual answer', grounding=object(),
        )
        assert result == {'state': 'discarded', 'reason': 'STALE_GENERATION'}
        assert tx.ai_message.await_count == (0 if expiry_stage == 'before_fallback' else 1)
        tx.published_generation.assert_not_awaited()
        tx.stale_generation.assert_awaited_once_with(ticket.id)

    asyncio.run(scenario())
