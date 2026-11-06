"""AI-04 harness checks with synthetic network/keys, never live provider calls."""
import asyncio,copy,json
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest
import agent_selection as e
from agent_transport import Bridge,normalize_gemini,wire
from airport_assistant.modules.agent.models import AgentError


def response_for(row):
    expected=row['expected'];task=row['task']
    if task=='plan':
        if expected['kind']=='tool':return None,dict(name=expected['name'],arguments=json.dumps({'reference':expected['reference']}))
        return {'question':'Which service needs help?' if row['language']=='en' else 'Layanan apa yang perlu dibantu?'},None
    if task=='answer':return expected,None
    if task=='draft':return dict(category=expected['category'],service_type=expected['service_type'],summary=row['report'][:100]),None
    return {'text':row['report']},None  # Literal fixture only; not translation quality evidence.


def network(data,*,failure=None,usage=None):
    by_report={r['report']:r for r in data['cases']};calls=[]
    def handle(req):
        calls.append(req);body=json.loads(req.content)
        provider='openai' if req.url.host=='api.openai.com' else 'gemini'
        if failure:return httpx.Response(failure,text='synthetic-key-marker-must-not-leak')
        row=by_report[(body['input'] if provider=='openai' else body['messages'])[-1]['content']]
        output,call=response_for(row)
        if provider=='openai':
            item={'type':'function_call','status':'completed','call_id':'call_test','name':call['name'],'arguments':call['arguments']} if call else {'type':'message','status':'completed','role':'assistant','content':[{'type':'output_text','text':json.dumps(output)}]}
            return httpx.Response(200,json={'model':body['model'],'status':'completed','output':[item],'usage':{'input_tokens':100,'output_tokens':30} if usage is None else usage})
        message={'role':'assistant','content':None,'tool_calls':[{'id':'call_test','type':'function','function':call}]} if call else {'role':'assistant','content':json.dumps(output)}
        return httpx.Response(200,json={'model':body['model']+'-001','choices':[{'message':message,'finish_reason':'tool_calls' if call else 'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':30} if usage is None else usage})
    return httpx.MockTransport(handle),calls


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    monkeypatch.setattr(e,'OUT',tmp_path)
    return asyncio.run(e.prepare())


def test_prepare_is_offline_reproducible_frozen_and_covers_same_schedule(prepared,monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv,'dotenv_values',lambda *a:pytest.fail('prepare must not read credentials'))
    again=asyncio.run(e.prepare(persist=False))
    assert again==prepared and len(again['jobs'])==144
    assert float(again['reservation_usd'])<1.0
    data,_=e.load()
    for row in data['cases']:
        body=again['canonical_payloads'][row['id']]
        assert body['input'][-1]['content']==row['report'] and 'expected' not in body
        assert body['store'] is False and body['max_output_tokens']==1024
    jobs=again['jobs'];assert len({j['job_id'] for j in jobs})==144
    assert sum(j['stage']=='preflight' for j in jobs)==8


def test_gold_positions_are_counterbalanced_and_families_do_not_cross_splits():
    data,_=e.load();splits={};misses=0
    for row in data['cases']:
        assert splits.setdefault(row['family'],row['split'])==row['split']
        if row['task']=='answer' and row['expected']['outcome']=='answer':
            misses+=not e.score(row,{'outcome':'answer','source_ids':['source_1']})['mechanical_success']
    assert len(splits)==17 and misses>=4


@pytest.mark.parametrize('cap',['0','NaN','Infinity','1e3','1.01'])
def test_wrong_cap_never_contacts_provider_or_claims_authorization(prepared,cap):
    transport,calls=network(e.load()[0])
    with pytest.raises(e.SafeFailure):asyncio.run(e.live(prepared['manifest_hash'],cap,network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'}))
    assert calls==[] and not list(e.OUT.glob('claimed-*'))


def test_wrong_manifest_and_missing_keys_stop_before_claim(prepared):
    with pytest.raises(e.SafeFailure):asyncio.run(e.live('wrong','1.00',keys={}))
    with pytest.raises(e.SafeFailure):asyncio.run(e.live(prepared['manifest_hash'],'1.00',keys={}))
    assert not list(e.OUT.glob('claimed-*'))


def test_full_mock_run_retains_records_review_and_one_shot_claim(prepared):
    data,config=e.load();transport,calls=network(data)
    async def no_wait(seconds):pass
    folder=asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'},sleep=no_wait))
    records=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    assert len(calls)==len(records)==144 and all(r['status']=='completed' for r in records)
    journal=[json.loads(line) for line in (folder/'journal.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(v['event']=='reserved' for v in journal)==144
    assert sum(v['event']=='result' for v in journal)==144
    packet=json.loads((folder/'review_packet.json').read_text(encoding='utf-8'))
    assert any(i['case']['task']=='answer' and i['rendered_answer'] for i in packet['items'])
    assert all('provider' not in i for i in packet['items'])
    review={'packet_hash':packet['packet_hash'],'reviewer':{'type':'agent','id':'synthetic-test-reviewer'},'items':[
        {'review_id':i['review_id'],'meaning_ok':True,'language_ok':True,'unsupported_fact':False,'note':'Synthetic harness test only.'} for i in packet['items']]}
    review_file=folder/'synthetic.review.json';e.save(review_file,review)
    assessment=e.assess(folder,review_file)
    assert assessment['candidates']['openai']['eligible'] and assessment['candidates']['gemini']['eligible']
    assert not assessment['candidates']['baseline']['eligible'] and assessment['automatic_winner'] is False
    assert assessment['reviewer']['type']=='agent'
    with pytest.raises(e.SafeFailure,match='authorization_already_used'):asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'},sleep=no_wait))
    # A missing or modified grade is never silently accepted as a passing review.
    review['items'].pop();e.save(review_file,review)
    with pytest.raises(e.SafeFailure,match='review_incomplete'):e.assess(folder,review_file)


@pytest.mark.parametrize('status',[401,403,404,429,500])
def test_provider_failure_has_no_retry_and_not_run_cases_stay_in_denominator(prepared,status):
    transport,calls=network(e.load()[0],failure=status)
    async def no_wait(seconds):pass
    folder=asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'},sleep=no_wait))
    assert len(calls)==2
    records=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    assert len(records)==144 and sum(r['sent'] for r in records)==2
    summary=json.loads((folder/'report.json').read_text(encoding='utf-8'))['summary']
    assert summary['gemini']['holdout_planned']==52 and summary['gemini']['holdout_mechanical_success']==0
    assert 'synthetic-key-marker-must-not-leak' not in (folder/'attempts.json').read_text(encoding='utf-8')


def test_missing_usage_retains_reservation_without_claiming_zero_cost(prepared):
    transport,calls=network(e.load()[0],usage={})
    async def no_wait(seconds):pass
    folder=asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'},sleep=no_wait))
    records=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    assert all(r['reported_usage'] is None and float(r['reservation_usd'])>0 for r in records)
    assert all('nominal_usage_cost_usd' not in r for r in records)


def test_excess_usage_stops_even_if_output_would_parse(prepared):
    transport,calls=network(e.load()[0],usage={'input_tokens':100,'output_tokens':1025,'prompt_tokens':100,'completion_tokens':1025})
    with pytest.raises(e.SafeFailure,match='usage_exceeds_reservation'):
        asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'}))
    assert len(calls)==1


def test_excess_usage_is_checked_even_when_content_is_parser_rejected(prepared):
    calls=[]
    def handle(req):
        calls.append(req);body=json.loads(req.content)
        return httpx.Response(200,json={'model':body['model'],'status':'completed',
            'output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'invalid JSON'}]}],
            'usage':{'input_tokens':100,'output_tokens':1025}})
    with pytest.raises(e.SafeFailure,match='usage_exceeds_reservation'):
        asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=httpx.MockTransport(handle),keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'}))
    assert len(calls)==1


def test_run_deadline_blocks_all_new_requests_and_keeps_denominators(prepared):
    transport,calls=network(e.load()[0]);ticks=[0]
    def clock():
        value=ticks[0];ticks[0]=1200;return value
    folder=asyncio.run(e.live(prepared['manifest_hash'],'1.00',network=transport,keys={'openai':'synthetic-openai-key','gemini':'synthetic-gemini-key'},clock=clock))
    assert calls==[]
    records=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    assert len(records)==144 and all(r['status']=='not_run_run_deadline' for r in records)


@pytest.mark.parametrize('change',['multiple','length','refusal','mixed_text'])
def test_gemini_normalization_rejects_ambiguous_or_incomplete_outputs(change):
    value={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'{}'}}]}
    if change=='multiple':value['choices']*=2
    if change=='length':value['choices'][0]['finish_reason']='length'
    if change=='refusal':value['choices'][0]['message']['refusal']='No'
    if change=='mixed_text':value['choices'][0]['message']['tool_calls']=[{'id':'call_test','type':'function','function':{'name':'read_booking','arguments':'{}'}}]
    with pytest.raises(ValueError):normalize_gemini(value,'local-envelope')


def test_scoring_rejects_wrong_scope_and_incomplete_qualifier_selection():
    rows=e.load()[0]['cases'];plan=next(r for r in rows if r['family']=='plan-correction')
    assert e.score(plan,{'kind':'tool','tool':{'name':'read_booking','arguments':{'reference':'PK-OLD-001'}}})['critical']
    answer=next(r for r in rows if r['family']=='answer-chunk-boundary')
    assert e.score(answer,{'outcome':'answer','source_ids':answer['expected']['source_ids'][:1]})['critical']


def test_safe_abstention_and_exact_negative_reason_agreement_are_separate_metrics():
    data,_=e.load();cases={r['id']:r for r in data['cases']}
    row=next(r for r in data['cases'] if r['family']=='answer-missing-injection')
    output={'outcome':'conflict','source_ids':[]}
    assert e.score(row,output)['mechanical_success']
    records=[{'case_id':row['id'],'output':output},{'case_id':row['id'],'output':None}]
    assert e.reason_agreement(records,cases)=={'correct':0,'planned':8,'unavailable':1}
