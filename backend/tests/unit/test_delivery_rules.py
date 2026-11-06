"""Status evidence and reconciliation never invoke a second send."""
import asyncio
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx
import pytest
from test_outbound_rules import fixture
from airport_assistant.modules.mail.delivery import DeliveryEvidence,ReconcileMail,delivery_transition
from airport_assistant.integrations.mail.resend import ResendTransport


@pytest.mark.parametrize('terminal',['delivered','bounced','failed'])
@pytest.mark.parametrize('incoming',['accepted','delivered','bounced','failed','delivery_unknown'])
def test_terminal_evidence_never_regresses(terminal,incoming):
    state,conflict=delivery_transition(terminal,incoming)
    assert state==terminal
    assert conflict==(incoming in {'delivered','bounced','failed'} and incoming!=terminal)


def test_stale_event_cannot_introduce_failure_or_false_conflict():
    now=datetime.now(timezone.utc)
    assert delivery_transition('delivered','bounced',current_at=now,event_at=now-timedelta(seconds=1))==('delivered',False)
    assert delivery_transition('accepted','delivery_unknown')==('accepted',False)


def setup():
    lease,data,_,_=fixture()
    from dataclasses import replace
    lease=replace(lease,kind='email_reconcile')
    repo=AsyncMock();transport=AsyncMock()
    snapshot=dict(tenant_id=str(lease.tenant_id),case_id=str(lease.case_id),id=str(uuid4()),delivery_version=1,
                  delivery_status='accepted',provider_message_id='receipt-1',provider_account_ref='account-a',
                  source_busy=False,attempts=1,max_attempts=5,sender='support@airport.example',recipient='customer@example.com')
    repo.delivery_snapshot.return_value=snapshot;repo.observe.return_value=True
    transport.lookup.return_value=DeliveryEvidence('receipt-1','delivered')
    return lease,snapshot,repo,transport


@pytest.mark.parametrize('change',[{'source_busy':True},{'provider_message_id':None},{'delivery_status':'delivered'}])
def test_busy_unknown_and_terminal_paths_never_post(change):
    lease,snapshot,repo,transport=setup();snapshot.update(change)
    asyncio.run(ReconcileMail(repo,transport,account_refs=('account-a',))(lease))
    transport.send.assert_not_awaited();transport.lookup.assert_not_awaited()


def test_lookup_error_retries_only_read_then_reviews_at_cap():
    lease,snapshot,repo,transport=setup();transport.lookup.side_effect=TimeoutError('secret')
    service=ReconcileMail(repo,transport,account_refs=('account-a',))
    assert asyncio.run(service(lease)).state=='retry'
    repo.observe.assert_not_awaited()
    snapshot['attempts']=5
    assert asyncio.run(service(lease)).state=='succeeded'
    assert repo.observe.call_args.args[-1]=='LOOKUP_UNAVAILABLE'
    transport.send.assert_not_awaited()


@pytest.mark.parametrize('status',[401,403,404,429,500,302])
def test_non_200_provider_get_is_not_failure_proof(status):
    async def scenario():
        _,snapshot,_,_=setup();calls=[]
        def handle(request):
            calls.append(request);assert request.method=='GET'
            return httpx.Response(status)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await ResendTransport({'account-a':'synthetic-key'},client=client).lookup(snapshot) is None
        assert len(calls)==1
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation',[{'id':'other'}, {'to':['foreign@example.com']},{'to':['customer@example.com','foreign@example.com']},
    {'from':'foreign@example.com'},{'from':'Name\r\nBcc: foreign@example.com'},{'cc':['other@example.com']},
    {'last_event':'opened'},{'last_event':'unrecognized'},{'message_id':'<bad\r\nheader>'}])
def test_mismatched_or_unrecognized_evidence_is_not_persistable(mutation):
    async def scenario():
        _,snapshot,_,_=setup()
        data={'object':'email','id':'receipt-1','to':['customer@example.com'],'from':'Airport <support@airport.example>',
              'last_event':'delivered','created_at':'2001-01-01T00:00:00Z','message_id':'<actual@provider.example>',**mutation}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=data))) as client:
            assert await ResendTransport('synthetic-key',client=client).lookup(snapshot) is None
    asyncio.run(scenario())


def test_normalized_provider_get_uses_actual_rfc_but_not_email_created_at():
    async def scenario():
        _,snapshot,_,_=setup()
        data={'object':'email','id':'receipt-1','to':['customer@example.com'],'from':'Airport <support@airport.example>',
              'last_event':'delivered','created_at':'2001-01-01T00:00:00Z','message_id':'<actual@provider.example>'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=data))) as client:
            evidence=await ResendTransport('synthetic-key',client=client).lookup(snapshot)
        assert evidence==DeliveryEvidence('receipt-1','delivered','<actual@provider.example>')
        assert evidence.event_at is None and evidence.record()['event_at'] is None
    asyncio.run(scenario())


def test_oversize_get_payload_cannot_create_evidence():
    async def scenario():
        _,snapshot,_,_=setup()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'x'*65537))) as client:
            assert await ResendTransport('synthetic-key',client=client).lookup(snapshot) is None
    asyncio.run(scenario())
