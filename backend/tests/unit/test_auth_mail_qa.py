"""Independent AUTH mail outcome, routing and no-network prerequisite checks."""
import asyncio
import base64
import json
import logging
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from test_auth import subject
from airport_assistant.mail_config import MailSettings, AuthMailRoute, WebhookConfig
from airport_assistant.modules.mail.verification import VerificationMail
from airport_assistant.modules.identity.auth import AuthError, VerificationMessage
from airport_assistant.integrations.mail.resend import ResendTransport
from airport_assistant import mail_check


@pytest.mark.parametrize('response_kind', ['server_error', 'rate_limit', 'redirect', 'malformed_200', 'response_lost'])
def test_ambiguous_real_adapter_response_retains_challenge_and_redacts_diagnostics(response_kind, caplog):
    async def scenario():
        service, repository, session, token, csrf = subject()
        route = AuthMailRoute(account_id=uuid4(), account_ref='qa-account', sender='support@example.com', reply_to='reply@example.com')
        routing = AsyncMock(); routing.allowed.return_value = True
        calls = []
        caplog.set_level(logging.DEBUG)
        def response(request):
            calls.append(request)
            if response_kind == 'response_lost':raise httpx.ReadError('Synthetic private transport diagnostic', request=request)
            code = {'server_error': 500, 'rate_limit': 429, 'redirect': 302, 'malformed_200': 200}[response_kind]
            return httpx.Response(code, content=b'{bad-json', headers={'location': 'https://untrusted.example/no-follow'},
                                  extensions={'reason_phrase': b'private-reason-sentinel 12345678 customer-private@example.com'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service._sender = VerificationMail({session.tenant_id: route}, ResendTransport({'qa-account': 'synthetic-private-key'}, client=client), routing)
            challenge_id = await service.request_challenge(session.tenant_id, token, csrf, 'customer-private@example.com', '127.0.0.1')
        assert challenge_id == repository.issue.call_args.args[3]
        assert len(calls) == 1 and calls[0].method == 'POST' and str(calls[0].url) == 'https://api.resend.com/emails'
        repository.cancel.assert_not_awaited()
        payload = json.loads(calls[0].content)
        assert payload['to'] == ['customer-private@example.com']
        for value in ('customer-private@example.com', 'synthetic-private-key', 'private-reason-sentinel', token, csrf):
            assert value not in caplog.text
        issued_code = payload['text'].split('Your verification code is ', 1)[1].split('.', 1)[0]
        assert issued_code not in caplog.text
    asyncio.run(scenario())


def test_two_tenants_select_distinct_credentials_and_idempotency_namespaces():
    async def scenario():
        tenants = [uuid4(), uuid4()]; challenge = uuid4(); calls = []
        routes = {tenant: AuthMailRoute(account_id=uuid4(), account_ref='qa-' + str(i), sender='support@example.com', reply_to='reply@example.com') for i, tenant in enumerate(tenants)}
        routing = AsyncMock(); routing.allowed.return_value = True
        def response(request):calls.append(request);return httpx.Response(200, json={'id': 'synthetic-receipt'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            sender = VerificationMail(routes, ResendTransport({'qa-0': 'key-zero', 'qa-1': 'key-one'}, client=client), routing)
            for tenant in tenants:await sender.send(VerificationMessage(challenge, tenant, 'recipient@example.com', '12345678'))
        assert [r.headers['authorization'] for r in calls] == ['Bearer key-zero', 'Bearer key-one']
        assert len({r.headers['idempotency-key'] for r in calls}) == 2
        assert [r.headers['idempotency-key'] for r in calls] == ['mail/' + tenant.hex + '/' + challenge.hex for tenant in tenants]
    asyncio.run(scenario())


@pytest.mark.parametrize('configured', [False, True])
def test_prerequisite_cli_is_secret_safe_and_does_not_invoke_network(configured, monkeypatch, capsys):
    tenant = uuid4(); secret = 'whsec_' + base64.b64encode(b'qa-webhook-prerequisite-key-00001').decode()
    route = AuthMailRoute(account_id=uuid4(), account_ref='qa-account', sender='sender-private@example.com', reply_to='reply-private@example.com')
    settings = MailSettings(_env_file=None) if not configured else MailSettings(_env_file=None,
        auth_enabled=True, enabled=True, ingress_enabled=True, receiving_enabled=True, reply_enabled=True,
        auth_routes={tenant: route}, accounts={'qa-account': 'private-api-key-sentinel'},
        webhooks={'airport-a': WebhookConfig(account_ref='qa-account', secrets=[secret])})
    monkeypatch.setattr(mail_check, 'load_mail_settings', lambda _: settings)
    monkeypatch.setattr('airport_assistant.config.load_settings', lambda _: SimpleNamespace(auth_enabled=configured, database_enabled=configured))
    monkeypatch.setattr('airport_assistant.auth_config.load_auth_settings', lambda _: SimpleNamespace())
    monkeypatch.setattr('sys.argv', ['mail-check'])
    def forbidden(*args, **kwargs):raise AssertionError('Prerequisite CLI attempted network')
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    if configured:mail_check.main()
    else:
        with pytest.raises(SystemExit) as error:mail_check.main()
        assert error.value.code == 2
    output = capsys.readouterr().out
    assert ('round-trip verification remains pending' in output) if configured else ('NOT READY' in output)
    for value in ('private-api-key-sentinel', secret, route.sender, route.reply_to):assert value not in output
