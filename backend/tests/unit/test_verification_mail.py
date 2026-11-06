"""MAIL-05 login mail routing and uncertain-delivery semantics."""
import asyncio,json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx,pytest
from airport_assistant.mail_config import MailSettings,AuthMailRoute
from airport_assistant.modules.mail.verification import VerificationMail,VerificationDeliveryError
from airport_assistant.modules.identity.auth import VerificationMessage,VerificationDeliveryUncertain
from airport_assistant.modules.mail.service import SendResult
from airport_assistant.integrations.mail.resend import ResendTransport
from airport_assistant.composition import build_verification_sender
from airport_assistant.mail_check import readiness
from test_auth import subject


def fixture():
    tenant=uuid4();route=AuthMailRoute(account_id=uuid4(),account_ref='account-a',sender='support@example.com',reply_to='reply@example.com')
    message=VerificationMessage(uuid4(),tenant,'customer@example.com','12345678',300)
    return tenant,route,message


@pytest.mark.parametrize('state',['accepted','failed','delivery_unknown'])
def test_one_post_exact_tenant_key_and_outcome(state,caplog):
    async def scenario():
        tenant,route,message=fixture();calls=[];routing=AsyncMock();routing.allowed.return_value=True
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'id':'receipt-test'}) if state=='accepted' else httpx.Response(422 if state=='failed' else 503)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            delivery=VerificationMail({tenant:route},ResendTransport({'account-a':'synthetic-a','account-b':'synthetic-b'},client=client),routing)
            if state=='accepted':await delivery.send(message)
            else:
                with pytest.raises(VerificationDeliveryError if state=='failed' else VerificationDeliveryUncertain):await delivery.send(message)
        assert len(calls)==1 and calls[0].headers['authorization']=='Bearer synthetic-a'
        assert calls[0].headers['idempotency-key']=='mail/'+tenant.hex+'/'+message.challenge_id.hex
        payload=json.loads(calls[0].content)
        assert payload['to']==[message.recipient] and message.code in payload['text']
        assert message.code not in caplog.text and message.recipient not in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('failure',['foreign_tenant','inactive','malformed_code'])
def test_rejected_route_makes_zero_transport_calls(failure):
    async def scenario():
        tenant,route,message=fixture();transport=AsyncMock();routing=AsyncMock();routing.allowed.return_value=failure!='inactive'
        if failure=='foreign_tenant':message=replace(message,tenant_id=uuid4())
        if failure=='malformed_code':message=replace(message,code='123456')
        with pytest.raises(VerificationDeliveryError):await VerificationMail({tenant:route},transport,routing).send(message)
        transport.send.assert_not_awaited()
    asyncio.run(scenario())


def test_unknown_delivery_retains_same_browser_challenge_without_resend():
    async def scenario():
        sender=AsyncMock();sender.send.side_effect=VerificationDeliveryUncertain()
        service,repo,session,token,csrf=subject(sender)
        id=await service.request_challenge(session.tenant_id,token,csrf,'customer@example.com','127.0.0.1')
        assert id==sender.send.await_args.args[0].challenge_id
        repo.cancel.assert_not_awaited();assert sender.send.await_count==1
    asyncio.run(scenario())


def test_cancelled_inflight_delivery_keeps_pending_and_propagates():
    async def scenario():
        started=asyncio.Event();sender=AsyncMock()
        async def send(message):started.set();await asyncio.Event().wait()
        sender.send.side_effect=send
        service,repo,session,token,csrf=subject(sender)
        task=asyncio.create_task(service.request_challenge(session.tenant_id,token,csrf,'customer@example.com','127.0.0.1'))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        repo.cancel.assert_not_awaited();assert sender.send.await_count==1
    asyncio.run(scenario())


def test_config_is_disabled_and_readiness_never_means_provider_verified():
    assert build_verification_sender(MailSettings()) is None
    assert 'APP_MAIL_AUTH_ENABLED' in readiness(MailSettings())
    with pytest.raises(ValueError):MailSettings(auth_enabled=True)
    tenant,route,_=fixture()
    with pytest.raises(ValueError):MailSettings(auth_enabled=True,auth_routes={tenant:route},accounts={'other':'synthetic'})
