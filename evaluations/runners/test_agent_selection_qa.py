"""Independent runner regression; Lead executes with synthetic transports only."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import agent_selection as selection
from airport_assistant.modules.agent.models import AgentError


def test_model_drift_in_parser_rejected_response_stops_before_next_paid_attempt(tmp_path, monkeypatch):
    row = dict(id='qa-case', task='answer', language='en', split='holdout',
               expected={'outcome': 'insufficient', 'source_ids': []}, sources=[])
    jobs = [dict(job_id=f'job-{i}', provider='gemini', stage='evaluation', repeat=i,
                 case_id=row['id'], canonical_digest='synthetic',
                 reserved_input_tokens=100, reservation_usd='0.01') for i in range(1, 4)]
    manifest = dict(manifest_hash='qa-manifest', cap_usd='1.00', jobs=jobs)
    config = dict(provider_interval_seconds=0, max_output_tokens=1024,
                  providers={'gemini': {'model': 'gemini-3.1-flash-lite',
                                       'input_usd_per_million': '0.25',
                                       'output_usd_per_million': '1.50'}})
    calls = []

    async def prepare(*, persist):
        return manifest

    async def invoke(planner, case):
        record = planner.transport.record
        calls.append(case['id'])
        record.update(sent=True, http_status=200,
                      observed_model='gemini-3.1-flash-lite-version-' + ('a' if len(calls) == 1 else 'b'),
                      reported_usage={'input': 20, 'output': 10})
        if len(calls) == 2:
            raise AgentError(502, 'AGENT_INVALID_RESPONSE')
        return {'outcome': 'insufficient', 'source_ids': []}

    monkeypatch.setattr(selection, 'OUT', tmp_path)
    monkeypatch.setattr(selection, 'prepare', prepare)
    monkeypatch.setattr(selection, 'load', lambda: ({'cases': [row]}, config))
    monkeypatch.setattr(selection, 'invoke', invoke)
    monkeypatch.setattr(selection, 'OpenAIPlanner', lambda key, *, transport: SimpleNamespace(transport=transport))
    (tmp_path / 'prepared_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    with pytest.raises(selection.SafeFailure, match='provider_version_drift'):
        asyncio.run(selection.live('qa-manifest', '1.00', keys={'gemini': 'synthetic-gemini-key'}))
    assert len(calls) == 2
    stop_files = list(tmp_path.glob('run-*/stop.json'))
    assert len(stop_files) == 1
    assert json.loads(stop_files[0].read_text(encoding='utf-8'))['reason'] == 'provider_version_drift'
