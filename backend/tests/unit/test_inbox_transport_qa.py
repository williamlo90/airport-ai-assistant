"""Independent receive boundary probes; no external HTTP or credentials."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from airport_assistant.composition import ApplicationServices, build_job_handlers
from airport_assistant.config import Settings
from airport_assistant.integrations.mail.resend import ResendTransport
from airport_assistant.mail_config import MailSettings
from airport_assistant.main import create_app
from airport_assistant.modules.system.health import ProcessHealthService
from airport_assistant.modules.jobs.service import Lease
from airport_assistant.modules.mail.inbox import InboxWorker


@pytest.mark.parametrize('field', ['In-Reply-To', 'References', 'Reply-To'])
def test_duplicate_case_insensitive_correlation_headers_are_rejected(field):
    async def scenario():
        data = {'object': 'email', 'id': 'receipt-qa', 'from': 'customer@example.com', 'to': ['reply@airport.example'],
                'subject': 'Help', 'text': 'Untrusted', 'html': None,
                'headers': {field: '<first@customer.example>', field.lower(): '<second@other.example>'}}
        calls = []
        def response(request):
            calls.append(request)
            return httpx.Response(200, json=data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            assert await ResendTransport({'qa-account': 'synthetic-key'}, client=client).receive({
                'provider_message_id': 'receipt-qa', 'provider_account_ref': 'qa-account', 'inbound_address': 'reply@airport.example'}) is None
        assert len(calls) == 1 and calls[0].method == 'GET'
    asyncio.run(scenario())


def test_chunked_webhook_stops_at_64k_before_reading_remaining_body():
    async def scenario():
        ingress = SimpleNamespace(receive=AsyncMock())
        app = create_app(Settings(env='test', max_request_body_bytes=2*1024*1024),
                         services=ApplicationServices(health=ProcessHealthService(), email_ingress=ingress))
        consumed = []
        async def chunks():
            consumed.append('first')
            yield b'x' * 65537
            consumed.append('second')
            raise AssertionError('Oversize webhook continued reading the stream')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            response = await client.post('/integrations/email/airport-a/events', content=chunks(), headers={'content-type': 'application/json'})
        assert response.status_code == 413 and consumed == ['first']
        ingress.receive.assert_not_awaited()
    asyncio.run(scenario())


def test_receiver_only_composition_enables_inbox_without_send_handlers():
    settings = MailSettings(enabled=False, receiving_enabled=True, accounts={'qa-account': 'synthetic-key'}, _env_file=None)
    database = SimpleNamespace(); scheduler = SimpleNamespace()
    handlers = build_job_handlers(database, mail_settings=settings, transport=AsyncMock(), scheduler=scheduler)
    assert set(handlers) == {'email_fetch', 'email_event'}
    assert scheduler.mail_accounts == ('qa-account',)
    assert database.mail_inbox_required is True and database.mail_delivery_required is True


def test_provider_attachment_urls_are_never_requested_or_retained():
    async def scenario():
        data = {'object': 'email', 'id': 'receipt-qa', 'from': 'customer@example.com', 'to': ['reply@airport.example'],
                'subject': 'Help', 'text': None, 'html': '<img src="cid:private">', 'headers': {},
                'raw': {'download_url': 'http://169.254.169.254/private?secret=synthetic'},
                'attachments': [{'download_url': 'https://untrusted.example/private', 'content_id': 'private'}]}
        calls = []
        def response(request):
            calls.append(request)
            assert str(request.url) == 'https://api.resend.com/emails/receiving/receipt-qa?html_format=cid'
            return httpx.Response(200, json=data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            result = await ResendTransport({'qa-account': 'synthetic-key'}, client=client).receive({
                'provider_message_id': 'receipt-qa', 'provider_account_ref': 'qa-account', 'inbound_address': 'reply@airport.example'})
        assert result is not None and result.attachment_count == 1
        assert len(calls) == 1 and 'download_url' not in str(result.record())
    asyncio.run(scenario())


@pytest.mark.parametrize('latest_provider_event', ['opened', 'clicked'])
def test_signed_delivery_callback_survives_newer_tracking_status(latest_provider_event):
    async def scenario():
        case_id, draft_id = uuid4(), uuid4()
        lease = Lease(uuid4(), uuid4(), None, None, 'email_event', 1, 1, uuid4(), uuid4())
        event_at = datetime.now(timezone.utc).isoformat()
        repository = AsyncMock(); mail = AsyncMock()
        repository.snapshot.return_value = dict(provider_account_ref='qa-account', processing_state='received',
            provider_message_id='receipt-qa', event_type='email.delivered', event_at=event_at, attempts=1, max_attempts=1)
        repository.bind.return_value = dict(case_id=str(case_id), draft_id=str(draft_id))
        repository.complete.return_value = True
        mail.delivery_snapshot.return_value = dict(provider_account_ref='qa-account', provider_message_id='receipt-qa',
            sender='support@airport.example', recipient='customer@example.com', source_busy=False, delivery_status='accepted')
        mail.observe.return_value = True
        data = {'object': 'email', 'id': 'receipt-qa', 'from': 'Airport <support@airport.example>',
                'to': ['customer@example.com'], 'message_id': '<actual@provider.example>', 'last_event': latest_provider_event}
        calls = []
        def response(request):
            calls.append(request)
            assert str(request.url) == 'https://api.resend.com/emails/receipt-qa' and request.method == 'GET'
            return httpx.Response(200, json=data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            transport = ResendTransport({'qa-account': 'synthetic-key'}, client=client)
            result = await InboxWorker(repository, mail, transport, accounts=('qa-account',))(lease)
        assert result.state == 'succeeded' and len(calls) == 1
        mail.observe.assert_awaited_once()
        evidence = mail.observe.call_args.args[2]
        assert evidence.state == 'delivered' and evidence.event_at.isoformat() == event_at
        assert repository.complete.call_args.args[-1] is None
    asyncio.run(scenario())
