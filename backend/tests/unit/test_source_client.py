"""Protocol and failure bounds without trusting provider payloads."""

import asyncio
import base64
from datetime import datetime,timezone,timedelta
import hashlib
import hmac
import json
import logging
from uuid import uuid4

import httpx
import pytest
from airport_assistant.integrations.source.client import SourceHttpClient
from airport_assistant.modules.source.models import SourceCaller,SourceFailure
from airport_assistant.source_config import SourceSettings
from airport_assistant.config import ConfigurationError

TOKEN='fake-source-token-'+'x'*40
SECRET='fake-context-key-'+'y'*40
A=uuid4();B=uuid4()
STAMP='2026-11-02T01:00:00Z'


def booking():return dict(reference='PK-A-001',service_type='parking',status='confirmed',start_at='2026-11-10T00:00:00Z',end_at='2026-11-11T00:00:00Z',service_timezone='Asia/Jakarta',location='T2',policy_reference='parking-v2',source_updated_at=STAMP,observed_at=STAMP,allowed_scopes=['booking:read'])


def transaction():return dict(reference='TX-A-001',status='pending',source_updated_at=STAMP,observed_at=STAMP,allowed_scopes=['payment_status:read'])


class Bytes(httpx.AsyncByteStream):
    def __init__(self,body,chunks=None,delay=0):self.body=body;self.chunks=chunks;self.delay=delay;self.closed=False
    async def __aiter__(self):
        for chunk in self.chunks or [self.body]:
            if self.delay:await asyncio.sleep(self.delay)
            yield chunk
    async def aclose(self):self.closed=True


def response(value,status=200,headers=None):
    body=json.dumps(value).encode() if not isinstance(value,bytes) else value
    return httpx.Response(status,headers={'Content-Type':'application/json',**(headers or {})},stream=Bytes(body))


def run(coroutine):return asyncio.run(coroutine)


def client(handler,**options):return SourceHttpClient('https://source.example.test',TOKEN,SECRET,transport=httpx.MockTransport(handler),**options)


def test_signs_actual_url_and_never_forwards_customer_cookie_or_claims():
    requests=[]
    def handler(req):
        requests.append(req)
        claims=json.loads(base64.urlsafe_b64decode(req.headers['x-source-context']+'='*((-len(req.headers['x-source-context']))%4)))
        assert claims['tenant_id']==str(A) and claims['subject']=='owner' and claims['aud']=='airport-reference-v1'
        assert claims['exp']-claims['iat']==60
        message=b'source-v1\nGET\n'+req.url.raw_path.partition(b'?')[0]+b'\n'+req.url.query+b'\n'+req.headers['x-source-context'].encode()
        assert req.headers['x-source-signature']==hmac.new(SECRET.encode(),message,hashlib.sha256).hexdigest()
        assert req.headers['authorization']=='Bearer '+TOKEN and 'cookie' not in req.headers
        return response({'items':[],'next_cursor':None})
    async def scenario():
        c=client(handler)
        try:
            await c.relations(SourceCaller(A,'owner'),limit=1,cursor='opaque+/-_=cursor')
            await c.relations(SourceCaller(A,'owner'),limit=1,cursor='opaque+/-_=cursor')
            assert requests[0].url.host=='source.example.test' and b'cursor=opaque%2B' in requests[0].url.query
            assert requests[0].headers['x-source-context']!=requests[1].headers['x-source-context']
        finally:await c.aclose()
        assert c._client.is_closed
    run(scenario())


@pytest.mark.parametrize('status,expected',[(404,404),(400,400),(401,503),(403,503),(408,503),(429,503),(500,503),(503,503),(301,502),(302,502),(307,502),(204,502),(422,502)])
def test_http_status_mapping_without_retry_or_redirect(status,expected):
    calls=[]
    def handler(req):calls.append(req);return response({'secret':TOKEN},status,{'Location':'https://evil.example.test'})
    async def scenario():
        c=client(handler)
        try:
            with pytest.raises(SourceFailure) as err:await c.booking(SourceCaller(A,'owner'),'PK-A-001')
            assert err.value.status==expected and TOKEN not in str(err.value) and len(calls)==1
        finally:await c.aclose()
    run(scenario())


@pytest.mark.parametrize('fault',['json','duplicate','html','gzip','oversize_header','oversize_stream','reference','extra','scope','money','datetime','timezone','interval'])
def test_invalid_source_response_fails_closed(fault):
    value=booking();headers={}
    if fault=='json':value=b'not-json'
    elif fault=='duplicate':value=b'{"reference":"PK-A-001","reference":"OTHER"}'
    elif fault=='html':headers['Content-Type']='text/html'
    elif fault=='gzip':headers['Content-Encoding']='gzip'
    elif fault=='oversize_header':headers['Content-Length']='300000'
    elif fault=='oversize_stream':value=b'x'*262145
    elif fault=='reference':value['reference']='PK-B-999'
    elif fault=='extra':value['customer_secret']=TOKEN
    elif fault=='scope':value['allowed_scopes']=['payment_details:read']
    elif fault=='money':value['amount_minor']=100
    elif fault=='datetime':value['source_updated_at']='invalid'
    elif fault=='timezone':value['service_timezone']='Invalid/Zone'
    else:value['end_at']=value['start_at']
    async def scenario():
        c=client(lambda req:response(value,headers=headers))
        try:
            with pytest.raises(SourceFailure) as err:await c.booking(SourceCaller(A,'owner'),'PK-A-001')
            assert err.value.status==502
        finally:await c.aclose()
    run(scenario())


@pytest.mark.parametrize('extra',[{'amount_minor':10},{'currency':None},{'refund_status':'none'},{'currency_exponent':0}])
def test_status_only_upstream_cannot_smuggle_payment_detail_fields(extra):
    async def scenario():
        c=client(lambda req:response({**transaction(),**extra}))
        try:
            with pytest.raises(SourceFailure) as err:await c.transaction(SourceCaller(A,'beneficiary'),'TX-A-001')
            assert err.value.status==502
        finally:await c.aclose()
    run(scenario())


def test_unknown_status_is_normalized_and_status_only_output_keeps_fields_absent():
    async def scenario():
        c=client(lambda req:response({**transaction(),'status':'provider_review'}))
        try:
            result=await c.transaction(SourceCaller(A,'beneficiary'),'TX-A-001')
            value=result.model_dump(mode='json')
            assert value['status']=='unknown' and value['source_status_label']=='provider_review'
            assert 'amount_minor' not in value and 'currency' not in value
        finally:await c.aclose()
    run(scenario())


@pytest.mark.parametrize('fault',['duplicate','wrong_parent','over_limit','missing_cursor','long_cursor'])
def test_transaction_pages_are_bound_and_bounded(fault):
    value={**transaction(),'booking_reference':'PK-A-001'}
    page={'items':[value],'next_cursor':None}
    if fault=='duplicate':page['items']=[value,value]
    elif fault=='wrong_parent':page['items'][0]['booking_reference']='OTHER'
    elif fault=='over_limit':page['items']=[value,{**value,'reference':'TX-A-002'}]
    elif fault=='missing_cursor':page.pop('next_cursor')
    else:page['next_cursor']='x'*2049
    async def scenario():
        c=client(lambda req:response(page))
        try:
            with pytest.raises(SourceFailure) as err:await c.booking_transactions(SourceCaller(A,'owner'),'PK-A-001',limit=1)
            assert err.value.status==502
        finally:await c.aclose()
    run(scenario())


@pytest.mark.parametrize('exception',[httpx.ConnectError('secret'),httpx.ReadTimeout('secret'),httpx.PoolTimeout('secret')])
def test_transport_failures_are_unavailable(exception):
    async def handler(req):raise exception
    async def scenario():
        c=client(handler)
        try:
            with pytest.raises(SourceFailure) as err:await c.booking(SourceCaller(A,'owner'),'PK-A-001')
            assert err.value.status==503 and 'secret' not in str(err.value)
        finally:await c.aclose()
    run(scenario())


def test_overall_deadline_and_cancel_close_stream_and_client_can_be_reused():
    async def scenario():
        slow=Bytes(b'',chunks=[b'{',b'}'],delay=.1)
        replies=[httpx.Response(200,headers={'Content-Type':'application/json'},stream=slow),response(booking())]
        c=client(lambda req:replies.pop(0),timeout=.02)
        try:
            with pytest.raises(SourceFailure) as err:await c.booking(SourceCaller(A,'owner'),'PK-A-001')
            assert err.value.status==503 and slow.closed
            c._timeout=5
            assert (await c.booking(SourceCaller(A,'owner'),'PK-A-001')).reference=='PK-A-001'
        finally:await c.aclose()
        slow2=Bytes(b'',chunks=[b'{}'],delay=1)
        c=client(lambda req:httpx.Response(200,headers={'Content-Type':'application/json'},stream=slow2))
        try:
            task=asyncio.create_task(c.booking(SourceCaller(A,'owner'),'PK-A-001'))
            await asyncio.sleep(.01);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert slow2.closed
        finally:await c.aclose()
    run(scenario())


def test_http_logs_do_not_contain_request_or_response_secrets(caplog):
    caplog.set_level(logging.DEBUG)
    async def scenario():
        c=client(lambda req:response(booking()))
        try:
            logging.getLogger('httpcore.http11').debug('headers=%s',TOKEN)
            logging.getLogger('httpx').info('url=%s',SECRET)
            await c.booking(SourceCaller(A,'owner'),'PK-A-001')
        finally:await c.aclose()
    run(scenario())
    assert TOKEN not in caplog.text and SECRET not in caplog.text and 'PK-A-001' not in caplog.text


@pytest.mark.parametrize('origin',['http://source.example.test','http://localhost:8010','https://user:pass@source.test','https://source.test/path','https://source.test?key=secret','https://source.test#frag','https://source.test:0'])
def test_config_rejects_unclosed_or_insecure_origins(origin):
    with pytest.raises(Exception):SourceSettings(SOURCE_API_BASE_URL=origin,SOURCE_API_TOKEN=TOKEN,context_secret=SECRET,tenant_map={A:B},allow_loopback_http=True)


def test_tenant_map_rejects_aliases_duplicates_and_many_to_one():
    base=dict(SOURCE_API_BASE_URL='https://source.test',SOURCE_API_TOKEN=TOKEN,context_secret=SECRET)
    for value in [{A:B,uuid4():B},'{"'+str(A)+'":"'+str(B)+'","'+str(A)+'":"'+str(uuid4())+'"}',{str(A).replace('-',''):str(B)}]:
        with pytest.raises(Exception):SourceSettings(**base,tenant_map=value)



@pytest.mark.parametrize('status',[None,True,123,'','bad status'])
def test_missing_or_malformed_status_is_invalid_not_unknown(status):
    async def scenario():
        c=client(lambda req:response({**transaction(),'status':status}))
        try:
            with pytest.raises(SourceFailure) as error:await c.transaction(SourceCaller(A,'owner'),'TX-A-001')
            assert error.value.status==502
        finally:await c.aclose()
    run(scenario())


def test_unexpected_provider_cookie_cannot_cross_customers():
    seen=[]
    def handler(req):
        seen.append(req)
        return response(booking(),headers={'Set-Cookie':'provider-user-secret=one; Path=/'}) if len(seen)==1 else response(booking())
    async def scenario():
        c=client(handler)
        try:
            with pytest.raises(SourceFailure) as error:await c.booking(SourceCaller(A,'owner'),'PK-A-001')
            assert error.value.status==502
            assert (await c.booking(SourceCaller(B,'beneficiary'),'PK-A-001')).reference=='PK-A-001'
            assert all('cookie' not in req.headers for req in seen) and not c._client.cookies
        finally:await c.aclose()
    run(scenario())



@pytest.mark.parametrize('fault',['single_missing','page_missing','refund_null','refund_empty'])
def test_missing_status_in_page_and_invalid_refund_details_fail_closed(fault):
    value=transaction()
    if fault in {'single_missing','page_missing'}:value.pop('status')
    else:value.update(allowed_scopes=['payment_status:read','payment_details:read'],currency='IDR',amount_minor=100,currency_exponent=0,refund_status=None if fault=='refund_null' else '')
    async def scenario():
        body={'items':[value],'next_cursor':None} if fault=='page_missing' else value
        c=client(lambda req:response(body))
        try:
            with pytest.raises(SourceFailure) as error:
                if fault=='page_missing':await c.booking_transactions(SourceCaller(A,'owner'),'PK-A-001')
                else:await c.transaction(SourceCaller(A,'owner'),'TX-A-001')
            assert error.value.status==502
        finally:await c.aclose()
    run(scenario())



@pytest.mark.parametrize('key',['a'*31+'\n','a'*31+'海'])
def test_configuration_rejects_non_header_safe_source_keys(key):
    with pytest.raises(Exception):SourceSettings(SOURCE_API_BASE_URL='https://source.test',SOURCE_API_TOKEN=key,context_secret=SECRET,tenant_map={A:B})


def test_malformed_upstream_http_protocol_is_invalid_response():
    async def handler(req):raise httpx.RemoteProtocolError('private-upstream-detail')
    async def scenario():
        c=client(handler)
        try:
            with pytest.raises(SourceFailure) as error:await c.transaction(SourceCaller(A,'owner'),'TX-A-001')
            assert error.value.status==502 and 'private-upstream-detail' not in str(error.value)
        finally:await c.aclose()
    run(scenario())
