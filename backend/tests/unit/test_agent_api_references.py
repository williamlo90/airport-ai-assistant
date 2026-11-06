"""HTTP source selectors are bounded hints, not authority or model-selected scope."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
from starlette.datastructures import Headers
from pydantic import ValidationError

from airport_assistant.api.agent import BeginRun,begin


def payload(**extra):
    return dict(message_id=str(uuid4()),request_key=str(uuid4()),purpose='assist',**extra)


@pytest.mark.parametrize('extra',[
    {'booking_references':['bad space']},
    {'transaction_references':['a'*81]},
    {'booking_references':['ONE','TWO']},
    {'booking_references':'ONE'},
    {'transaction_references':[1]},
    {'booking_references':['']},
    {'source_subject':'forged'},
])
def test_bad_scope_rejected(extra):
    with pytest.raises(ValidationError):BeginRun.model_validate(payload(**extra))


@pytest.mark.parametrize('purpose',['answer','handoff'])
def test_public_answer_and_direct_handoff_do_not_accept_private_selectors(purpose):
    with pytest.raises(ValidationError):
        BeginRun.model_validate(dict(payload(booking_references=['BOOK-1']),purpose=purpose))


def test_route_forwards_only_explicit_references_to_existing_authorization_workflow():
    async def scenario():
        identity=SimpleNamespace(session=SimpleNamespace(kind='customer'));run=uuid4();conversation=uuid4()
        delivery=SimpleNamespace(start=AsyncMock(return_value=run))
        request=SimpleNamespace(query_params={},headers=Headers(),app=SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(agent_delivery=delivery))))
        response=Response()
        result=await begin(conversation,BeginRun.model_validate(payload(booking_references=['BOOK-1'],transaction_references=['PAY-1'])),request,response,identity)
        assert result.run_id==run and response.headers['Cache-Control']=='no-store'
        actual_identity,command=delivery.start.await_args.args
        assert actual_identity is identity
        assert command.booking_references==('BOOK-1',) and command.transaction_references==('PAY-1',)
        assert command.mode=='assist' and command.knowledge is None and command.contact_email is None
    asyncio.run(scenario())


def test_anonymous_private_selector_does_not_invoke_workflow():
    from airport_assistant.modules.identity.auth import AuthError
    async def scenario():
        delivery=SimpleNamespace(start=AsyncMock())
        request=SimpleNamespace(query_params={},headers=Headers(),app=SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(agent_delivery=delivery))))
        with pytest.raises(AuthError) as error:
            await begin(uuid4(),BeginRun.model_validate(payload(booking_references=['BOOK-1'])),request,Response(),SimpleNamespace(session=SimpleNamespace(kind='anonymous')))
        assert error.value.status==403
        delivery.start.assert_not_awaited()
    asyncio.run(scenario())
