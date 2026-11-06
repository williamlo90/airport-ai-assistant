"""Translation scope safety and denominators, never real language-quality scores."""
import asyncio
import json
from decimal import Decimal
import httpx
import pytest
import agent_selection as e
import agent_acceptance_v3 as bounded
import translation_acceptance as selected
from test_agent_selection import network
from test_agent_acceptance import Clock


@pytest.fixture
def scope(tmp_path,monkeypatch):
    for attr in ('DATA','CONFIG','OUT','source_hashes','baseline'):monkeypatch.setattr(e,attr,getattr(e,attr))
    monkeypatch.setattr(bounded,'OUT',bounded.OUT)
    monkeypatch.setattr(selected,'OUT',tmp_path)
    selected.configure()
    import dotenv
    monkeypatch.setattr(dotenv,'dotenv_values',lambda *a:pytest.fail('offline test read env'))
    return asyncio.run(e.prepare())


def grade(folder):
    packet=json.loads((folder/'review_packet.json').read_text(encoding='utf-8'))
    e.save(folder/'test-review.json',{'packet_hash':packet['packet_hash'],'reviewer':{'type':'agent','id':'synthetic-test'},'items':[
        dict(review_id=i['review_id'],meaning_ok=True,language_ok=True,unsupported_fact=False,note='Synthetic grade for integrity test only') for i in packet['items']]})
    return selected.assess(folder,folder/'test-review.json')


def test_scope_pairs_directions_no_baseline_and_request_budget(scope):
    data,config=e.load()
    assert len(data['cases'])==20 and len(scope['jobs'])==42 and scope['max_http_attempts']==46
    assert scope['baseline']==[] and config['retry_policy']['total']==4
    cases={r['id']:r for r in data['cases']}
    preflight=[cases[j['case_id']] for j in scope['jobs'] if j['stage']=='preflight']
    assert len(preflight)==2 and {r['language'] for r in preflight}=={'en','id'}
    assert all(r['split']=='calibration' for r in preflight)
    assert Decimal(scope['reservation_usd'])<=Decimal('0.45')
    assert Decimal(scope['retry_reservation_usd'])==4*max(Decimal(j['reservation_usd']) for j in scope['jobs'])
    for row in data['cases']:
        body=scope['canonical_payloads'][row['id']]
        assert body['tools']==[] and body['text']['format']['name']=='translation_preview'
        assert body['input']==[{'role':'user','content':row['report']}]
        assert row['target_language']!=row['language']
        assert 'meaning_invariants' not in body and 'rejection_examples' not in body
    for name in ('agent_selection.json','agent_selection_v2.json','agent_acceptance_v3.json'):
        old=json.loads((e.ROOT/'evaluations/datasets'/name).read_text(encoding='utf-8'))
        assert not {r['report'] for r in old['cases']} & {r['report'] for r in data['cases']}


@pytest.mark.parametrize('retry',[False,True])
def test_full_mock_run_reviews_only_translation_and_preserves_first_denominator(scope,retry):
    transport,_=network(e.load()[0]);calls=[];clock=Clock()
    async def handle(request):
        calls.append(request)
        if retry and len(calls)==7:raise httpx.ReadTimeout('private details',request=request)
        return await transport.handle_async_request(request)
    result=asyncio.run(selected.live(scope['manifest_hash'],'0.45',key='synthetic-key',network=httpx.MockTransport(handle),clock=clock,sleep=clock.sleep,jitter=lambda:0))
    assert result['http_requests_sent']==42+int(retry) and result['retry_attempts']==int(retry)
    folder=e.OUT/result['run_folder']
    packet=json.loads((folder/'review_packet.json').read_text(encoding='utf-8'))
    assert len(packet['items'])==40 and all(i['case']['task']=='translation' for i in packet['items'])
    mapping=json.loads((folder/'review_mapping.json').read_text(encoding='utf-8'))
    assert set(mapping['aliases'])=={'openai'}
    assessment=grade(folder)
    assert set(assessment['candidates'])=={'openai'}
    model=assessment['candidates']['openai']
    assert model['planned']==32 and model['holdout_complete']==32 and model['first_attempt_holdout_planned']==32
    assert model['first_attempt_holdout_complete']==32-int(retry)
    assert model['per_language']['en']['planned']==16 and model['per_language']['id']['planned']==16
    assert set(model['per_task'])=={'translation'} and 'negative_reason_agreement' not in model
    assert assessment['http_requests_sent']==42+int(retry) and not assessment['production_changed']


def test_failure_does_not_manufacture_other_component_scores(scope):
    calls=[];clock=Clock()
    def handle(request):calls.append(request);return httpx.Response(401,text='sensitive-body')
    result=asyncio.run(selected.live(scope['manifest_hash'],'0.45',key='synthetic-key',network=httpx.MockTransport(handle),clock=clock,sleep=clock.sleep))
    assert len(calls)==1
    assessment=grade(e.OUT/result['run_folder'])['candidates']['openai']
    assert assessment['planned']==32 and assessment['holdout_complete']==0
    assert set(assessment['per_task'])=={'translation'} and not assessment['eligible']


@pytest.mark.parametrize('kind',['hash','cap','saved'])
def test_scope_mismatch_stops_before_env_or_claim(scope,kind):
    if kind=='saved':e.save(e.OUT/'prepared_manifest.json',dict(scope,cap_usd='99'))
    with pytest.raises(e.SafeFailure):
        asyncio.run(selected.live('old-scope' if kind=='hash' else scope['manifest_hash'],'0.75' if kind=='cap' else '0.45'))
    assert not list(e.OUT.glob('claimed-*'))
