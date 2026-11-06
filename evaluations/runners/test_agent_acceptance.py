"""Bounded retry and acceptance artifacts with synthetic HTTP only."""
import asyncio
from datetime import datetime,timedelta,timezone
from email.utils import format_datetime
import json
from decimal import Decimal
import httpx
import pytest
import agent_selection as e
import agent_acceptance_v3 as final
from agent_transport import retry_after
from test_agent_selection import network


class Clock:
    def __init__(self):self.value=0
    def __call__(self):return self.value
    async def sleep(self,seconds):self.value+=seconds


@pytest.fixture
def scope(tmp_path,monkeypatch):
    for attr in ('DATA','CONFIG','OUT','source_hashes','baseline'):monkeypatch.setattr(e,attr,getattr(e,attr))
    monkeypatch.setattr(final,'OUT',tmp_path)
    final.configure()
    import dotenv
    monkeypatch.setattr(dotenv,'dotenv_values',lambda *a:pytest.fail('offline test accessed env'))
    return asyncio.run(e.prepare())


def run(scope,transport,**kwargs):
    clock=Clock()
    result=asyncio.run(final.live(scope['manifest_hash'],'0.75',key='synthetic-key-marker',network=transport,
                                  clock=clock,sleep=clock.sleep,jitter=lambda:0,**kwargs))
    return e.OUT/result['run_folder'],result


def grade(folder):
    packet=json.loads((folder/'review_packet.json').read_text(encoding='utf-8'))
    e.save(folder/'test-review.json',dict(packet_hash=packet['packet_hash'],reviewer={'type':'agent','id':'synthetic-test'},items=[
        dict(review_id=i['review_id'],meaning_ok=True,language_ok=True,unsupported_fact=False,note='Synthetic harness grade, not semantic evidence') for i in packet['items']]))
    return final.assess(folder,folder/'test-review.json')


def test_manifest_reserves_primary_and_worst_case_retry_cost_and_new_families(scope):
    assert len(scope['jobs'])==72 and scope['max_http_attempts']==80
    maximum=max(Decimal(r['reservation_usd']) for r in scope['jobs'])
    assert Decimal(scope['retry_reservation_usd'])==8*maximum
    assert Decimal(scope['reservation_usd'])==sum((Decimal(r['reservation_usd']) for r in scope['jobs']),Decimal(0))+8*maximum
    assert Decimal(scope['reservation_usd'])<=Decimal('0.75')
    data,_=e.load();old=json.loads((e.ROOT/'evaluations/datasets/agent_selection_v2.json').read_text(encoding='utf-8'))
    assert not {r['report'] for r in data['cases']} & {r['report'] for r in old['cases']}
    assert len({r['family'] for r in data['cases'] if r['split']=='holdout'})==13


def test_retry_success_keeps_first_failure_and_first_vs_eventual_grades(scope):
    transport,calls=network(e.load()[0]);count=[]
    async def handle(request):
        count.append(1)
        if len(count)==7:raise httpx.ReadTimeout('synthetic-key-marker secretdetail',request=request)
        return await transport.handle_async_request(request)
    folder,result=run(scope,httpx.MockTransport(handle))
    assert result['http_requests_sent']==73 and result['retry_attempts']==1 and not result['stop_reason']
    attempts=json.loads((folder/'http_attempts.json').read_text(encoding='utf-8'))
    assert attempts[6]['status']=='rejected' and attempts[7]['status']=='completed'
    assert attempts[6]['canonical_digest']==attempts[7]['canonical_digest']
    assessment=grade(folder)['candidates']['openai']
    assert assessment['first_attempt_holdout_complete']==51 and assessment['holdout_complete']==52
    assert assessment['cost']['unknown_usage_attempts']==1
    assert assessment['cost']==final.request_cost(attempts)
    assert all(r.url.host=='api.openai.com' for r in calls)
    with pytest.raises(FileExistsError):run(scope,transport)


def test_three_physical_transient_failures_open_circuit_preserve_denominators(scope):
    calls=[]
    def handle(request):calls.append(request);raise httpx.ConnectError('private detail',request=request)
    folder,result=run(scope,httpx.MockTransport(handle))
    assert len(calls)==3 and result['retry_attempts']==1 and result['stop_reason']=='transient_circuit_open'
    rows=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    assert len(rows)==72 and sum(r['sent'] for r in rows)==2
    assessment=grade(folder)['candidates']['openai']
    assert assessment['planned']==52 and not assessment['eligible']


@pytest.mark.parametrize('failure',[400,401,403,'quota','malformed','identity'])
def test_permanent_failures_never_retry(scope,failure):
    calls=[]
    def handle(request):
        calls.append(request)
        if failure=='quota':return httpx.Response(429,json={'error':{'code':'insufficient_quota'}})
        if failure=='malformed':return httpx.Response(200,json=[])
        if failure=='identity':return httpx.Response(200,json={'model':'other-model','status':'completed'})
        return httpx.Response(failure,text='private secret detail')
    _,result=run(scope,httpx.MockTransport(handle))
    assert len(calls)==1 and result['retry_attempts']==0 and result['stop_reason']


def test_retry_after_respected_and_long_wait_stops(scope):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(429,headers={'Retry-After':'31'},json={'error':{'code':'rate_limit_exceeded'}})
    _,result=run(scope,httpx.MockTransport(handle))
    assert len(calls)==1 and result['stop_reason']=='retry_after_outside_window'
    assert retry_after('12')==12 and retry_after('nonsense') is None
    date=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=20))
    assert 18<=retry_after(date)<=20


def test_retry_allowance_is_global_not_per_case(scope):
    transport,_=network(e.load()[0]);calls=[]
    retry_reports=set()
    async def handle(request):
        calls.append(request)
        report=json.loads(request.content)['input'][-1]['content']
        if report not in retry_reports:
            retry_reports.add(report)
            raise httpx.ReadTimeout('temporary',request=request)
        return await transport.handle_async_request(request)
    folder,result=run(scope,httpx.MockTransport(handle))
    assert result['retry_attempts']==8 and result['http_requests_sent']<=80
    attempts=json.loads((folder/'http_attempts.json').read_text(encoding='utf-8'))
    assert sum(r['try']==2 for r in attempts)==8
    assert all(sum(r['job_id']==job['job_id'] for r in attempts)<=2 for job in scope['jobs'])


@pytest.mark.parametrize('kind',['scope','cap','saved'])
def test_unauthorized_scope_cannot_load_env_or_claim(scope,kind):
    if kind=='saved':
        changed=dict(scope);changed['cap_usd']='5';e.save(final.OUT/'prepared_manifest.json',changed)
    with pytest.raises(e.SafeFailure):
        asyncio.run(final.live('invalid' if kind=='scope' else scope['manifest_hash'],'1.00' if kind=='cap' else '0.75'))
    assert not list(final.OUT.glob('claimed-*'))


def test_http_ledger_tampering_is_rejected_before_assessment(scope):
    transport,_=network(e.load()[0]);folder,_=run(scope,transport)
    rows=json.loads((folder/'http_attempts.json').read_text(encoding='utf-8'));rows.pop();e.save(folder/'http_attempts.json',rows)
    with pytest.raises(e.SafeFailure,match='http_attempts_changed'):grade(folder)


def test_retry_after_delay_is_honored_before_second_dispatch(scope):
    transport,_=network(e.load()[0]);clock=Clock();times=[]
    async def handle(request):
        times.append(clock())
        if len(times)==1:return httpx.Response(429,headers={'Retry-After':'20'},json={'error':{'code':'rate_limit_exceeded'}})
        return await transport.handle_async_request(request)
    result=asyncio.run(final.live(scope['manifest_hash'],'0.75',key='synthetic-key-marker',network=httpx.MockTransport(handle),clock=clock,sleep=clock.sleep,jitter=lambda:0))
    assert result['http_requests_sent']==73 and times[1]-times[0]>=20


@pytest.mark.parametrize('problem',['auth_body_timeout','bad_body_503','oversize_503','credential_body_503','invalid_key_503'])
def test_nonretryable_signals_override_transient_status_end_to_end(scope,problem):
    calls=[]
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadTimeout('sensitive detail')
            yield b''
    def handle(request):
        calls.append(request)
        if problem=='auth_body_timeout':return httpx.Response(401,stream=Body())
        if problem=='oversize_503':return httpx.Response(503,content=b'x'*262145)
        if problem=='credential_body_503':return httpx.Response(503,content=b'synthetic-key-marker')
        if problem=='invalid_key_503':return httpx.Response(503,json={'error':{'code':'invalid_api_key'}})
        # Arbitrary error bodies are not malformed success envelopes; retryable HTTP503 still allowed.
        return httpx.Response(200,content=b'not-json')
    _,result=run(scope,httpx.MockTransport(handle))
    assert len(calls)==1 and result['retry_attempts']==0 and result['stop_reason']


def test_deadline_leaves_all_logical_jobs_visible_without_dispatch(scope):
    clock=Clock();seen=[]
    def now():
        seen.append(1)
        return 0 if len(seen)==1 else 1190
    transport,calls=network(e.load()[0])
    result=asyncio.run(final.live(scope['manifest_hash'],'0.75',key='synthetic-key-marker',network=transport,clock=now,sleep=clock.sleep))
    assert result['http_requests_sent']==0 and calls==[] and result['stop_reason']=='run_deadline'
    rows=json.loads((e.OUT/result['run_folder']/'attempts.json').read_text(encoding='utf-8'))
    assert len(rows)==72


def test_semantically_wrong_valid_output_is_never_retried(scope):
    calls=[]
    def handle(request):
        calls.append(request)
        body=json.loads(request.content)
        return httpx.Response(200,json={'model':body['model'],'status':'completed','output':[
            {'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{"question":"Which reference?"}'}]}]})
    _,result=run(scope,httpx.MockTransport(handle))
    assert len(calls)==1 and result['retry_attempts']==0 and result['stop_reason']=='preflight_mechanical_failure'
