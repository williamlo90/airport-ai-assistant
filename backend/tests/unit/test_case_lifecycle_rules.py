"""Lifecycle decisions and strict user intent before any database write."""
from uuid import uuid4
import pytest
from pydantic import ValidationError
from airport_assistant.modules.cases.lifecycle import StatusChange,ContinueCase,Assignment,decide_status
from airport_assistant.modules.drafts.models import DraftError


@pytest.mark.parametrize('origin,destination,allowed',[
    ('new','in_progress',True),('new','closed',True),('new','awaiting_customer',False),('new','awaiting_external',False),
    ('in_progress','awaiting_customer',True),('in_progress','awaiting_external',True),('in_progress','closed',True),
    ('awaiting_customer','in_progress',True),('awaiting_customer','awaiting_external',False),('awaiting_customer','closed',True),
    ('awaiting_external','in_progress',True),('awaiting_external','awaiting_customer',False),('closed','in_progress',False),('closed','closed',False)])
def test_transition_matrix(origin,destination,allowed):
    case=dict(status=origin,assigned_membership_id=uuid4())
    command=StatusChange(expected_version=1,status=destination,next_action='Langkah selanjutnya.',**(dict(close_reason='resolved',resolution='Hasil dijelaskan.') if destination=='closed' else {}))
    if allowed:
        result=decide_status(case,command)
        assert result['status']==destination
        assert result['next_actor']=={'awaiting_customer':'customer','awaiting_external':'external','closed':'none'}.get(destination,'staff')
    else:
        with pytest.raises(DraftError) as error:decide_status(case,command)
        assert error.value.status==409


@pytest.mark.parametrize('extra',[
    {},{'close_reason':'resolved'},{'resolution':'Selesai'},
    {'close_reason':'duplicate','resolution':'Duplikat'},
    {'close_reason':'no_response','resolution':'Tidak dibalas'},
    {'close_reason':'resolved','resolution':' '},
    {'close_reason':'resolved','resolution':'Selesai','followup_evidence':'Tidak sesuai'},
    {'close_reason':'customer_cancelled','resolution':'Selesai','related_case_id':uuid4()},
])
def test_closure_requires_reason_resolution_and_reason_specific_evidence(extra):
    with pytest.raises(ValidationError):StatusChange(expected_version=1,status='closed',next_action='Hasil tersedia.',**extra)


@pytest.mark.parametrize('reason',['resolved','customer_cancelled','duplicate','no_response'])
def test_supported_manual_closure_reasons(reason):
    value=StatusChange(expected_version=1,status='closed',next_action='Hasil tersedia.',close_reason=reason,resolution='Penanganan diakhiri.',
        **(dict(related_case_id=uuid4()) if reason=='duplicate' else dict(followup_evidence='Dua tindak lanjut petugas.') if reason=='no_response' else {}))
    assert value.close_reason==reason


@pytest.mark.parametrize('value',[True,0,-1,'1',1.5])
def test_version_must_be_positive_strict_integer(value):
    with pytest.raises(ValidationError):Assignment(expected_version=value,assignee_id=uuid4())


@pytest.mark.parametrize('intent',['thanks','ambiguous','new_issue','reopen',''])
def test_only_explicit_same_issue_continuation_can_reopen(intent):
    with pytest.raises(ValidationError):ContinueCase(expected_version=1,intent=intent,message='Bantuan.',client_request_id=uuid4())


@pytest.mark.parametrize('text',['',' ','\x00'])
def test_empty_or_invalid_customer_message_rejected(text):
    with pytest.raises(ValidationError):ContinueCase(expected_version=1,intent='same_issue',message=text,client_request_id=uuid4())


def test_active_status_requires_assignee_and_does_not_treat_closed_as_satisfied():
    with pytest.raises(DraftError):decide_status(dict(status='new',assigned_membership_id=None),StatusChange(expected_version=1,status='in_progress',next_action='Ditangani.'))
    result=decide_status(dict(status='new',assigned_membership_id=None),StatusChange(expected_version=1,status='closed',next_action='Hasil.',close_reason='no_response',resolution='Belum dibalas.',followup_evidence='Ditindaklanjuti petugas.'))
    assert 'rating' not in result and 'satisfied' not in result


def test_model_or_client_cannot_supply_authority_fields():
    with pytest.raises(ValidationError):StatusChange(expected_version=1,status='in_progress',next_action='Tindak lanjut.',tenant_id=uuid4())


@pytest.mark.parametrize('state,active,expected,rechecks',[
    ('23514',False,'lost',1),('23514',True,'database',1),('42501',False,'lost',1),('42P01',False,'database',0)])
def test_repository_confirms_session_loss_without_masking_live_integrity_failure(state,active,expected,rechecks):
    import asyncio
    from contextlib import asynccontextmanager
    from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
    from airport_assistant.infrastructure.database.connections import DatabaseError
    from airport_assistant.modules.identity.customer_transaction import CustomerSessionLost
    class Database:
        calls=0
        @asynccontextmanager
        async def transaction(self,context):
            self.calls+=1
            yield object()
            if self.calls==1:raise DatabaseError(state)
    class Unit:
        async def session_active(self):return active
    database=Database();repository=PostgresCaseLifecycle(database)
    repository.unit=lambda *_:Unit()
    async def scenario():
        with pytest.raises(CustomerSessionLost if expected=='lost' else DatabaseError):
            async with repository.transaction(object()):pass
    asyncio.run(scenario())
    assert database.calls==1+rechecks
