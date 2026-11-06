"""CHAT-03 untrusted message boundaries and hidden internal capability."""
from uuid import uuid4
import pytest
from pydantic import ValidationError
from airport_assistant.modules.conversations.publication import StaffMessage,GenerationTicket


def command(**changes):return dict({'conversation_id':str(uuid4()),'channel':'chat','body':'Original staff reply','language':'en','expected_version':1,'expected_conversation_version':2,'expected_epoch':2},**changes)


@pytest.mark.parametrize('field',['expected_version','expected_conversation_version','expected_epoch'])
@pytest.mark.parametrize('value',[True,0,'1'])
def test_versions_and_epoch_are_strict_positive(field,value):
    with pytest.raises(ValidationError):StaffMessage.model_validate(command(**{field:value}))


@pytest.mark.parametrize('change',[{'body':' \n'},{'body':'\x00'},{'body':'x'*4001},{'author_type':'ai'},{'sender_membership_id':str(uuid4())},{'tenant_id':str(uuid4())},{'generation_id':str(uuid4())}])
def test_reply_cannot_inject_identity_or_invalid_body(change):
    with pytest.raises(ValidationError):StaffMessage.model_validate(command(**change))


def test_internal_nonce_is_not_in_ticket_representation():
    ticket=GenerationTicket(uuid4(),'private-internal-test-capability')
    assert ticket.nonce not in repr(ticket)
