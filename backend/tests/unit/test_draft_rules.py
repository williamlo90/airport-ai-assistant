"""CASE-01 canonical previews, lifecycle and access decisions."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from airport_assistant.modules.drafts.models import Draft,ReportPayload,Destination,DraftError,displayed_summary,seal,require_integrity,require_confirmation,canonical_hash
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.conversations.service import Conversation
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.auth import Session,AuthError


def report(**changes):
    values=dict(category='transaction_issue',service_type='parking',summary='Payment error',customer_report='TX-A-001 IDR100000 2026-11-10',attachment_ids=[])
    return ReportPayload.model_validate({**values,**changes})


def setup():
    now=datetime.now(timezone.utc)
    actor=AccessIdentity(Session(uuid4(),uuid4(),None,'anonymous',now+timedelta(hours=1),'private'))
    conv=Conversation(uuid4(),actor.session.tenant_id,None,actor.session.id,'id',1,1,0)
    dest=Destination(queue_id=uuid4(),organization_id=uuid4(),queue_name='Parking',organization_name='Airport',service_type='parking')
    draft=seal(Draft(uuid4(),actor.session.tenant_id,conv.id,None,actor.session.id,actor.session.id,'create_case',report(),dest,
                     displayed_summary('create_case',report(),dest,'id'),'id',1,'',now,now+timedelta(minutes=15)))
    tx=Mock(lock_draft_case=AsyncMock(),lock_draft_sources=AsyncMock(return_value=()),session_active=AsyncMock(return_value=True),find=AsyncMock(return_value=conv),
            locate_draft=AsyncMock(return_value=conv.id),find_draft=AsyncMock(return_value=draft),
            destination=AsyncMock(return_value=dest),now=AsyncMock(return_value=now),
            create_draft=AsyncMock(side_effect=lambda value:value),edit_draft=AsyncMock(side_effect=lambda value:value),
            terminal=AsyncMock(return_value=replace(draft,state='expired')))
    outcomes=[]
    @asynccontextmanager
    async def transaction(context):
        try:yield tx
        except BaseException:outcomes.append('rollback');raise
        else:outcomes.append('commit')
    service=DraftService(Mock(transaction=transaction))
    return actor,conv,draft,tx,outcomes,service


@pytest.mark.parametrize('field',['id','tenant_id','conversation_id','created_session_id','actor_session_id','action_type','version','language','created_at','expires_at','payload','destination','displayed_summary'])
def test_hash_binds_every_material_confirmation_field(field):
    _,_,draft,*_=setup()
    changes=dict(id=uuid4(),tenant_id=uuid4(),conversation_id=uuid4(),created_session_id=uuid4(),actor_session_id=uuid4(),
                 action_type='request_handoff',version=2,language='en',created_at=draft.created_at-timedelta(seconds=1),
                 expires_at=draft.expires_at-timedelta(seconds=1),payload=report(contact_email='other@example.com'),
                 destination=draft.destination.model_copy(update={'queue_id':uuid4()}),displayed_summary='Changed preview')
    changed=replace(draft,**{field:changes[field]})
    assert canonical_hash(changed)!=draft.payload_hash
    with pytest.raises(DraftError):require_integrity(changed)


def test_canonical_payload_and_immutable_material_match_displayed_values():
    _,_,draft,*_=setup()
    payload=ReportPayload.model_validate(dict(reversed(list(draft.payload.model_dump().items()))))
    assert canonical_hash(replace(draft,payload=payload))==draft.payload_hash
    assert draft.payload.customer_report in draft.displayed_summary
    assert draft.destination.queue_name in draft.displayed_summary
    assert draft.payload.attachment_ids==()
    with pytest.raises(ValidationError):draft.payload.summary='Rewritten'


@pytest.mark.parametrize('version,hash_value',[(0,None),(True,None),(2,None),(1,'0'*64),(1,'é'*64)])
def test_old_or_invalid_confirmation_is_rejected_without_execution(version,hash_value):
    _,_,draft,*_=setup()
    with pytest.raises(DraftError) as exc:
        require_confirmation(draft,expected_version=version,payload_hash=hash_value or draft.payload_hash,now=draft.created_at)
    assert exc.value.status==409


@pytest.mark.parametrize('state,status',[('cancelled',409),('expired',410)])
def test_terminal_draft_cannot_pass_confirmation_guard(state,status):
    _,_,draft,*_=setup();draft=replace(draft,state=state)
    require_integrity(draft)
    with pytest.raises(DraftError) as exc:require_confirmation(draft,expected_version=1,payload_hash=draft.payload_hash,now=draft.created_at)
    assert exc.value.status==status


@pytest.mark.parametrize('changes',[
    {'source_observations':[]},{'resource_security_labels':[]},{'destination_queue_id':str(uuid4())},
    {'attachment_ids':['invalid-uuid']},{'customer_report':' \t\n'},{'summary':'\x00'},
    {'summary':'x'*201},{'customer_report':'x'*4001},{'category':'refund_payment'},
])
def test_report_input_cannot_inject_authority_destination_or_unimplemented_features(changes):
    with pytest.raises((ValidationError,AuthError)):report(**changes)


def test_edit_changes_version_hash_and_keeps_deadline():
    async def scenario():
        actor,conv,draft,tx,outcomes,service=setup()
        changed=await service.edit(actor,draft.id,1,report(summary='Corrected'))
        assert changed.version==2 and changed.payload_hash!=draft.payload_hash
        assert changed.expires_at==draft.expires_at and outcomes==['commit']
        with pytest.raises(DraftError):require_confirmation(changed,expected_version=1,payload_hash=draft.payload_hash,now=draft.created_at)
    asyncio.run(scenario())


def test_expired_mutation_commits_terminal_state_before_410():
    async def scenario():
        actor,conv,draft,tx,outcomes,service=setup();tx.now.return_value=draft.expires_at
        with pytest.raises(DraftError) as exc:await service.edit(actor,draft.id,1,report())
        assert exc.value.status==410 and outcomes==['commit']
        tx.terminal.assert_awaited_once_with(draft.id,'expired');tx.edit_draft.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('changes',[{'created_session_id':uuid4()},{'case_id':uuid4()},{'resource_security_labels':({'source':'reference'},)}])
def test_same_owner_cannot_bypass_session_or_private_source_context(changes):
    async def scenario():
        actor,conv,draft,tx,outcomes,service=setup();tx.find_draft.return_value=replace(draft,**changes)
        with pytest.raises(AuthError) as exc:await service.get(actor,draft.id)
        assert exc.value.status==404
    asyncio.run(scenario())


def test_no_active_destination_never_creates_placeholder_success():
    async def scenario():
        actor,conv,draft,tx,outcomes,service=setup();tx.destination.return_value=None
        with pytest.raises(DraftError) as exc:await service.create(actor,conv.id,'create_case',report())
        assert exc.value.status==503
        assert [call.args for call in tx.destination.await_args_list]==[('parking',),('general',)]
        tx.create_draft.assert_not_awaited()
    asyncio.run(scenario())


def test_wire_uuid_extra_claims_and_composition():
    from airport_assistant.api.drafts import CreateDraft
    from airport_assistant.composition import build_services
    from airport_assistant.auth_config import AuthSettings
    values=dict(conversation_id=str(uuid4()),action_type='create_case',payload=report().model_dump(mode='json'))
    assert CreateDraft.model_validate(values).payload==report()
    for change in ({'actor_session_id':str(uuid4())},{'case_id':'invalid-uuid'},{'action_type':'add_case_information'}):
        with pytest.raises(ValidationError):CreateDraft.model_validate({**values,**change})
    assert build_services().drafts is None
    assert isinstance(build_services(database=Mock(schema="app"),auth_settings=AuthSettings(SESSION_SECRET='k'*48)).drafts,DraftService)
