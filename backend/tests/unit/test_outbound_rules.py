"""Recipient authorization precedes provider execution; no stored grant reuse."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import httpx
from airport_assistant.modules.jobs.service import Lease
from airport_assistant.modules.mail.service import OutboundMail, SendResult, Envelope, address
from airport_assistant.integrations.mail.resend import ResendTransport
from airport_assistant.mail_config import MailSettings


def fixture():
    lease = Lease(uuid4(), uuid4(), uuid4(), uuid4(), 'case_created', 1, 1, uuid4(), uuid4())
    data = dict(tenant_id=str(lease.tenant_id), case_id=str(lease.case_id), case_version=1,
                public_reference='CASE-' + uuid4().hex, app_message_id=str(uuid4()), language='en',
                contact_id=str(uuid4()), principal_id=str(uuid4()), recipient='customer@example.com',
                tenant_active=True, account_active=True, principal_active=True, verified=True, participant_active=True,
                account_id=str(uuid4()), provider_account_ref='account-a', sender='support@airport.example',
                reply_to='reply@airport.example', labels=[], delivery_status=None)
    data['followup_contact_id'] = data['contact_id']
    data['owner_principal_id'] = data['principal_id']
    repo = AsyncMock()
    repo.snapshot.return_value = data
    repo.begin.return_value = repo.complete.return_value = True
    transport = AsyncMock()
    transport.send.return_value = SendResult('accepted', 'provider-123')
    return lease, data, repo, transport


@pytest.mark.parametrize('mutation', [
    {'tenant_active': False}, {'account_active': False}, {'principal_active': False}, {'verified': False},
    {'participant_active': False}, {'contact_id': None}, {'followup_contact_id': str(uuid4())},
    {'owner_principal_id': str(uuid4())}, {'provider_account_ref': 'other-account'}, {'tenant_id': str(uuid4())},
    {'case_id': str(uuid4())}, {'recipient': 'bad\r\nBcc: other@example.com'}, {'labels': None},
])
def test_unavailable_recipient_or_authority_never_calls_provider(mutation):
    lease, data, repo, transport = fixture()
    data.update(mutation)
    result = asyncio.run(OutboundMail(repo, transport, account_ref='account-a')(lease))
    assert result.state == 'blocked'
    repo.begin.assert_not_awaited()
    transport.send.assert_not_awaited()


def test_changed_state_after_grant_check_is_blocked():
    lease, data, repo, transport = fixture()
    repo.snapshot.side_effect = [data, {**data, 'participant_active': False}]
    assert asyncio.run(OutboundMail(repo, transport, account_ref='account-a')(lease)).state == 'blocked'
    transport.send.assert_not_awaited()


@pytest.mark.parametrize('state', ['sending', 'failed', 'delivery_unknown'])
def test_persisted_attempt_is_not_resent(state):
    lease, data, repo, transport = fixture()
    data['delivery_status'] = state
    assert asyncio.run(OutboundMail(repo, transport, account_ref='account-a')(lease)).state == 'unknown'
    transport.send.assert_not_awaited()


def test_success_ids_are_stable_and_separate_and_ack_is_generic():
    lease, data, repo, transport = fixture()
    service = OutboundMail(repo, transport, account_ref='account-a')
    assert asyncio.run(service(lease)).state == 'succeeded'
    envelope = transport.send.call_args.args[0]
    assert envelope.id != envelope.app_message_id
    assert envelope.delivery_key.startswith('mail/' + lease.tenant_id.hex + '/')
    assert envelope == service._envelope(lease, data)
    assert 'portal' not in envelope.text.lower()
    assert data['recipient'] not in repr(envelope)


def test_exception_after_send_is_uncertain_not_retry():
    lease, data, repo, transport = fixture()
    transport.send.side_effect = TimeoutError('secret-sentinel')
    assert asyncio.run(OutboundMail(repo, transport, account_ref='account-a')(lease)).state == 'unknown'
    assert repo.complete.call_args.args[2] == SendResult('delivery_unknown')


def test_reservation_recheck_failure_does_not_send():
    lease, data, repo, transport = fixture()
    repo.begin.return_value = False
    assert asyncio.run(OutboundMail(repo, transport, account_ref='account-a')(lease)).state == 'blocked'
    transport.send.assert_not_awaited()


def test_private_source_grants_are_fresh_and_storage_stays_fail_closed():
    lease, data, repo, transport = fixture()
    data['labels'] = [{'source':'reference','resource_type':'booking','reference':'B123','required_source_scopes':['booking:read']}]
    access = AsyncMock()
    access.allows.return_value = True
    service = OutboundMail(repo, transport, account_ref='account-a', private_access=access)
    assert asyncio.run(service(lease)).state == 'blocked'
    assert asyncio.run(service(lease)).state == 'blocked'
    assert access.allows.await_count == 2
    transport.send.assert_not_awaited()


@pytest.mark.parametrize('status,body,state', [(200, {'id':'receipt-1'}, 'accepted'), (200, {'id':None}, 'delivery_unknown'),
    (200, {}, 'delivery_unknown'), (302, {}, 'delivery_unknown'), (500, {}, 'delivery_unknown'), (429, {}, 'delivery_unknown'), (403, {}, 'failed')])
def test_http_adapter_contract_and_no_hidden_retries(status, body, state):
    async def scenario():
        lease, data, repo, transport = fixture()
        envelope = OutboundMail(repo, transport, account_ref='account-a')._envelope(lease, data)
        calls = []
        def handle(request):
            calls.append(request)
            assert request.url == 'https://api.resend.com/emails'
            assert request.headers['Idempotency-Key'] == envelope.delivery_key
            return httpx.Response(status, json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await ResendTransport('private-sentinel', client=client).send(envelope)
        assert result.state == state and len(calls) == 1
    asyncio.run(scenario())


def test_bounded_provider_body_and_transport_timeout_are_unknown():
    async def scenario():
        lease, data, repo, transport = fixture()
        envelope = OutboundMail(repo, transport, account_ref='account-a')._envelope(lease, data)
        for callback in (lambda r:httpx.Response(200, content=b'x'*4097), lambda r:(_ for _ in ()).throw(httpx.ReadTimeout('secret'))):
            async with httpx.AsyncClient(transport=httpx.MockTransport(callback)) as client:
                assert (await ResendTransport('secret',client=client).send(envelope)).state == 'delivery_unknown'
    asyncio.run(scenario())


def test_disabled_default_and_enabled_requires_explicit_account_secret():
    assert not MailSettings(_env_file=None).enabled
    with pytest.raises(ValueError):
        MailSettings(enabled=True, _env_file=None)


def test_composition_binds_multiple_account_routes_without_network():
    from airport_assistant.composition import build_job_handlers
    from types import SimpleNamespace
    settings=MailSettings(enabled=True,accounts={'account-a':'secret-a','account-b':'secret-b'},_env_file=None)
    scheduler=type('Scheduler',(),{})()
    database=SimpleNamespace()
    handlers=build_job_handlers(database,mail_settings=settings,transport=AsyncMock(),scheduler=scheduler)
    assert database.mail_delivery_required is True
    assert set(handlers)=={'case_created','email_send','email_reconcile'}
    assert set(scheduler.mail_accounts)=={'account-a','account-b'}
    assert handlers['case_created'] is handlers['email_send']
    assert 'secret-a' not in repr(settings)
    assert build_job_handlers(object(),mail_settings=MailSettings(_env_file=None),scheduler=scheduler)=={}
    assert scheduler.mail_accounts==()


def test_transport_selects_only_trusted_envelope_account_credential():
    async def scenario():
        lease,data,repo,transport=fixture()
        envelope=OutboundMail(repo,transport,account_ref='account-a')._envelope(lease,data)
        requests=[]
        def handle(request):
            requests.append(request)
            assert request.headers['Authorization']=='Bearer secret-a'
            return httpx.Response(200,json={'id':'receipt-a'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter=ResendTransport({'account-a':'secret-a','account-b':'secret-b'},client=client)
            assert (await adapter.send(envelope)).state=='accepted'
            assert (await adapter.send(replace(envelope,provider_account_ref='unconfigured'))).state=='failed'
        assert len(requests)==1
    asyncio.run(scenario())
