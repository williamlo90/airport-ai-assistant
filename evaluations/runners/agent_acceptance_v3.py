"""Bounded OpenAI component acceptance: logical outcomes and physical attempts differ."""
import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

import agent_selection as e
import agent_selection_v2 as v2
from agent_transport import Bridge

ROOT=e.ROOT
DATA=ROOT/'evaluations/datasets/agent_acceptance_v3.json'
CONFIG=ROOT/'evaluations/rubrics/agent_acceptance_v3.json'
OUT=ROOT/'local/ai-04-acceptance-v3'
_BASE_HASHES=e.source_hashes


def baseline(row):
    if row['task']=='plan' and re.search(r'have not provided|belum memberikan|missing reference|referensi belum',row['report'],re.I):
        return {'kind':'clarify','clarification':{'question':'What is the reference to check?' if row['language']=='en' else 'Apa referensi yang perlu diperiksa?'}}
    return v2.baseline(row)


def source_hashes():
    hashes=_BASE_HASHES()
    paths=[Path(__file__),Path(v2.__file__),*Path(__file__).parent.glob('test_agent_acceptance*.py'),
           *Path(__file__).parent.glob('test_agent_request*.py')]
    for p in paths:hashes[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def configure():
    e.DATA,e.CONFIG,e.OUT=DATA,CONFIG,OUT
    e.source_hashes,e.baseline=source_hashes,baseline
    return e


def transient(record):
    if (record.get('identity_error') or record.get('provider_error_code') in {'insufficient_quota','invalid_api_key'}
        or record.get('network_category') in {'invalid_provider_envelope','cancelled'}
        or record.get('safe_error_code') in {'AGENT_INVALID_RESPONSE','AGENT_TOOL_DENIED','EVAL_REQUEST_CHANGED'}
        or record.get('http_status') not in {None,200,429,502,503,504}):return False
    return (record.get('http_status') in {429,502,503,504} or
            record.get('network_category') in {'connect_timeout','read_timeout','write_timeout','pool_timeout','connect_error','remote_protocol_error'})


def request_cost(records):
    sent=[r for r in records if r.get('sent')]
    return {'nominal_standard_upper_estimate_usd':str(sum((Decimal(r.get('nominal_usage_cost_usd',r['reservation_usd'])) for r in sent),Decimal(0))),
            'unknown_usage_attempts':sum(r.get('reported_usage') is None for r in sent),'invoice_verified':False,'human_labor_priced':False}


async def live(approved_hash,approved_cap,*,key=None,network=None,clock=time.monotonic,sleep=asyncio.sleep,jitter=None,_configure=None):
    (_configure or configure)();manifest=await e.prepare(persist=False);data,config=e.load();policy=config['retry_policy']
    run_out=e.OUT
    if approved_hash!=manifest['manifest_hash'] or approved_cap!=manifest['cap_usd']:raise e.SafeFailure('scope_not_authorized')
    if json.loads((run_out/'prepared_manifest.json').read_text(encoding='utf-8'))!=manifest:raise e.SafeFailure('prepared_manifest_changed')
    if key is None:
        from dotenv import dotenv_values
        key=os.environ.get('EVAL_OPENAI_API_KEY') or dotenv_values(ROOT/'.env').get('EVAL_OPENAI_API_KEY')
    if not isinstance(key,str) or not key.strip():raise e.SafeFailure('credential_missing')
    with (run_out/('claimed-'+approved_hash+'.json')).open('x',encoding='utf-8') as f:
        json.dump({'manifest':approved_hash,'cap_usd':approved_cap,'claimed_at':datetime.now(timezone.utc).isoformat()},f)
    folder=run_out/('run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8])
    e.save(folder/'manifest.json',manifest)
    cases={r['id']:r for r in data['cases']};logical=[];attempts=[];reserved=Decimal(0)
    retries=0;streak=0;stop=None;version=None;started=clock();last=-100000
    jitter=jitter or (lambda:e.random.SystemRandom().uniform(0,policy['jitter_seconds']))
    for job in manifest['jobs']:
        logical_start=clock()
        selected={**job,'sent':False,'status':'not_run','output':None,'score':e.score(cases[job['case_id']],None),'attempt_ids':[]}
        for number in (1,2):
            if stop:break
            if number==2:
                if retries>=policy['total']:break
                retries+=1
                delay=attempts[-1].get('retry_after_seconds',policy['base_backoff_seconds']+jitter())
            else:delay=0
            wait=max(delay,config['provider_interval_seconds']-(clock()-last),0)
            if clock()-started+wait+20>=config['max_wall_seconds']:
                stop='run_deadline';break
            if len(attempts)>=config['max_attempts'] or reserved+Decimal(job['reservation_usd'])>Decimal(approved_cap):
                stop='attempt_or_budget_limit';break
            if wait:await sleep(wait)
            if clock()-started+20>=config['max_wall_seconds']:
                stop='run_deadline';break
            last=clock();reserved+=Decimal(job['reservation_usd'])
            record={**job,'http_attempt_id':f'{job["job_id"]}-try-{number}','try':number,'sent':False,'status':'reserved',
                    'reserved_total_usd':str(reserved),'wait_seconds':round(wait,3)}
            attempts.append(record);e.journal(folder,'reserved',record)
            output=None
            try:
                bridge=Bridge('openai',config['providers']['openai'],key,job['canonical_digest'],e.digest,record,network=network)
                output=await e.invoke(e.OpenAIPlanner(e.PLACEHOLDER,transport=bridge),cases[job['case_id']])
                if key in json.dumps(output,ensure_ascii=False):raise e.SafeFailure('credential_marker_in_output')
                record['status']='completed'
            except e.AgentError as error:
                record.update(status='rejected',safe_error_code=error.code)
            except e.SafeFailure:
                stop='credential_marker_in_output';record['status']='stopped';output=None
            except asyncio.CancelledError:
                record['status']='cancelled';raise
            finally:
                observed=record.get('observed_model');usage=record.get('reported_usage')
                if record.get('identity_error') or (version and observed and observed!=version):stop='model_identity_changed'
                version=version or observed
                if usage:
                    record['nominal_usage_cost_usd']=str(e.nominal(config,'openai',usage['input'],usage['output']))
                    if usage['input']>job['reserved_input_tokens'] or usage['output']>config['max_output_tokens']:stop='usage_exceeds_reservation'
                if stop:output=None;record['status']='stopped_'+stop
                record['output']=output;record['score']=e.score(cases[job['case_id']],output)
                if record.get('safe_error_code') in {'AGENT_INVALID_RESPONSE','AGENT_TOOL_DENIED'}:record['score']['critical'].append('unusable_contract_response')
                e.journal(folder,'result',record)
            selected={**record,'attempt_ids':[r['http_attempt_id'] for r in attempts if r['job_id']==job['job_id']]}
            print(json.dumps({'job':job['job_id'],'try':number,'status':record['status'],'http_attempts':len(attempts)}),flush=True)
            if stop:break
            if record['status']=='completed':
                streak=0
                if job['stage']=='preflight' and not record['score']['mechanical_success']:stop='preflight_mechanical_failure'
                break
            if not transient(record):stop='permanent_request_failure';break
            streak+=1
            if streak>=policy['circuit_failures']:stop='transient_circuit_open';break
            if record.get('retry_after_seconds',0)>policy['max_retry_after_seconds']:stop='retry_after_outside_window';break
        if not selected['attempt_ids']:selected['status']='not_run_'+(stop or 'unavailable')
        selected['logical_elapsed_ms']=round((clock()-logical_start)*1000,3)
        logical.append(selected)
    e.save(folder/'attempts.json',logical);e.save(folder/'http_attempts.json',attempts)
    first=[next((r for r in attempts if r['job_id']==j['job_id']),j) for j in logical]
    report={'manifest_hash':approved_hash,'summary':e.summary(logical,data),'first_attempt_summary':e.summary(first,data),
            'reserved_usd':str(reserved),'http_requests_sent':sum(r['sent'] for r in attempts),'retry_attempts':sum(r['try']==2 for r in attempts),
            'stop_reason':stop,'observed_models':{'openai':version},'production_changed':False,'invoice_cost_verified':False,'review_pending':True,
            'scope':'Paired-family component benchmark with bounded retries, not business execution or production availability.'}
    report['physical_attempt_latency_ms']=e.latency([r['http_ms'] for r in attempts if 'http_ms' in r])
    report['logical_with_wait_latency_ms']=e.latency([r['logical_elapsed_ms'] for r in logical if r['sent']])
    base=[dict(job_id=f'baseline-{repeat}-{row["id"]}',provider='baseline',stage='evaluation',repeat=repeat,case_id=row['id'],output=e.baseline(row),score=e.score(row,e.baseline(row))) for repeat in (1,2) for row in (data['cases'] if config.get('include_baseline',True) else [])]
    packet,mapping=e.reviewed_packet(logical+base,data,approved_hash)
    e.save(folder/'review_packet.json',packet);e.save(folder/'review_mapping.json',mapping)
    report.update(attempts_hash=e.digest(logical),http_attempts_hash=e.digest(attempts),mapping_hash=e.digest(mapping),packet_hash=packet['packet_hash'])
    e.save(folder/'report.json',report)
    return {'run_folder':str(folder),'http_requests_sent':report['http_requests_sent'],'retry_attempts':report['retry_attempts'],'reserved_usd':str(reserved),'stop_reason':stop}


def assess(folder,review_file,*,_configure=None):
    (_configure or configure)();folder=folder.resolve();config=e.load()[1]
    if not folder.is_relative_to(e.OUT.resolve()):raise e.SafeFailure('run_path_not_owned')
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    attempts=json.loads((folder/'http_attempts.json').read_text(encoding='utf-8'))
    logical=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if e.digest(attempts)!=report['http_attempts_hash']:raise e.SafeFailure('http_attempts_changed')
    if len(attempts)>config['max_attempts'] or sum(r['try']==2 for r in attempts)>config['retry_policy']['total'] or len({r['http_attempt_id'] for r in attempts})!=len(attempts):raise e.SafeFailure('http_attempt_bounds')
    for job in manifest['jobs']:
        rows=[r for r in attempts if r['job_id']==job['job_id']]
        chosen=next(r for r in logical if r['job_id']==job['job_id'])
        if [r['try'] for r in rows]!=list(range(1,len(rows)+1)) or len(rows)>2:raise e.SafeFailure('http_attempt_order')
        if chosen['attempt_ids']!=[r['http_attempt_id'] for r in rows] or (rows and chosen['output']!=rows[-1]['output']):raise e.SafeFailure('selected_output_changed')
        for r in rows:
            if any(r[k]!=v for k,v in job.items()):raise e.SafeFailure('attempt_payload_changed')
        if len(rows)==2 and not transient(rows[0]):raise e.SafeFailure('nontransient_retry')
    if any(r['job_id'] not in {j['job_id'] for j in manifest['jobs']} for r in attempts):raise e.SafeFailure('unknown_http_attempt')
    result=e.assess(folder,review_file)
    candidates=result['candidates'];openai=candidates['openai']
    openai['cost']=request_cost(attempts)
    data,_=e.load();cases={r['id']:r for r in data['cases']}
    mapping=json.loads((folder/'review_mapping.json').read_text(encoding='utf-8'))
    review=json.loads(review_file.read_text(encoding='utf-8'))
    ratings={mapping['links'][r['review_id']]:r for r in review['items']}
    first_complete=0
    for chosen in logical:
        if chosen['stage']!='evaluation' or cases[chosen['case_id']]['split']!='holdout':continue
        first=next((r for r in attempts if r['job_id']==chosen['job_id']),None)
        grade=ratings.get(chosen['job_id'])
        if first and first['status']=='completed' and first['score']['mechanical_success'] and (not first['score']['review_required'] or (grade and grade['meaning_ok'] and grade['language_ok'] and not grade['unsupported_fact'])):first_complete+=1
    openai['first_attempt_holdout_complete']=first_complete
    openai['first_attempt_holdout_planned']=sum(r['split']=='holdout' for r in data['cases'])*config['repetitions']
    openai['gates']['run_not_stopped']=report['stop_reason'] is None
    openai['eligible']=all(openai['gates'].values())
    result.update(http_requests_sent=sum(r['sent'] for r in attempts),retry_attempts=sum(r['try']==2 for r in attempts),stop_reason=report['stop_reason'])
    e.save(folder/'assessment.json',result)
    report.update(review_pending=False,assessment_hash=e.digest(result));e.save(folder/'report.json',report)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','live','assess'])
    parser.add_argument('--approved-manifest');parser.add_argument('--approved-cap-usd');parser.add_argument('--run-folder',type=Path);parser.add_argument('--review-file',type=Path)
    args=parser.parse_args();configure()
    try:
        if args.command=='prepare':
            m=asyncio.run(e.prepare());print(json.dumps({k:m[k] for k in ('manifest_hash','max_http_attempts','reservation_usd','cap_usd')}))
        elif args.command=='live':print(json.dumps(asyncio.run(live(args.approved_manifest,args.approved_cap_usd))))
        else:print(json.dumps(assess(args.run_folder,args.review_file)))
    except (e.SafeFailure,ValueError,OSError,KeyError,TypeError,StopIteration):
        print(json.dumps({'failed':'acceptance_stopped_check_sanitized_artifacts'}));raise SystemExit(1) from None
