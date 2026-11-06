"""Untrusted staff commands must contain two strict reviewed versions."""
import asyncio
from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from airport_assistant.modules.conversations.handoff import AcceptChat,ChatHandoff
from airport_assistant.modules.cases.lifecycle import Assignment,StatusChange
from airport_assistant.modules.drafts.models import DraftError


@pytest.mark.parametrize('field',['expected_version','expected_case_version'])
@pytest.mark.parametrize('value',[True,0,-1,'1',1.5])
def test_accept_versions_are_strict_positive_integers(field,value):
    data={'expected_version':1,'expected_case_version':1,field:value}
    with pytest.raises(ValidationError):AcceptChat.model_validate(data)


@pytest.mark.parametrize('extra',[{'handler_membership_id':str(uuid4())},{'actor_principal_id':str(uuid4())},{'mode':'human'}])
def test_accept_cannot_choose_handler_or_actor(extra):
    with pytest.raises(ValidationError):AcceptChat.model_validate(dict(expected_version=1,expected_case_version=1,**extra))


@pytest.mark.parametrize('key',['','short','not valid key long enough','x'*129,None])
def test_bad_accept_retry_key_never_opens_a_transaction(key):
    repository=Mock()
    async def scenario():
        with pytest.raises(DraftError) as error:await ChatHandoff(repository).accept(None,uuid4(),AcceptChat(expected_version=1,expected_case_version=1),key)
        assert error.value.code=='IDEMPOTENCY_KEY_REQUIRED'
        repository.transaction.assert_not_called()
    asyncio.run(scenario())


def test_legacy_unlinked_commands_still_allow_no_conversation_version():
    assert Assignment(expected_version=1,assignee_id=None).expected_conversation_version is None
    assert StatusChange(expected_version=1,status='in_progress',next_action='Work continues.').expected_conversation_version is None
    with pytest.raises(ValidationError):Assignment(expected_version=1,expected_conversation_version=True,assignee_id=None)
