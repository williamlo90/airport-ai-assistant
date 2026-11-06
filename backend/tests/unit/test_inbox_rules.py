"""Raw signature and quarantine rules tested without credential/provider calls."""
import asyncio,base64,hashlib,hmac,json,time
from datetime import datetime,timezone
from unittest.mock import AsyncMock
from dataclasses import replace
from uuid import uuid4
import httpx
import pytest
from airport_assistant.modules.mail.inbox import verify,normalize,WebhookIngress,InboxWorker,ReceivedContent
from airport_assistant.mail_config import MailSettings,WebhookConfig
from airport_assistant.modules.system.health import ProcessHealthService
from airport_assistant.composition import ApplicationServices
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.integrations.mail.resend import ResendTransport
from test_outbound_rules import fixture

KEY='whsec_'+base64.b64encode(b'unit-synthetic-signing-key-000001').decode()


def body(kind='email.received'):
    return json.dumps({'type':kind,'created_at':datetime.now(timezone.utc).isoformat(),'data':{'email_id':'receipt-1','tenant_id':'ignored'}}).encode()


def signed(raw,*,stamp=None,key=KEY):
    headers={'svix-id':'msg_123','svix-timestamp':str(int(time.time()) if stamp is None else stamp)}
    message=headers['svix-id'].encode()+b'.'+headers['svix-timestamp'].encode()+b'.'+raw
    headers['svix-signature']='v1,'+base64.b64encode(hmac.new(base64.b64decode(key[6:]),message,hashlib.sha256).digest()).decode()
    return headers


@pytest.mark.parametrize('change',['body','id','timestamp','key','old','future','invalid-base64'])
def test_signature_binds_raw_bytes_id_timestamp_and_key(change):
    raw=body();headers=signed(raw);keys=[KEY]
    assert verify(raw,headers,keys)
    if change=='body':raw+=b' '
    elif change=='id':headers['svix-id']='msg_other'
    elif change=='timestamp':headers['svix-timestamp']=str(int(headers['svix-timestamp'])+1)
    elif change=='key':keys=['whsec_'+base64.b64encode(b'x'*32).decode()]
    elif change=='old':headers=signed(raw,stamp=int(time.time())-301)
    elif change=='future':headers=signed(raw,stamp=int(time.time())+302)
    else:headers['svix-signature']='v1,not-base64!'
    assert not verify(raw,headers,keys)


def test_rotation_and_multiple_signatures_accept_only_one_matching_v1():
    raw=body();headers=signed(raw)
    headers['svix-signature']='v2,ignored v1,'+base64.b64encode(b'x'*32).decode()+' '+headers['svix-signature']
    assert verify(raw,headers,['whsec_'+base64.b64encode(b'y'*32).decode(),KEY])
    assert normalize(raw)['provider_message_id']=='receipt-1'
    assert 'tenant_id' not in normalize(raw)


@pytest.mark.parametrize('raw',[b'{"type":"email.received","type":"email.sent"}',b'[]',b'{',
    b'{"type":"email.received","created_at":"2026-01-01","data":{"email_id":"id"}}'])
def test_malformed_or_duplicate_keys_cannot_be_normalized(raw):
    with pytest.raises(Exception):normalize(raw)


def test_http_boundary_rejects_duplicate_headers_and_storage_failure_is_non_2xx():
    async def scenario():
        repo=AsyncMock();repo.accept.return_value='accepted'
        service=WebhookIngress(repo,{'airport-a':WebhookConfig(account_ref='account-a',secrets=[KEY])})
        app=create_app(Settings(env='test'),services=ApplicationServices(health=ProcessHealthService(),email_ingress=service))
        raw=body();sig=signed(raw)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://example.test') as client:
            for name in ('svix-id','svix-timestamp','svix-signature'):
                headers=[('content-type','application/json'),*sig.items(),(name,sig[name])]
                assert (await client.post('/integrations/email/airport-a/events',content=raw,headers=headers)).status_code==401
            repo.accept.assert_not_awaited()
            response=await client.post('/integrations/email/airport-a/events',content=raw,headers={'content-type':'application/json',**sig})
            assert response.status_code==202 and response.json()=={'accepted':True}
            assert response.headers['cache-control']=='no-store'
            repo.accept.side_effect=RuntimeError('private-sentinel')
            response=await client.post('/integrations/email/airport-a/events',content=raw,headers={'content-type':'application/json',**sig})
            assert response.status_code==500 and 'private-sentinel' not in response.text
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation',[{'id':'wrong-id'},{'to':['foreign@example.com']},{'from':'bad\r\nheader'},
    {'text':'x'*65537},{'cc':['other@example.com']},{'raw':{'download_url':'https://attacker.example/secret'},'to':['foreign@example.com']}])
def test_received_fetch_is_fixed_bounded_and_identity_routed(mutation):
    async def scenario():
        snap={'provider_message_id':'receipt-1','provider_account_ref':'account-a','inbound_address':'reply@airport.example'}
        data={'object':'email','id':'receipt-1','from':'Customer <customer@example.com>','to':['reply@airport.example'],
              'subject':'Question','text':'Hello','html':None,'message_id':'<rfc@customer.example>',**mutation}
        calls=[]
        def response(request):
            calls.append(request);assert str(request.url)=='https://api.resend.com/emails/receiving/receipt-1?html_format=cid'
            return httpx.Response(200,json=data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            assert await ResendTransport({'account-a':'synthetic-key'},client=client).receive(snap) is None
        assert len(calls)==1
    asyncio.run(scenario())


def test_html_headers_and_attachment_metadata_are_staged_without_fetching_urls():
    async def scenario():
        snap={'provider_message_id':'receipt-1','provider_account_ref':'account-a','inbound_address':'reply@airport.example'}
        data={'object':'email','id':'receipt-1','from':'Customer <customer@example.com>','to':['reply@airport.example'],
              'subject':'','text':None,'html':'<script>bad()</script><p>Hello</p>','message_id':'<rfc@customer.example>',
              'headers':{'In-Reply-To':'<original@airport.example>','References':'<old@airport.example>\r\n <original@airport.example>'},
              'raw':{'download_url':'https://attacker.example/raw'},'attachments':[{'download_url':'https://attacker.example/file'}],
              'authentication':{'spf':'pass','dkim':'pass','dmarc':'pass'}}
        calls=[]
        def response(request):calls.append(request);return httpx.Response(200,json=data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            content=await ResendTransport('synthetic-key',client=client).receive(snap)
        assert isinstance(content,ReceivedContent) and content.html==data['html'] and content.attachment_count==1
        assert '\r' not in content.references and content.authentication['dmarc']=='pass'
        assert len(calls)==1 and 'download_url' not in content.record()
    asyncio.run(scenario())


def test_receiving_worker_retry_and_review_never_send_or_publish():
    lease,_,_,_=fixture();lease=replace(lease,kind='email_fetch',case_id=None,draft_id=None)
    repo=AsyncMock();mail=AsyncMock();transport=AsyncMock()
    snap=dict(provider_account_ref='account-a',processing_state='received',existing_message_id=None,attempts=1,max_attempts=2)
    repo.snapshot.return_value=snap;repo.complete.return_value=True;transport.receive.return_value=None
    worker=InboxWorker(repo,mail,transport,accounts=('account-a',))
    assert asyncio.run(worker(lease)).state=='retry'
    repo.complete.assert_not_awaited()
    snap['attempts']=2
    assert asyncio.run(worker(lease)).state=='succeeded'
    assert repo.complete.call_args.args[-1]=='FETCH_UNAVAILABLE'
    transport.send.assert_not_awaited();mail.observe.assert_not_awaited()


def test_mail_flags_are_opt_in_and_keys_never_enter_repr():
    settings=MailSettings(_env_file=None)
    assert not settings.ingress_enabled and not settings.receiving_enabled
    with pytest.raises(ValueError):MailSettings(ingress_enabled=True,_env_file=None)
    with pytest.raises(ValueError):MailSettings(receiving_enabled=True,_env_file=None)
    cfg=WebhookConfig(account_ref='account-a',secrets=[KEY]);assert KEY not in repr(cfg)
