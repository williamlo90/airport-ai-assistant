"""Human preview is one signed intent, bound to actor/case and bounded in time."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from airport_assistant.modules.cases.information_preview import (
    InformationInput,InformationConfirmation,InformationPreviewCodec)
from airport_assistant.modules.drafts.models import DraftError


def setup():
    identity=SimpleNamespace(session=SimpleNamespace(kind='customer',tenant_id=uuid4(),principal_id=uuid4(),id=uuid4()))
    clock=[1000]
    return identity,uuid4(),clock,InformationPreviewCodec(b'x'*32,clock=lambda:clock[0])


def test_nonce_unique_original_unicode_round_trip_and_expiry():
    identity,cid,clock,codec=setup()
    command=InformationInput(expected_version=7,message='🙂'*4000,language='id')
    record,token=codec.issue(identity,cid,command)
    another,_=codec.issue(identity,cid,command)
    assert record.preview_id!=another.preview_id
    assert len(token)<=32768 and codec.read(identity,cid,token)==record
    assert record.expected_version==7 and record.message==command.message
    assert len(codec.digest(record))==64
    clock[0]=record.expires_at
    with pytest.raises(DraftError) as e:codec.read(identity,cid,token)
    assert e.value.code=='PREVIEW_EXPIRED'
    # Only the business service's already-saved receipt lookup may opt into
    # decoding old identity; this does not authorize a fresh write.
    assert codec.read(identity,cid,token,allow_expired=True)==record


@pytest.mark.parametrize('changed',['tenant_id','principal_id','id','kind','case_id','key'])
def test_signed_intent_cannot_move_between_actors_cases_or_purposes(changed):
    identity,cid,clock,codec=setup()
    _,token=codec.issue(identity,cid,InformationInput(expected_version=1,message='Original',language='en'))
    if changed=='case_id':cid=uuid4()
    elif changed=='key':codec=InformationPreviewCodec(b'y'*32,clock=lambda:1000)
    else:setattr(identity.session,changed,'staff' if changed=='kind' else uuid4())
    with pytest.raises(DraftError) as e:codec.read(identity,cid,token)
    assert e.value.code=='BAD_REQUEST'


@pytest.mark.parametrize('bad',['','x'*32769,'not.a.token','🙂'])
def test_malformed_token_never_becomes_a_new_preview(bad):
    identity,cid,clock,codec=setup()
    with pytest.raises(DraftError):codec.read(identity,cid,bad)


@pytest.mark.parametrize('message',['',' ','\x00','x'*4001,'\ud800'])
def test_empty_unbounded_invalid_unicode_message_rejected(message):
    with pytest.raises(ValidationError):InformationInput(expected_version=1,message=message,language='en')


@pytest.mark.parametrize('value',[False,1,'true',None])
def test_confirmation_requires_explicit_true(value):
    with pytest.raises(ValidationError):InformationConfirmation(confirmation_token='signed',confirmed=value)


def test_signature_tampering_and_clock_rollback_rejected():
    identity,cid,clock,codec=setup()
    _,token=codec.issue(identity,cid,InformationInput(expected_version=1,message='Original',language='en'))
    with pytest.raises(DraftError):codec.read(identity,cid,('x' if token[0]!='x' else 'y')+token[1:])
    clock[0]=999
    with pytest.raises(DraftError):codec.read(identity,cid,token)
