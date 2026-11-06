"""OpenAI-only preparation/ledger tests. Mock outputs are not quality evidence."""
import asyncio
import copy
import json
from decimal import Decimal
import httpx
import pytest
import agent_selection as e
import agent_openai_validation as selected
from test_agent_selection import network


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    for attr in ('DATA','CONFIG','OUT','source_hashes','baseline'):
        monkeypatch.setattr(e, attr, getattr(e, attr))
    monkeypatch.setattr(selected, 'OUT', tmp_path)
    selected.configure()
    import dotenv
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *a:pytest.fail('offline test accessed env'))
    return asyncio.run(e.prepare())


def test_prepare_has_only_openai_jobs_and_exact_scope(prepared):
    assert len(prepared['jobs'])==72
    assert {r['provider'] for r in prepared['jobs']}=={'openai'}
    assert sum(r['stage']=='preflight' for r in prepared['jobs'])==4
    assert sum(r['stage']=='evaluation' for r in prepared['jobs'])==68
    assert prepared['cap_usd']=='0.60'
    assert Decimal(prepared['reservation_usd']) <= Decimal('0.60')
    assert 'evaluations/runners/agent_openai_validation.py' in prepared['hashes']
    assert asyncio.run(e.prepare(persist=False))==prepared
    _, config = e.load()
    assert set(config['providers'])=={'openai'}
    assert config['providers']['openai']['key_env']=='EVAL_OPENAI_API_KEY'


def test_full_mock_run_and_review_never_require_gemini_or_invent_its_denominator(prepared):
    transport, calls = network(e.load()[0])
    folder = asyncio.run(e.live(prepared['manifest_hash'], '0.60', network=transport,
                               keys={'openai':'synthetic-openai-key'}, sleep=lambda _:asyncio.sleep(0)))
    assert len(calls)==72 and all(r.url.host=='api.openai.com' for r in calls)
    report = json.loads((folder/'report.json').read_text(encoding='utf-8'))
    assert set(report['summary'])=={'openai'}
    assert report['summary']['openai']['holdout_planned']==52
    assert report['summary']['openai']['recorded_evaluation_cases']==68
    mapping = json.loads((folder/'review_mapping.json').read_text(encoding='utf-8'))
    assert set(mapping['aliases'])=={'openai','baseline'}
    packet = json.loads((folder/'review_packet.json').read_text(encoding='utf-8'))
    # Synthetic grades exercise hash/coverage/gates, never semantic validity.
    review = dict(packet_hash=packet['packet_hash'],reviewer={'type':'agent','id':'synthetic-test'},items=[
        dict(review_id=i['review_id'],meaning_ok=True,language_ok=True,unsupported_fact=False,note='Synthetic test only')
        for i in packet['items']])
    e.save(folder/'test-review.json',review)
    assessment=e.assess(folder,folder/'test-review.json')
    assert set(assessment['candidates'])=={'openai','baseline'}
    assert assessment['candidates']['openai']['planned']==52
    assert assessment['candidates']['openai']['per_language']['en']['planned']==26
    assert not assessment['production_changed'] and not assessment['automatic_winner']
    with pytest.raises(e.SafeFailure,match='already_used'):
        asyncio.run(e.live(prepared['manifest_hash'],'0.60',network=transport,keys={'openai':'synthetic-openai-key'}))
    assert len(calls)==72


@pytest.mark.parametrize('problem',['hash','cap','keyset','manifest'])
def test_old_scope_or_extra_credentials_cannot_authorize_new_run(prepared,problem):
    transport,calls=network(e.load()[0])
    manifest_hash,cap,keys=prepared['manifest_hash'],'0.60',{'openai':'synthetic-openai-key'}
    if problem=='hash':manifest_hash='retired-two-provider-manifest'
    if problem=='cap':cap='1.00'
    if problem=='keyset':keys['gemini']='unused-key'
    if problem=='manifest':
        changed=copy.deepcopy(prepared);changed['jobs'][0]['provider']='gemini'
        e.save(e.OUT/'prepared_manifest.json',changed)
    with pytest.raises(e.SafeFailure):
        asyncio.run(e.live(manifest_hash,cap,network=transport,keys=keys))
    assert calls==[] and not list(e.OUT.glob('claimed-*'))


def test_openai_failure_stops_without_fallback_or_success_only_denominator(prepared):
    transport,calls=network(e.load()[0],failure=429)
    folder=asyncio.run(e.live(prepared['manifest_hash'],'0.60',network=transport,keys={'openai':'synthetic-openai-key'}))
    assert len(calls)==1 and calls[0].url.host=='api.openai.com'
    records=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    assert len(records)==72 and sum(r['sent'] for r in records)==1
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    assert report['summary']['openai']['holdout_planned']==52
    assert report['summary']['openai']['holdout_schema_available']==0
