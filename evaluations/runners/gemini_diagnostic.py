"""Separately authorized, fail-fast Gemini calibration probes. Prepare is offline."""
import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
from uuid import uuid4

import httpx
import agent_selection_v2 as v2
from agent_transport import Bridge, wire

OUT = v2.OUT/'gemini-diagnostic'
CAP = '0.02'


def classify(record, error):
    record['network_category'] = next((label for kind, label in (
        (httpx.ConnectTimeout, 'connect_timeout'), (httpx.ReadTimeout, 'read_timeout'),
        (httpx.WriteTimeout, 'write_timeout'), (httpx.PoolTimeout, 'pool_timeout'),
        (httpx.ConnectError, 'connect_error'), (httpx.RemoteProtocolError, 'remote_protocol_error'),
    ) if isinstance(error, kind)), 'http_transport_error')


class ClassifiedStream(httpx.AsyncByteStream):
    def __init__(self, record, inner):
        self.record, self.inner = record, inner

    async def __aiter__(self):
        try:
            async for chunk in self.inner:
                yield chunk
        except httpx.HTTPError as error:
            classify(self.record, error)
            raise

    async def aclose(self):
        await self.inner.aclose()


class ClassifiedNetwork(httpx.AsyncBaseTransport):
    def __init__(self, record, inner=None):
        self.record, self.inner = record, inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        try:
            response = await self.inner.handle_async_request(request)
            response.stream = ClassifiedStream(self.record, response.stream)
            return response
        except httpx.HTTPError as error:
            # Fixed exception categories only, never exception text or host/headers.
            classify(self.record, error)
            raise

    async def aclose(self):
        await self.inner.aclose()


async def prepare(persist=True):
    e = v2.configure()
    data, config = e.load()
    provider = config['providers']['gemini']
    jobs = []
    for task in ('plan', 'answer', 'translation'):
        row = next(r for r in data['cases'] if r['split']=='calibration' and r['language']=='en' and r['task']==task)
        payload = await e.canonical(row)
        _, body = wire('gemini', payload, provider)
        size = len(json.dumps(body, ensure_ascii=False).encode())
        if size > config['max_wire_bytes']:
            raise e.SafeFailure('wire_input_too_large')
        reserved = size + config['input_overhead_reserve']
        jobs.append(dict(job_id=f'probe-{len(jobs)+1}', case_id=row['id'], payload=payload,
                         canonical_digest=e.digest(payload), reserved_input_tokens=reserved,
                         reservation_usd=str(e.nominal(config, 'gemini', reserved, config['max_output_tokens']))))
    total = sum((Decimal(j['reservation_usd']) for j in jobs), Decimal(0))
    if total > Decimal(CAP):
        raise e.SafeFailure('probe_budget_insufficient')
    base = dict(version=1, scope='Gemini calibration connectivity/contract only; no holdout or quality selection',
                hashes=e.source_hashes(), jobs=jobs, max_attempts=3, max_retries=0, fail_fast=True,
                provider=provider, interval_seconds=6.5, admission_seconds=120, max_output_tokens=1024,
                reservation_usd=str(total), cap_usd=CAP)
    result = {**base, 'manifest_hash':e.digest(base)}
    if persist:
        e.save(OUT/'prepared_manifest.json', result)
    return result


async def live(approved_hash, approved_cap, *, key=None, network=None, sleep=asyncio.sleep):
    e = v2.configure()
    manifest = await prepare(False)
    if approved_hash != manifest['manifest_hash'] or approved_cap != CAP:
        raise e.SafeFailure('probe_scope_not_authorized')
    if json.loads((OUT/'prepared_manifest.json').read_text(encoding='utf-8')) != manifest:
        raise e.SafeFailure('probe_manifest_changed')
    if key is None:
        from dotenv import dotenv_values
        key = os.environ.get('EVAL_GEMINI_API_KEY') or dotenv_values(e.ROOT/'.env').get('EVAL_GEMINI_API_KEY')
    if not isinstance(key, str) or not key.strip():
        raise e.SafeFailure('probe_key_missing')
    with (OUT/('claimed-'+approved_hash+'.json')).open('x', encoding='utf-8') as f:
        json.dump(dict(manifest_hash=approved_hash, cap_usd=CAP), f)
    folder = OUT/('run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8])
    e.save(folder/'manifest.json', manifest)
    data, config = e.load()
    rows = {r['id']:r for r in data['cases']}
    records, blocked, version = [], False, None
    start = time.monotonic()
    for job in manifest['jobs']:
        record = {k:v for k,v in job.items() if k!='payload'}
        record.update(provider='gemini', sent=False, status='not_run')
        if not blocked and time.monotonic()-start < manifest['admission_seconds']-27:
            if records:
                await sleep(manifest['interval_seconds'])
            record['status'] = 'reserved'
            e.journal(folder, 'reserved', record)
            bridge = Bridge('gemini', manifest['provider'], key, job['canonical_digest'], e.digest,
                            record, network=ClassifiedNetwork(record, network))
            try:
                output = await e.invoke(e.OpenAIPlanner(e.PLACEHOLDER, transport=bridge), rows[job['case_id']])
                if key in json.dumps(output, ensure_ascii=False):
                    raise e.SafeFailure('credential_marker_in_output')
                record['status'] = 'completed'
            except e.AgentError as error:
                record.update(status='rejected', safe_error_code=error.code)
                blocked = True
            finally:
                observed = record.get('observed_model')
                usage = record.get('reported_usage')
                if record.get('identity_error') or (version and observed and version != observed):
                    record['status'], blocked = 'model_identity_changed', True
                version = version or observed
                if usage:
                    record['nominal_usage_cost_usd'] = str(e.nominal(config, 'gemini', usage['input'], usage['output']))
                    if usage['input']>job['reserved_input_tokens'] or usage['output']>1024:
                        record['status'], blocked = 'usage_exceeds_reservation', True
                e.journal(folder, 'result', record)
        else:
            blocked = True
            e.journal(folder, 'not_run', record)
        records.append(record)
    e.save(folder/'attempts.json', records)
    report = dict(manifest_hash=approved_hash, requests_sent=sum(r['sent'] for r in records),
                  probe_pass=all(r['status']=='completed' for r in records), retries=0,
                  reserved_usd=str(sum((Decimal(r['reservation_usd']) for r in records if r['sent']), Decimal(0))),
                  invoice_verified=False, production_changed=False, quality_measured=False)
    e.save(folder/'report.json', report)
    return dict(run_folder=str(folder), **report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'live'])
    parser.add_argument('--approved-manifest')
    parser.add_argument('--approved-cap-usd')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = asyncio.run(prepare())
            print(json.dumps({k:result[k] for k in ('manifest_hash', 'max_attempts', 'reservation_usd', 'cap_usd')}))
        else:
            print(json.dumps(asyncio.run(live(args.approved_manifest, args.approved_cap_usd))))
    except (v2.evaluator.SafeFailure, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'failed':'probe_stopped_check_local_sanitized_artifacts'}))
        raise SystemExit(1) from None
