"""Business decisions at the conversation UoW boundary, without database I/O."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import pytest

from airport_assistant.modules.conversations.service import Conversation,ConversationService,ConversationError,Message
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.auth import Session,AuthError


def setup():
    actor=AccessIdentity(Session(uuid4(),uuid4(),None,'anonymous',datetime.now(timezone.utc)+timedelta(hours=1),'private'))
    conversation=Conversation(uuid4(),actor.session.tenant_id,None,actor.session.id,'id',1,1,0)
    tx=Mock(session_active=AsyncMock(return_value=True),find=AsyncMock(return_value=conversation),
            languages=AsyncMock(return_value=['id','en']),duplicate=AsyncMock(return_value=None),
            append=AsyncMock(),create=AsyncMock(),change_language=AsyncMock())
    @asynccontextmanager
    async def transaction(context):
        assert context.session_id==actor.session.id
        yield tx
    repo=Mock(transaction=transaction)
    return actor,conversation,tx,ConversationService(repo)


@pytest.mark.parametrize('method',['get','append','change_language','create'])
def test_stale_session_is_rejected_before_object_lookup(method):
    async def scenario():
        actor,conv,tx,service=setup();tx.session_active.return_value=False
        args={'get':(conv.id,),'append':(conv.id,uuid4(),'Message'),
              'change_language':(conv.id,'en',1),'create':('en',)}[method]
        with pytest.raises(AuthError) as exc:await getattr(service,method)(actor,*args)
        assert exc.value.status==401
        tx.find.assert_not_awaited();tx.create.assert_not_awaited();tx.append.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('change',[
    dict(case_id=uuid4()),dict(resource_security_labels=({'source':'reference'},)),
    dict(anonymous_session_id=uuid4()),dict(tenant_id=uuid4()),dict(id=uuid4()),
])
def test_ownership_alone_never_opens_bound_or_mismatched_context(change):
    async def scenario():
        actor,conv,tx,service=setup();tx.find.return_value=replace(conv,**change)
        with pytest.raises(AuthError) as exc:await service.append(actor,conv.id,uuid4(),'Private input')
        assert exc.value.status==404
        tx.duplicate.assert_not_awaited();tx.append.assert_not_awaited()
    asyncio.run(scenario())


def test_duplicate_content_is_not_resubmitted_after_language_change():
    async def scenario():
        actor,conv,tx,service=setup()
        original=Message(uuid4(),7,'Original text','id')
        tx.find.return_value=replace(conv,language='en',version=8,last_sequence=7)
        tx.duplicate.return_value=original
        assert await service.append(actor,conv.id,uuid4(),'Original text')==original
        tx.append.assert_not_awaited()
        with pytest.raises(ConversationError) as exc:await service.append(actor,conv.id,uuid4(),'New content')
        assert exc.value.status==409 and exc.value.code=='MESSAGE_CONFLICT'
    asyncio.run(scenario())


def test_language_noop_does_not_bump_version_and_stale_version_is_rejected():
    async def scenario():
        actor,conv,tx,service=setup()
        assert await service.change_language(actor,conv.id,'id',1)==conv
        tx.change_language.assert_not_awaited()
        with pytest.raises(ConversationError):await service.change_language(actor,conv.id,'en',2)
        tx.change_language.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('body',['\u00a0','\u2003','\x00','x'*4001,42])
def test_message_input_validation_precedes_storage(body):
    async def scenario():
        actor,conv,tx,service=setup()
        with pytest.raises(ConversationError):await service.append(actor,conv.id,uuid4(),body)
        tx.session_active.assert_not_awaited()
    asyncio.run(scenario())


def test_uuid_string_wire_format_is_supported_without_loosening_message_body():
    from airport_assistant.api.conversations import SendMessage
    from pydantic import ValidationError
    id=uuid4()
    assert SendMessage.model_validate(dict(body='Text',client_message_id=str(id))).client_message_id==id
    for values in (dict(body=5,client_message_id=str(id)),dict(body='Text',client_message_id='not-uuid'),
                   dict(body='Text',client_message_id=str(id),owner='claim')):
        with pytest.raises(ValidationError):SendMessage.model_validate(values)


def test_session_context_validation_and_composition():
    from airport_assistant.modules.identity.context import DatabaseContext
    from airport_assistant.composition import build_services
    from airport_assistant.auth_config import AuthSettings
    with pytest.raises(ValueError):DatabaseContext(uuid4(),session_id='not-uuid')
    assert build_services().conversations is None
    services=build_services(database=Mock(),auth_settings=AuthSettings(SESSION_SECRET='s'*48))
    assert isinstance(services.conversations,ConversationService)


@pytest.mark.parametrize('sqlstate,active,expected_calls',[('42501',False,2),('42501',True,2),('23505',False,1),('08006',False,1)])
def test_repository_confirms_session_loss_only_for_rolled_back_rls_failure(sqlstate,active,expected_calls):
    from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
    from airport_assistant.infrastructure.database.connections import DatabaseError
    from airport_assistant.modules.conversations.service import ConversationSessionLost
    async def scenario():
        calls=[]
        cursor=Mock(fetchone=AsyncMock(return_value=(active,)))
        connection=Mock(execute=AsyncMock(return_value=cursor))
        @asynccontextmanager
        async def transaction(context):
            calls.append(context)
            yield connection
        repo=PostgresConversations(Mock(transaction=transaction))
        actor,*_=setup()
        error=DatabaseError(sqlstate)
        with pytest.raises(ConversationSessionLost if sqlstate=='42501' and not active else DatabaseError) as exc:
            async with repo.transaction(actor.database_context):raise error
        assert len(calls)==expected_calls
        if not(sqlstate=='42501' and not active):assert exc.value is error
    asyncio.run(scenario())
