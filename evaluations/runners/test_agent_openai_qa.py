"""Independent OpenAI-only scope checks; Lead executes, never real HTTP."""
import asyncio
import json

import httpx
import pytest

import agent_openai_validation as wrapper


@pytest.fixture
def configured(tmp_path, monkeypatch):
    e = wrapper.evaluator
    # configure mutates shared module bindings: restore every binding afterwards.
    for name in ('DATA', 'CONFIG', 'OUT', 'source_hashes', 'baseline'):
        monkeypatch.setattr(e, name, getattr(e, name))
    wrapper.configure()
    monkeypatch.setattr(e, 'OUT', tmp_path)
    return e


def test_openai_manifest_has_no_gemini_jobs_and_rejects_extra_provider_key(configured):
    e = configured

    async def scenario():
        manifest = await e.prepare()
        assert len(manifest['jobs']) == 72
        assert {j['provider'] for j in manifest['jobs']} == {'openai'}
        assert sum(j['stage'] == 'preflight' for j in manifest['jobs']) == 4
        with pytest.raises(e.SafeFailure, match='evaluation_credentials_missing'):
            await e.live(manifest['manifest_hash'], manifest['cap_usd'], keys={
                'openai': 'synthetic-openai-key', 'gemini': 'unexpected-extra-key',
            })
        assert not list(e.OUT.glob('claimed-*.json'))

    asyncio.run(scenario())


def test_openai_key_alone_suffices_and_deadline_preserves_all_denominators(configured):
    e = configured

    def forbidden_network(request):
        raise AssertionError('Deadline must prevent every HTTP attempt.')

    async def scenario():
        manifest = await e.prepare()
        ticks = iter([0] + [1000] * 72)
        folder = await e.live(
            manifest['manifest_hash'], manifest['cap_usd'],
            keys={'openai': 'synthetic-openai-key'},
            network=httpx.MockTransport(forbidden_network), clock=lambda: next(ticks),
        )
        records = json.loads((folder / 'attempts.json').read_text(encoding='utf-8'))
        report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        assert len(records) == 72
        assert all(not r['sent'] and r['status'] == 'not_run_run_deadline' for r in records)
        assert set(report['summary']) == {'openai'}
        assert report['summary']['openai']['planned_evaluation_cases'] == 68
        assert report['summary']['openai']['holdout_planned'] == 52
        assert report['summary']['openai']['holdout_schema_available'] == 0
        assert report['reserved_usd'] == '0'

    asyncio.run(scenario())
