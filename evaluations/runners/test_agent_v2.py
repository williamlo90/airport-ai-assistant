"""Preparation and diagnostic safety; every provider response is mocked."""
import asyncio
import json
from decimal import Decimal
import httpx
import pytest
import agent_selection as e
import agent_selection_v2 as v2
import gemini_diagnostic as probe
from test_agent_selection import network
from airport_assistant.modules.knowledge.retrieval import chunk_text


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    # Restore every shared evaluator binding after each wrapper test.
    for attr in ('DATA', 'CONFIG', 'OUT', 'source_hashes', 'baseline'):
        monkeypatch.setattr(e, attr, getattr(e, attr))
    monkeypatch.setattr(v2, 'OUT', tmp_path/'v2')
    monkeypatch.setattr(probe, 'OUT', tmp_path/'probe')
    import dotenv
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *a: pytest.fail('offline test accessed env'))
    return v2.configure()


def test_new_family_split_and_chunk_gold_are_not_renamed_old_rows(isolated):
    data, _ = e.load()
    old = json.loads((e.ROOT/'evaluations/datasets/agent_selection.json').read_text(encoding='utf-8'))
    assert not {r['family'] for r in data['cases']} & {r['family'] for r in old['cases']}
    assert not {r['report'] for r in data['cases']} & {r['report'] for r in old['cases']}
    assert len({r['family'] for r in data['cases'] if r['split']=='holdout'}) == 13
    for row in data['cases']:
        if row['family']=='v2-answer-child-age-boundary':
            chunks = chunk_text(row['chunk_fixture_body'])
            assert row['sources'][2]['text'] == chunks[1].body
            assert chunks[0].body in [s['text'] for s in row['sources']]
        if row['family']=='v2-answer-separate-facilities':
            for id in row['expected']['source_ids']:
                assert e.score(row, {'outcome':'answer', 'source_ids':[id]})['critical']


def test_v2_full_schedule_isolated_and_label_free_with_fake_providers(isolated):
    manifest = asyncio.run(e.prepare())
    data, _ = e.load()
    transport, calls = network(data)
    folder = asyncio.run(e.live(manifest['manifest_hash'], '1.00', network=transport,
                               keys={'openai':'fake-openai-key', 'gemini':'fake-gemini-key'},
                               sleep=lambda _: asyncio.sleep(0)))
    assert len(calls)==144 and folder.is_relative_to(v2.OUT)
    assert 'evaluations/runners/gemini_diagnostic.py' in manifest['hashes']
    assert 'evaluations/runners/agent_selection_v2.py' in manifest['hashes']
    for job in manifest['jobs']:
        payload = manifest['canonical_payloads'][job['case_id']]
        assert set(payload) >= {'instructions', 'input'}
        assert 'expected' not in payload and 'holdout' not in json.dumps(payload)
    report = json.loads((folder/'report.json').read_text(encoding='utf-8'))
    assert report['review_pending'] is True


def test_probe_manifest_only_uses_calibration_and_independent_budget(isolated):
    manifest = asyncio.run(probe.prepare())
    data, _ = e.load()
    cases = {r['id']:r for r in data['cases']}
    assert len(manifest['jobs'])==3
    assert {cases[j['case_id']]['task'] for j in manifest['jobs']}=={'plan','answer','translation'}
    assert all(cases[j['case_id']]['split']=='calibration' for j in manifest['jobs'])
    assert Decimal(manifest['reservation_usd']) <= Decimal('0.02')
    assert manifest['max_retries']==0
    assert not (v2.OUT/'prepared_manifest.json').exists()


def test_v2_baseline_handles_generic_declared_refs_and_current_ambiguity(isolated):
    for ref in ('Ab_9-x', 'ALLLETTERS'):
        row = dict(task='plan', language='en', report='Please check transaction '+ref+'.', tools=['read_transaction'])
        assert v2.baseline(row)['tool']['arguments']['reference']==ref
    row = dict(task='plan', language='id', report='Reservasi Aa_4-b dan Cc_5-d perlu diperiksa salah satunya.', tools=['read_booking'])
    assert v2.baseline(row)['kind']=='clarify'


@pytest.mark.parametrize('cap', [None, '0', '1.00', 'NaN'])
def test_probe_unauthorized_scope_never_loads_env_or_sends(isolated, cap):
    manifest = asyncio.run(probe.prepare())
    calls = []
    def forbidden(request):
        calls.append(request)
        pytest.fail('unexpected network')
    with pytest.raises(e.SafeFailure):
        asyncio.run(probe.live(manifest['manifest_hash'], cap, network=httpx.MockTransport(forbidden)))
    assert calls==[] and not list(probe.OUT.glob('claimed-*'))


@pytest.mark.parametrize('failure', ['connect_timeout', 'read_timeout', '429'])
def test_probe_transport_failure_is_sanitized_fail_fast_no_retry(isolated, failure):
    manifest = asyncio.run(probe.prepare())
    calls = []
    def handle(request):
        calls.append(request)
        if failure=='429':
            return httpx.Response(429, text='secret fake-gemini-key must not be stored')
        kind = httpx.ConnectTimeout if failure=='connect_timeout' else httpx.ReadTimeout
        raise kind('private transport detail fake-gemini-key', request=request)
    result = asyncio.run(probe.live(manifest['manifest_hash'], '0.02', key='fake-gemini-key',
                                    network=httpx.MockTransport(handle)))
    assert result['requests_sent']==1 and not result['probe_pass'] and len(calls)==1
    records = json.loads((probe.OUT/result['run_folder']/'attempts.json').read_text(encoding='utf-8'))
    assert [r['status'] for r in records]==['rejected','not_run','not_run']
    if failure!='429':
        assert records[0]['network_category']==failure
    for path in probe.OUT.rglob('*.json*'):
        assert 'fake-gemini-key' not in path.read_text(encoding='utf-8')
    with pytest.raises(FileExistsError):
        asyncio.run(probe.live(manifest['manifest_hash'], '0.02', key='fake-gemini-key', network=httpx.MockTransport(handle)))
    assert len(calls)==1


def test_probe_success_has_no_quality_claim_or_automatic_full_run(isolated):
    manifest = asyncio.run(probe.prepare())
    transport, calls = network(e.load()[0])
    result = asyncio.run(probe.live(manifest['manifest_hash'], '0.02', key='fake-gemini-key',
                                    network=transport, sleep=lambda _: asyncio.sleep(0)))
    assert result['requests_sent']==3 and len(calls)==3 and result['probe_pass']
    assert not result['quality_measured'] and not result['production_changed']
    assert not (v2.OUT/'prepared_manifest.json').exists()


@pytest.mark.parametrize('problem', ['excess_usage', 'identity', 'version_drift'])
def test_probe_stops_on_usage_or_identity_even_when_content_rejected(isolated, problem):
    manifest = asyncio.run(probe.prepare())
    original, calls = network(e.load()[0])
    count = []
    async def handle(request):
        response = await original.handle_async_request(request)
        body = json.loads(response.content)
        count.append(1)
        if problem=='excess_usage':
            body['usage']['completion_tokens'] = 1025
        elif problem=='identity':
            body['model'] = 'not-the-approved-model'
        elif len(count)==2:
            body['model'] = 'gemini-3.1-flash-lite-002'
        if problem!='version_drift' or len(count)==2:
            body['choices'][0]['finish_reason'] = 'length'
        return httpx.Response(200, json=body)
    result = asyncio.run(probe.live(manifest['manifest_hash'], '0.02', key='fake-gemini-key',
                                    network=httpx.MockTransport(handle), sleep=lambda _: asyncio.sleep(0)))
    assert not result['probe_pass']
    assert result['requests_sent']==(2 if problem=='version_drift' else 1)
    records = json.loads((probe.OUT/result['run_folder']/'attempts.json').read_text(encoding='utf-8'))
    assert records[result['requests_sent']-1]['status'] == ('usage_exceeds_reservation' if problem=='excess_usage' else 'model_identity_changed')
