"""Independent AUTH delivery checks; real Neon and controlled HTTP only."""
import asyncio
from dataclasses import replace
import json
import os
import re
from uuid import UUID, uuid4
import httpx
import pytest
from psycopg import sql
from test_auth_mail_delivery import (
    target, reply_target, inbox_target, delivery_target, mail_target, worker_target,
    publication_target, handoff_target, events_target, files_target, lifecycle_target,
    commit_target, adoption_target, draft_target, conversation_target, auth_target, inbox_app,
)
from test_staff_auth import run, row, change
from airport_assistant.infrastructure.database.connections import RuntimeDatabase, DatabaseError
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.verification_mail import PostgresVerificationRouting
from airport_assistant.mail_config import AuthMailRoute
from airport_assistant.modules.mail.verification import VerificationMail, VerificationDeliveryError
from airport_assistant.modules.identity.auth import VerificationMessage
from airport_assistant.integrations.mail.resend import ResendTransport

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in independent AUTH mail proof, not live provider.')


def route(t):
    return AuthMailRoute(account_id=t['mail_account'], account_ref='test-account', sender='support@airport.example', reply_to='reply@airport.example')


def parse(request):
    payload = json.loads(request.content)
    return UUID(request.headers['idempotency-key'].rsplit('/', 1)[1]), re.search(r'\b[0-9]{8}\b', payload['text'])[0]


async def issue(h, client, email):
    return await client.post('/api/v1/auth/challenges', headers=h.headers(client), json=dict(contact=email, purpose='customer_login'))


async def verify(h, client, id, value, **kwargs):
    return await client.post('/api/v1/auth/challenges/' + str(id) + '/verify', headers=kwargs.get('headers', h.headers(client)), json=dict(code=value))


def test_route_requires_every_current_field_and_inactive_tenant_or_account_never_posts(target):
    async def scenario():
        async with inbox_app(target) as (h, _):
            current = route(target); routing = PostgresVerificationRouting(h.db, target['schema']); calls = []
            def response(request):calls.append(request);return httpx.Response(200, json={'id': 'synthetic-receipt'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                adapter = ResendTransport({'test-account': 'synthetic-key'}, client=client)
                message = VerificationMessage(uuid4(), target['a'], 'unverified-new-contact@example.com', '12345678')
                assert await routing.allowed(target['a'], current)
                for field, value in (('account_id', uuid4()), ('account_ref', 'foreign-account'), ('sender', 'foreign@example.com'), ('reply_to', 'foreign-reply@example.com')):
                    wrong = current.model_copy(update={field: value})
                    assert await routing.allowed(target['a'], wrong) is False
                    with pytest.raises(VerificationDeliveryError):await VerificationMail({target['a']: wrong}, adapter, routing).send(message)
                assert await routing.allowed(target['b'], current) is False
                with pytest.raises(VerificationDeliveryError):await VerificationMail({target['b']: current}, adapter, routing).send(replace(message, tenant_id=target['b']))
                for table, id in (('email_accounts', target['mail_account']), ('tenants', target['a'])):
                    change(target, 'UPDATE {s}.' + table + ' SET active=false WHERE id=:id', id=id)
                    try:
                        assert await routing.allowed(target['a'], current) is False
                        with pytest.raises(VerificationDeliveryError):await VerificationMail({target['a']: current}, adapter, routing).send(message)
                    finally:change(target, 'UPDATE {s}.' + table + ' SET active=true WHERE id=:id', id=id)
                assert calls == []
                await VerificationMail({target['a']: current}, adapter, routing).send(message)
                assert len(calls) == 1
    run(scenario())


def test_timeout_after_post_preserves_original_browser_csrf_and_tenant_bound_code(target):
    async def scenario():
        async with inbox_app(target) as (h, _):
            requests = []; entered = asyncio.Event()
            async def response(request):requests.append(request);entered.set();await asyncio.Event().wait()
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                h.auth._sender = VerificationMail({target['a']: route(target)}, ResendTransport({'test-account': 'synthetic-key'}, client=client), PostgresVerificationRouting(h.db, target['schema']))
                h.auth._policy = replace(h.auth._policy, delivery_timeout=1)
                async with h.client() as original, h.client() as foreign, h.client('b.example.test') as other_tenant:
                    for browser in (original, foreign, other_tenant):await h.boot(browser)
                    result = await asyncio.wait_for(issue(h, original, 'timeout-' + uuid4().hex + '@example.com'), 8)
                    assert result.status_code == 202 and entered.is_set() and len(requests) == 1
                    id, value = parse(requests[0]); assert result.json()['challenge_id'] == str(id)
                    assert row(target, 'SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id', id=id)[0] is None
                    assert (await verify(h, foreign, id, value)).status_code == 422
                    assert (await verify(h, other_tenant, id, value)).status_code in (401, 422)
                    bad_headers = h.headers(original); bad_headers['X-CSRF-Token'] = 'z'*43
                    assert (await verify(h, original, id, value, headers=bad_headers)).status_code == 403
                    assert (await verify(h, original, id, value)).status_code == 200
                    assert len(requests) == 1
    run(scenario())


def test_cancelled_request_after_post_keeps_code_usable_without_retrying_delivery(target):
    async def scenario():
        async with inbox_app(target) as (h, _):
            requests = []; entered = asyncio.Event()
            async def response(request):requests.append(request);entered.set();await asyncio.Event().wait()
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                h.auth._sender = VerificationMail({target['a']: route(target)}, ResendTransport({'test-account': 'synthetic-key'}, client=client), PostgresVerificationRouting(h.db, target['schema']))
                async with h.client() as browser:
                    await h.boot(browser)
                    pending = asyncio.create_task(issue(h, browser, 'cancel-' + uuid4().hex + '@example.com'))
                    await asyncio.wait_for(entered.wait(), 5); pending.cancel()
                    with pytest.raises(asyncio.CancelledError):await pending
                    id, value = parse(requests[0])
                    assert row(target, 'SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id', id=id)[0] is None
                    assert (await verify(h, browser, id, value)).status_code == 200
                    assert len(requests) == 1
    run(scenario())


def test_inactive_route_definitively_cancels_challenge_and_performs_zero_http(target):
    async def scenario():
        async with inbox_app(target) as (h, _):
            calls = []
            def response(request):calls.append(request);return httpx.Response(200, json={'id': 'synthetic-receipt'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                h.auth._sender = VerificationMail({target['a']: route(target)}, ResendTransport({'test-account': 'synthetic-key'}, client=client), PostgresVerificationRouting(h.db, target['schema']))
                async with h.client() as browser:
                    await h.boot(browser); email = 'inactive-' + uuid4().hex + '@example.com'
                    change(target, 'UPDATE {s}.email_accounts SET active=false WHERE id=:id', id=target['mail_account'])
                    try:
                        assert (await issue(h, browser, email)).status_code == 503
                        assert calls == []
                        assert row(target, 'SELECT v.consumed_at FROM {s}.verification_challenges v JOIN {s}.contacts c ON c.tenant_id=v.tenant_id AND c.id=v.contact_id WHERE c.email_address=:e', e=email)[0] is not None
                    finally:change(target, 'UPDATE {s}.email_accounts SET active=true WHERE id=:id', id=target['mail_account'])
    run(scenario())


def test_new_user_requests_supersede_uncertain_codes_and_rate_limit_further_posts(target):
    async def scenario():
        async with inbox_app(target) as (h, _):
            calls = []
            def response(request):calls.append(request);return httpx.Response(503)
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                h.auth._sender = VerificationMail({target['a']: route(target)}, ResendTransport({'test-account': 'synthetic-key'}, client=client), PostgresVerificationRouting(h.db, target['schema']))
                async with h.client() as browser:
                    await h.boot(browser); email = 'supersede-' + uuid4().hex + '@example.com'
                    for _ in range(3):assert (await issue(h, browser, email)).status_code == 202
                    assert (await issue(h, browser, email)).status_code == 429
                    assert len(calls) == 3
                    old, old_code = parse(calls[0]); latest, latest_code = parse(calls[-1])
                    assert row(target, 'SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id', id=old)[0] is not None
                    assert (await verify(h, browser, old, old_code)).status_code == 422
                    assert (await verify(h, browser, latest, latest_code)).status_code == 200
                    assert len(calls) == 3
    run(scenario())


def test_route_capability_does_not_expand_api_raw_grants_or_worker_capabilities(target):
    async def scenario():
        async with inbox_app(target) as (h, _):
            async with h.db.transaction() as connection:
                assert (await(await connection.execute(sql.SQL('SELECT count(*) FROM {}.email_accounts').format(sql.Identifier(target['schema'])))).fetchone())[0] == 0
            for statement in ('SELECT body FROM {s}.email_messages', 'SELECT hashed_code FROM {s}.verification_challenges', 'UPDATE {s}.email_accounts SET active=true'):
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction() as c:await c.execute(sql.SQL(statement).format(s=sql.Identifier(target['schema'])))
                assert error.value.sqlstate == '42501'
            worker = WorkerDatabase(target['worker_url'], schema=target['schema'])
            try:
                await worker.open()
                with pytest.raises(DatabaseError) as error:
                    async with worker.transaction() as c:await c.execute(sql.SQL('SELECT {}.auth_mail_route_allowed(NULL,NULL,NULL,NULL,NULL)').format(sql.Identifier(target['schema'])))
                assert error.value.sqlstate == '42501'
            finally:await worker.close()
    run(scenario())


def test_revoked_routing_capability_fails_closed_before_transport_and_auth_startup(target):
    async def scenario():
        function = '"' + target['schema'] + '".auth_mail_route_allowed(uuid,uuid,text,text,text)'; role = '"' + target['role'] + '"'
        async with inbox_app(target) as (h, _):
            calls = []
            def response(request):calls.append(request);return httpx.Response(200, json={'id': 'synthetic-receipt'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                h.auth._sender = VerificationMail({target['a']: route(target)}, ResendTransport({'test-account': 'synthetic-key'}, client=client), PostgresVerificationRouting(h.db, target['schema']))
                async with h.client() as browser:
                    await h.boot(browser)
                    with target['engine'].begin() as db:db.exec_driver_sql('REVOKE EXECUTE ON FUNCTION ' + function + ' FROM ' + role)
                    try:
                        assert (await issue(h, browser, 'denied-' + uuid4().hex + '@example.com')).status_code == 503
                        assert calls == []
                        restricted = RuntimeDatabase(target['url']); restricted.auth_mail_required = True
                        try:
                            with pytest.raises(DatabaseError):await restricted.open()
                        finally:await restricted.close()
                    finally:
                        with target['engine'].begin() as db:db.exec_driver_sql('GRANT EXECUTE ON FUNCTION ' + function + ' TO ' + role)
    run(scenario())
