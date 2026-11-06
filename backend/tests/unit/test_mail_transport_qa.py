"""Provider routing and untrusted HTTP diagnostics stay behind the mail port."""
import asyncio
from dataclasses import replace
import logging
import httpx
from test_outbound_rules import fixture
from airport_assistant.modules.mail.service import OutboundMail
from airport_assistant.integrations.mail.resend import ResendTransport


def test_provider_account_selects_its_own_credential_without_cross_routing():
    async def scenario():
        lease, snapshot, repo, transport = fixture()
        envelope = OutboundMail(repo, transport, account_ref='account-a')._envelope(lease, snapshot)
        calls = []

        def response(request):
            calls.append(request)
            return httpx.Response(200, json={'id': 'qa-provider-id'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            adapter = ResendTransport({'account-a': 'synthetic-key-a', 'account-b': 'synthetic-key-b'}, client=client)
            assert (await adapter.send(envelope)).state == 'accepted'
            assert (await adapter.send(replace(envelope, provider_account_ref='account-b'))).state == 'accepted'
            assert (await adapter.send(replace(envelope, provider_account_ref='unconfigured'))).state == 'failed'
        assert len(calls) == 2
        assert [request.headers['authorization'] for request in calls] == ['Bearer synthetic-key-a', 'Bearer synthetic-key-b']
        assert calls[0].headers['Idempotency-Key'] == calls[1].headers['Idempotency-Key'] == envelope.delivery_key
    asyncio.run(scenario())


def test_provider_http_reason_phrase_cannot_leak_into_general_logs(caplog, monkeypatch):
    # Exercise standalone mail startup; unrelated source-client initialization
    # must not be required to install mail HTTP-log redaction.
    logger = logging.getLogger('httpx')
    monkeypatch.setattr(logger, 'filters', [])
    sensitive = 'synthetic-private-recipient@example.com'

    async def scenario():
        lease, snapshot, repo, transport = fixture()
        envelope = OutboundMail(repo, transport, account_ref='account-a')._envelope(lease, snapshot)

        def response(request):
            return httpx.Response(500, extensions={'reason_phrase': sensitive.encode()})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            assert (await ResendTransport('synthetic-key', client=client).send(envelope)).state == 'delivery_unknown'

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    assert sensitive not in caplog.text
