"""CASE-02: consent prerequisites, stable retries and rollback decisions."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_draft_rules import setup,report
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.cases.models import CommitResult,request_hash,validate_request
from airport_assistant.modules.drafts.models import DraftError,displayed_summary,seal
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.auth import AuthError


def case_setup():
    old,conv,draft,tx,outcomes,service=setup();principal=uuid4()
    actor=AccessIdentity(replace(old.session,principal_id=principal,kind='customer'))
    conv=replace(conv,customer_principal_id=principal,anonymous_session_id=None)
    data=report(contact_email='customer@example.com')
    draft=seal(replace(draft,actor_principal_id=principal,actor_session_id=None,payload=data,
        displayed_summary=displayed_summary('create_case',data,draft.destination,'id')))
    result=CommitResult(case_id=uuid4(),result_ref='CASE-'+uuid4().hex,message_id=uuid4())
    tx.find.return_value=conv;tx.find_draft.return_value=draft
    tx.lock_key=AsyncMock();tx.idempotency=AsyncMock(return_value=None)
    tx.lock_destination=AsyncMock(return_value=draft.destination);tx.verified_contact=AsyncMock(return_value=uuid4())
    tx.case_result=AsyncMock(return_value=result);tx.create_case=AsyncMock(return_value=result)
    tx.mark_committed=AsyncMock(return_value=True);tx.record_key=AsyncMock()
    @asynccontextmanager
    async def savepoint():yield
    tx.savepoint=savepoint
    return actor,draft,tx,outcomes,CaseService(service._repository),result


@pytest.mark.parametrize('key',[None,'','short','invalid key '+'x'*20,'x'*129,'x\n'+'a'*20])
def test_bad_retry_keys_are_rejected_before_database(key):
    with pytest.raises(DraftError) as exc:validate_request(1,'a'*64,key)
    assert exc.value.status==400


@pytest.mark.parametrize('version,hash_value',[(True,'a'*64),(0,'a'*64),(1,'bad'),(1,'é'*64)])
def test_invalid_review_arguments_cannot_reach_commit(version,hash_value):
    with pytest.raises(DraftError) as exc:validate_request(version,hash_value,str(uuid4()))
    assert exc.value.status==422


def test_request_digest_binds_draft_version_and_displayed_hash():
    id=uuid4();original=request_hash(id,1,'a'*64)
    assert original!=request_hash(uuid4(),1,'a'*64)
    assert original!=request_hash(id,2,'a'*64)
    assert original!=request_hash(id,1,'b'*64)


def test_confirm_records_only_after_locked_destination_and_verified_contact():
    async def scenario():
        actor,draft,tx,outcomes,service,result=case_setup();key=str(uuid4())
        assert await service.confirm(actor,draft.id,1,draft.payload_hash,key,uuid4())==result
        tx.lock_destination.assert_awaited_once_with(draft.destination)
        tx.verified_contact.assert_awaited_once_with('customer@example.com')
        assert tx.record_key.await_count==1 and outcomes==['commit']
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['version','hash','destination','key','contact'])
def test_invalid_confirmation_never_creates_a_case(change):
    async def scenario():
        actor,draft,tx,outcomes,service,result=case_setup()
        version=2 if change=='version' else 1;hash_value='0'*64 if change=='hash' else draft.payload_hash
        if change=='destination':tx.lock_destination.return_value=None
        if change=='key':tx.idempotency.return_value='0'*64
        if change=='contact':tx.verified_contact.return_value=None
        with pytest.raises(DraftError):await service.confirm(actor,draft.id,version,hash_value,str(uuid4()),uuid4())
        tx.create_case.assert_not_awaited();tx.record_key.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('existing_key',[True,False])
def test_committed_retry_after_deadline_returns_original_receipt(existing_key):
    async def scenario():
        actor,draft,tx,outcomes,service,result=case_setup()
        stored=replace(draft,state='committed',result_case_id=result.case_id,result_message_id=result.message_id,result_ref=result.result_ref,committed_at=draft.created_at)
        tx.find_draft.return_value=stored;tx.now.return_value=draft.expires_at+timedelta(days=1)
        tx.idempotency.return_value=request_hash(draft.id,1,draft.payload_hash) if existing_key else None
        assert await service.confirm(actor,draft.id,1,draft.payload_hash,str(uuid4()),uuid4())==result
        tx.create_case.assert_not_awaited();tx.mark_committed.assert_not_awaited()
        assert tx.record_key.await_count==(0 if existing_key else 1)
    asyncio.run(scenario())


def test_graph_deadline_rollback_commits_only_expired_marker():
    async def scenario():
        actor,draft,tx,outcomes,service,result=case_setup();rollbacks=[]
        tx.mark_committed.return_value=False
        tx.now.side_effect=[draft.created_at,draft.created_at,draft.expires_at+timedelta(seconds=1)]
        tx.terminal.return_value=replace(draft,state='expired')
        @asynccontextmanager
        async def savepoint():
            try:yield
            except BaseException:rollbacks.append(True);raise
        tx.savepoint=savepoint
        with pytest.raises(DraftError) as exc:await service.confirm(actor,draft.id,1,draft.payload_hash,str(uuid4()),uuid4())
        assert exc.value.status==410 and rollbacks==[True] and outcomes==['commit']
        tx.record_key.assert_not_awaited()
    asyncio.run(scenario())


def test_anonymous_booking_report_requires_verification_before_business_commit():
    async def scenario():
        actor,conv,draft,tx,outcomes,unused=setup()
        tx.lock_key=AsyncMock();tx.idempotency=AsyncMock(return_value=None)
        tx.lock_destination=AsyncMock(return_value=draft.destination);tx.create_case=AsyncMock()
        service=CaseService(unused._repository)
        with pytest.raises(AuthError) as exc:await service.confirm(actor,draft.id,1,draft.payload_hash,str(uuid4()),uuid4())
        assert exc.value.status==401;tx.create_case.assert_not_awaited()
    asyncio.run(scenario())


def test_confirm_wire_does_not_accept_actor_case_or_recipient_overrides():
    from airport_assistant.api.cases import ConfirmDraft
    body=dict(expected_version=1,payload_hash='a'*64)
    assert ConfirmDraft.model_validate(body).expected_version==1
    for extra in ({'case_id':str(uuid4())},{'contact_email':'other@example.com'},{'actor_principal_id':str(uuid4())},{'tenant_id':str(uuid4())}):
        with pytest.raises(ValidationError):ConfirmDraft.model_validate({**body,**extra})
