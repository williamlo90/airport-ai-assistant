"""AI-04 bounded component evaluation. Prepare is offline; live is one-shot and explicit."""
from __future__ import annotations
import argparse,asyncio,hashlib,json,math,os,re,statistics,time,random
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import httpx
from airport_assistant.integrations.agent.openai import OpenAIPlanner
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.agent.answers import render
from airport_assistant.modules.agent.translation import preserve_values
from agent_transport import Bridge,wire

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'evaluations/datasets/agent_selection.json'
CONFIG=ROOT/'evaluations/rubrics/agent_selection.json'
OUT=ROOT/'local/ai-04'
PLACEHOLDER='evaluation-local-placeholder-not-a-credential'

class SafeFailure(Exception):pass
class Captured(Exception):pass

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temporary.replace(path)

def journal(folder,event,record):
    with (folder/'journal.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps({'event':event,'record':record},ensure_ascii=False,allow_nan=False)+'\n')
        out.flush();os.fsync(out.fileno())

def load():
    if not __debug__:raise SafeFailure('optimized_python_not_supported')
    data=json.loads(DATA.read_text(encoding='utf-8'));config=json.loads(CONFIG.read_text(encoding='utf-8'))
    translation_only=config.get('dataset_profile')=='airport_translation_v1'
    if config.get('dataset_profile') not in (None,'airport_translation_v1'):raise SafeFailure('dataset_profile')
    size,family_count,calibration=(20,10,4) if translation_only else (34,17,8)
    rows=data['cases'];families={};counts=Counter()
    if len(rows)!=size or len({r['id'] for r in rows})!=size:raise SafeFailure('dataset_shape')
    for row in rows:
        if row['task'] not in {'plan','answer','draft','translation'} or row['language'] not in {'en','id'} or row['split'] not in {'calibration','holdout'}:raise SafeFailure('dataset_labels')
        if families.setdefault(row['family'],row['split'])!=row['split']:raise SafeFailure('family_leakage')
        if not isinstance(row['report'],str) or not 0<len(row['report'])<=4000:raise SafeFailure('report_bound')
        counts[row['family'],row['language']]+=1
        if translation_only and (row['task']!='translation' or row.get('target_language')!=('id' if row['language']=='en' else 'en')):raise SafeFailure('translation_direction')
        if row['task']=='answer':
            ids={s['source_id'] for s in row['sources']}
            if len(ids)!=len(row['sources']) or not set(row['expected']['source_ids'])<=ids:raise SafeFailure('source_labels')
            if any(not 0<len(s['text'])<=384 for s in row['sources']):raise SafeFailure('source_bound')
            if any(not set(group)<=ids for group in row.get('acceptable_source_sets',[])):raise SafeFailure('source_labels')
    if len(families)!=family_count or any(counts[f,l]!=1 for f in families for l in ('en','id')):raise SafeFailure('family_pairs')
    if sum(r['split']=='calibration' for r in rows)!=calibration:raise SafeFailure('split_size')
    if translation_only and (set(config['providers'])!={'openai'} or config.get('include_baseline') is not False):raise SafeFailure('translation_scope')
    if set(config['providers']) not in ({'openai'}, {'openai','gemini'}) or config['max_output_tokens']!=1024:raise SafeFailure('provider_bounds')
    policy=config.get('retry_policy')
    extra=(4 if translation_only else 8) if policy else 0
    if policy and (set(config['providers'])!={'openai'} or policy!={'per_job':1,'total':extra,'circuit_failures':3,'base_backoff_seconds':3,'jitter_seconds':1,'max_retry_after_seconds':30}):raise SafeFailure('retry_policy_bounds')
    logical_count=size*2+(2 if translation_only else 4)
    if config['repetitions']!=2 or config['max_retries']!=(1 if policy else 0) or config['max_attempts']!=logical_count*len(config['providers'])+extra:raise SafeFailure('schedule_bounds')
    for p in config['providers'].values():
        if any(not Decimal(p[k]).is_finite() or Decimal(p[k])<=0 for k in ('input_usd_per_million','output_usd_per_million')):raise SafeFailure('prices')
    return data,config

async def invoke(planner,row):
    if row['task']=='plan':
        value=await planner.advance(row['report'],row['language'],tuple(row['tools']),memory=tuple(row['memory']),observations=())
        return value.model_dump(mode='json')
    if row['task']=='draft':return (await planner.decide(row['report'],row['language'],(),draft=True)).model_dump(mode='json')
    if row['task']=='translation':return {'text':await planner.translate(row['report'],row['language'],row['target_language'])}
    return await planner.answer(row['report'],row['language'],tuple(row['sources']),memory=tuple(row['memory']))

async def canonical(row):
    result=[]
    def capture(request):result.append(json.loads(request.content));raise Captured()
    try:await invoke(OpenAIPlanner(PLACEHOLDER,transport=httpx.MockTransport(capture)),row)
    except Captured:pass
    if len(result)!=1:raise SafeFailure('canonical_capture_failed')
    return result[0]

def schedule(data,providers=('openai','gemini')):
    rows=data['cases'];jobs=[]
    tasks=tuple(t for t in ('plan','answer','draft','translation') if any(r['task']==t for r in rows))
    if tasks==('translation',):
        family=next(r['family'] for r in rows if r['split']=='calibration')
        preflight=[next(r for r in rows if r['family']==family and r['language']==language) for language in ('en','id')]
    else:preflight=[next(r for r in rows if r['task']==task and r['language']=='en' and r['split']=='calibration') for task in tasks]
    for stage,repeat,items in [('preflight',0,preflight),('evaluation',1,rows),('evaluation',2,list(reversed(rows)))]:
        for index,row in enumerate(items):
            order=providers if (index+repeat)%2==0 else tuple(reversed(providers))
            for provider in order:jobs.append({'provider':provider,'stage':stage,'repeat':repeat,'case_id':row['id']})
    return jobs

def nominal(config,provider,input_tokens,output_tokens):
    p=config['providers'][provider]
    return (Decimal(p['input_usd_per_million'])*input_tokens+Decimal(p['output_usd_per_million'])*output_tokens)/Decimal(1000000)

def tokens(value):return set(re.findall(r'[^\W_]+',value.lower()))
def baseline(row):
    text=row['report'];lower=text.lower();task=row['task'];lang=row['language']
    if task=='translation':return {'requires_human':True}
    if task=='plan':
        refs=re.findall(r'\b(?:PK|LG|TX)-[A-Z0-9-]+\b',text)
        for ref in refs:
            name='read_transaction' if ref.startswith('TX-') else 'read_booking'
            if name in row['tools']:return {'kind':'tool','tool':{'name':name,'arguments':{'reference':ref}}}
        return {'kind':'clarify','clarification':{'question':'Which service needs help?' if lang=='en' else 'Layanan apa yang perlu dibantu?'}}
    if task=='draft':
        service='lounge' if 'lounge' in lower else ('parking' if any(w in lower for w in ('parking','parkir')) else 'general')
        category='transaction_issue' if any(w in lower for w in ('payment','pembayaran','charged','terdebit')) else ('booking_issue' if any(w in lower for w in ('booking','pesanan')) else 'information')
        return {'kind':'draft','proposal':{'category':category,'service_type':service,'summary':('Customer reports: ' if lang=='en' else 'Laporan pelanggan: ')+text[:170]}}
    stop={'the','is','a','an','in','at','to','can','i','my','di','yang','dan','saya','apakah','dapat','untuk','on','of','it','this','with','without'}
    question=tokens(text)-stop
    scores=[(len(question & (tokens(s['text'])-stop)),s['source_id']) for s in row['sources']]
    scores.sort(key=lambda s:(-s[0],s[1]))
    ids=[scores[0][1]] if scores and scores[0][0]>=2 else []
    return {'outcome':'answer' if ids else 'insufficient','source_ids':ids}

def score(row,output):
    task=row['task'];expected=row['expected'];critical=[];success=False;review=False
    if output is None:return {'mechanical_success':False,'critical':[],'review_required':False,'schema_available':False}
    if task=='plan':
        if output.get('kind')=='tool':
            tool=output.get('tool') or {};args=tool.get('arguments') or {}
            if tool.get('name') not in row['tools'] or args!={'reference':expected.get('reference')}:critical.append('tool_or_reference_outside_expected_scope')
            success=expected['kind']=='tool' and tool.get('name')==expected.get('name') and args=={'reference':expected.get('reference')}
        else:success=expected['kind']=='clarify' and output.get('kind')=='clarify';review=success
    elif task=='answer':
        ids=output.get('source_ids',[]);known={s['source_id'] for s in row['sources']}
        if not isinstance(ids,(list,tuple)) or any(not isinstance(i,str) for i in ids):critical.append('malformed_citations');ids=[]
        if set(ids)-known or len(set(ids))!=len(ids):critical.append('invalid_citations')
        outcomes=row.get('acceptable_outcomes',[expected['outcome']])
        sets=row.get('acceptable_source_sets',[expected['source_ids']])
        success=output.get('outcome') in outcomes and any(set(ids)==set(group) for group in sets)
        if output.get('outcome')=='answer' and not success:critical.append('unsupported_or_incomplete_answer')
        review=True
    elif task=='draft':
        p=output.get('proposal') or {}
        success=output.get('kind')=='draft' and all(p.get(k)==expected[k] for k in ('category','service_type')) and isinstance(p.get('summary'),str) and 0<len(p['summary'])<=200
        identifiers=set(re.findall(r'\b[A-Z]{2,}-[A-Z0-9-]+\b|[\w.+-]+@[\w.-]+',p.get('summary','')))
        if any(value not in row['report'] for value in identifiers):critical.append('invented_identifier_or_contact')
        review=True
    else:
        if output.get('requires_human'):return {'mechanical_success':False,'critical':[],'review_required':False,'schema_available':True,'human_deferral':True}
        try:preserve_values(row['report'],output['text']);success=True
        except (AgentError,KeyError,TypeError):critical.append('translation_values_changed')
        review=True
    return {'mechanical_success':bool(success and not critical),'critical':critical,'review_required':review,'schema_available':True}

def source_hashes():
    paths=[DATA,CONFIG,Path(__file__),Path(__file__).with_name('agent_transport.py'),*sorted(Path(__file__).parent.glob('test_agent_selection*.py')),*sorted((ROOT/'backend/src').rglob('*.py')),ROOT/'backend/pyproject.toml',ROOT/'backend/uv.lock']
    return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

async def prepare(*,persist=True):
    data,config=load();payloads={r['id']:await canonical(r) for r in data['cases']};jobs=schedule(data,tuple(config['providers']));total=Decimal(0)
    for index,job in enumerate(jobs):
        provider=job['provider'];_,body=wire(provider,payloads[job['case_id']],config['providers'][provider])
        size=len(json.dumps(body,ensure_ascii=False).encode())
        if size>config['max_wire_bytes']:raise SafeFailure('wire_input_too_large')
        reserve=size+config['input_overhead_reserve'];cost=nominal(config,provider,reserve,config['max_output_tokens']);total+=cost
        job.update(job_id=f'job-{index+1:03}',canonical_digest=digest(payloads[job['case_id']]),reserved_input_tokens=reserve,reservation_usd=str(cost))
    extra=config.get('retry_policy',{}).get('total',0)
    retry_reserve=extra*max(Decimal(j['reservation_usd']) for j in jobs)
    if len(jobs)+extra!=config['max_attempts'] or total+retry_reserve>Decimal(config['proposed_cap_usd']):raise SafeFailure('proposed_budget_insufficient')
    base={'version':1,'hashes':source_hashes(),'jobs':jobs,'reservation_usd':str(total),'cap_usd':config['proposed_cap_usd'],
        'canonical_payloads':payloads,'baseline':[{'case_id':r['id'],'output':baseline(r),'score':score(r,baseline(r))} for r in data['cases']] if config.get('include_baseline',True) else []}
    if extra:base.update(reservation_usd=str(total+retry_reserve),primary_reservation_usd=str(total),retry_reservation_usd=str(retry_reserve),max_http_attempts=config['max_attempts'],retry_policy=config['retry_policy'])
    manifest={**base,'manifest_hash':digest(base)}
    if persist:save(OUT/'prepared_manifest.json',manifest)
    return manifest

def summary(records,data):
    cases={r['id']:r for r in data['cases']};result={}
    repeats=load()[1]['repetitions'];holdout_count=sum(r['split']=='holdout' for r in data['cases'])*repeats
    tasks=tuple(t for t in ('plan','answer','draft','translation') if any(r['task']==t for r in data['cases']))
    for provider in load()[1]['providers']:
        rows=[r for r in records if r['provider']==provider and r['stage']=='evaluation'];holdout=[r for r in rows if cases[r['case_id']]['split']=='holdout']
        result[provider]={'planned_evaluation_cases':len(data['cases'])*repeats,'recorded_evaluation_cases':len(rows),
            'holdout_planned':holdout_count,'holdout_mechanical_success':sum(r['score']['mechanical_success'] for r in holdout),
            'holdout_critical_failures':sum(bool(r['score']['critical']) for r in holdout),
            'holdout_schema_available':sum(r['score']['schema_available'] for r in holdout),
            'negative_reason_agreement':reason_agreement(holdout,cases),
            'semantic_reviews_pending':sum(r['score']['review_required'] for r in rows),
            'per_language':{l:{'planned':sum(r['split']=='holdout' and r['language']==l for r in data['cases'])*repeats,'mechanical_success':sum(r['score']['mechanical_success'] for r in holdout if cases[r['case_id']]['language']==l)} for l in ('en','id')},
            'per_task':{t:{'count':sum(cases[r['case_id']]['task']==t for r in holdout),'mechanical_success':sum(r['score']['mechanical_success'] for r in holdout if cases[r['case_id']]['task']==t)} for t in tasks},
            'http_success_latency_ms':latency([r['http_ms'] for r in rows if r.get('http_status')==200 and 'http_ms' in r]),
            'all_attempt_latency_ms':latency([r['http_ms'] for r in rows if r.get('sent') and 'http_ms' in r]),
            'qualification':'pending_semantic_review_not_a_winner'}
        if 'answer' not in tasks:result[provider].pop('negative_reason_agreement')
    return result

def reason_agreement(records,cases):
    ids={id for id,row in cases.items() if row['task']=='answer' and row['split']=='holdout' and row['expected']['outcome']!='answer'}
    rows=[r for r in records if r['case_id'] in ids]
    return {'correct':sum((r.get('output') or {}).get('outcome')==cases[r['case_id']]['expected']['outcome'] for r in rows),
        'planned':len(ids)*2,'unavailable':sum(r.get('output') is None for r in rows)}

def latency(values):
    if not values:return {'n':0,'median':None,'p95_nearest_rank':None}
    values=sorted(values);return {'n':len(values),'median':statistics.median(values),'p95_nearest_rank':values[math.ceil(.95*len(values))-1]}

def reviewed_packet(records,data,manifest_hash):
    rows={r['id']:r for r in data['cases']};providers=sorted({r['provider'] for r in records if r['provider']!='baseline'});random.SystemRandom().shuffle(providers)
    aliases={p:'candidate-'+chr(65+i) for i,p in enumerate(providers)}
    if any(r['provider']=='baseline' for r in records):aliases['baseline']='rule-baseline'
    packet=[];links={}
    for record in records:
        if record['stage']!='evaluation' or not record['score']['review_required']:continue
        id=uuid4().hex;links[id]=record['job_id']
        row=rows[record['case_id']];output=record['output'];rendered=None
        if row['task']=='answer' and output and output.get('outcome')=='answer':
            by_id={s['source_id']:s for s in row['sources']}
            if all(id in by_id for id in output.get('source_ids',[])):
                rendered=render(row['language'],[{'quote':by_id[id]['text'],'source_uri':'urn:airport:fixture:'+id} for id in output['source_ids']])
        packet.append({'review_id':id,'candidate':aliases[record['provider']],'case':row,
            'repeat':record['repeat'],'output':output,'rendered_answer':rendered})
    random.SystemRandom().shuffle(packet)
    base={'manifest_hash':manifest_hash,'items':packet}
    return {**base,'packet_hash':digest(base)},{'aliases':aliases,'links':links}

def assess(folder,review_file):
    folder=folder.resolve();review_file=review_file.resolve()
    if not folder.is_relative_to(OUT.resolve()) or not folder.name.startswith('run-') or not review_file.is_relative_to(folder):raise SafeFailure('review_path_not_owned')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest['hashes']!=source_hashes():raise SafeFailure('evaluation_source_changed')
    packet=json.loads((folder/'review_packet.json').read_text(encoding='utf-8'))
    packet_hash=digest({k:v for k,v in packet.items() if k!='packet_hash'})
    review=json.loads(review_file.read_text(encoding='utf-8'))
    if packet.get('packet_hash')!=packet_hash or review.get('packet_hash')!=packet_hash:raise SafeFailure('review_packet_changed')
    reviewer=review.get('reviewer',{})
    if reviewer.get('type') not in {'agent','human'} or not isinstance(reviewer.get('id'),str) or not 1<=len(reviewer['id'])<=100:raise SafeFailure('reviewer_identity_required')
    ratings={}
    for rating in review.get('items',[]):
        if set(rating)!={'review_id','meaning_ok','language_ok','unsupported_fact','note'} or rating['review_id'] in ratings:raise SafeFailure('invalid_duplicate_rating')
        if any(type(rating[k]) is not bool for k in ('meaning_ok','language_ok','unsupported_fact')) or not isinstance(rating['note'],str) or len(rating['note'])>1000:raise SafeFailure('invalid_rating')
        ratings[rating['review_id']]=rating
    if set(ratings)!={i['review_id'] for i in packet['items']}:raise SafeFailure('review_incomplete')
    mapping=json.loads((folder/'review_mapping.json').read_text(encoding='utf-8'))
    by_job={mapping['links'][id]:rating for id,rating in ratings.items()}
    records=json.loads((folder/'attempts.json').read_text(encoding='utf-8'))
    original_report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    if original_report.get('attempts_hash')!=digest(records) or original_report.get('mapping_hash')!=digest(mapping) or original_report.get('packet_hash')!=packet_hash:raise SafeFailure('run_artifacts_changed')
    planned={j['job_id']:j for j in manifest['jobs']}
    if len(records)!=len(planned) or len({r['job_id'] for r in records})!=len(records):raise SafeFailure('incomplete_attempt_records')
    for record in records:
        job=planned.get(record['job_id'])
        if job is None or any(record[k]!=v for k,v in job.items()):raise SafeFailure('attempt_schedule_changed')
    data,config=load();cases={r['id']:r for r in data['cases']}
    for repeat in (1,2):
        for row in (data['cases'] if config.get('include_baseline',True) else []):
            output=baseline(row)
            records.append(dict(job_id=f'baseline-{repeat}-{row["id"]}',provider='baseline',stage='evaluation',repeat=repeat,case_id=row['id'],score=score(row,output),output=output))
    review_jobs={r['job_id'] for r in records if r['stage']=='evaluation' and r['score']['review_required']}
    if set(mapping['links'])!=set(ratings) or len(set(mapping['links'].values()))!=len(ratings) or set(mapping['links'].values())!=review_jobs:raise SafeFailure('review_mapping_changed')
    result={}
    holdout_count=sum(r['split']=='holdout' for r in data['cases'])*config['repetitions']
    tasks=tuple(t for t in ('plan','answer','draft','translation') if any(r['task']==t for r in data['cases']))
    for provider in (*config['providers'],*(('baseline',) if config.get('include_baseline',True) else ())):
        all_rows=[r for r in records if r['provider']==provider];hold=[r for r in all_rows if r['stage']=='evaluation' and cases[r['case_id']]['split']=='holdout']
        critical=0
        for record in all_rows:
            rating=by_job.get(record['job_id'])
            record['component_complete']=record['score']['mechanical_success'] and (not record['score']['review_required'] or bool(rating and rating['meaning_ok'] and rating['language_ok'] and not rating['unsupported_fact']))
            critical+=bool(record['score']['critical']) or bool(rating and rating['unsupported_fact'])
        complete=sum(r['component_complete'] for r in hold);available=sum(r['score']['schema_available'] for r in hold)
        gates={'no_critical':critical==0,'completion':Decimal(complete)/holdout_count>=Decimal(config['rules']['holdout_completion_min']),
            'availability':Decimal(available)/holdout_count>=Decimal(config['rules']['schema_availability_min'])}
        per_language={};per_task={}
        for language in ('en','id'):
            n=sum(r['component_complete'] for r in hold if cases[r['case_id']]['language']==language)
            planned=sum(r['split']=='holdout' and r['language']==language for r in data['cases'])*config['repetitions']
            per_language[language]={'complete':n,'planned':planned};gates['language_'+language]=Decimal(n)/planned>=Decimal(config['rules']['per_language_completion_min'])
        for task in tasks:
            group=[r for r in hold if cases[r['case_id']]['task']==task];n=sum(r['component_complete'] for r in group)
            per_task[task]={'complete':n,'planned':len(group),'meets_gate':bool(group) and Decimal(n)/len(group)>=Decimal(config['rules'][task+'_completion_min'])}
            gates['task_'+task]=per_task[task]['meets_gate']
        family_success={}
        for family in sorted({cases[r['case_id']]['family'] for r in hold}):
            group=[r for r in hold if cases[r['case_id']]['family']==family]
            family_success[family]=len(group)==4 and all(r['component_complete'] for r in group)
        result[provider]={'eligible':all(gates.values()),'gates':gates,'holdout_complete':complete,'planned':holdout_count,'critical_failures':critical,
            'per_language':per_language,'per_task':per_task,'negative_reason_agreement':reason_agreement(hold,cases),
            'independent_families_passed':sum(family_success.values()),'family_success':family_success}
        sent=[r for r in all_rows if r.get('sent')]
        result[provider]['cost']={'nominal_standard_upper_estimate_usd':str(sum((Decimal(r.get('nominal_usage_cost_usd',r['reservation_usd'])) for r in sent),Decimal(0))),
            'unknown_usage_attempts':sum(r.get('reported_usage') is None for r in sent),'invoice_verified':False,'human_labor_priced':False}
        if 'answer' not in tasks:result[provider].pop('negative_reason_agreement')
    assessment={'manifest_hash':manifest['manifest_hash'],'packet_hash':packet_hash,'reviewer':reviewer,'candidates':result,
        'automatic_winner':False,'production_changed':False,'scope':'Small paired-family component benchmark. Baseline translation is human deferral; UI effort/whole workflow latency and deployment quality are not measured.'}
    if config.get('dataset_profile')=='airport_translation_v1':assessment['scope']='Translation-only paired-family component benchmark. No other task or baseline is newly qualified; no production availability claim.'
    save(folder/'assessment.json',assessment)
    original_report.update(review_pending=False,assessment_hash=digest(assessment))
    save(folder/'report.json',original_report)
    return assessment

async def live(approved_hash,approved_cap,*,network=None,keys=None,clock=time.monotonic,sleep=asyncio.sleep):
    manifest=await prepare(persist=False);data,config=load()
    if config.get('retry_policy'):raise SafeFailure('retry_scope_requires_reliable_runner')
    if not isinstance(approved_cap,str) or not re.fullmatch(r'\d+(?:\.\d{1,8})?',approved_cap):raise SafeFailure('manifest_or_cap_not_authorized')
    if approved_hash!=manifest['manifest_hash'] or Decimal(approved_cap)!=Decimal(manifest['cap_usd']):raise SafeFailure('manifest_or_cap_not_authorized')
    saved=json.loads((OUT/'prepared_manifest.json').read_text(encoding='utf-8'))
    if saved!=manifest:raise SafeFailure('prepared_manifest_changed')
    if keys is None:
        from dotenv import dotenv_values
        local=dotenv_values(ROOT/'.env')
        keys={p:os.environ.get(c['key_env']) or local.get(c['key_env']) for p,c in config['providers'].items()}
    if set(keys)!=set(config['providers']) or any(not isinstance(k,str) or not k.strip() for k in keys.values()):raise SafeFailure('evaluation_credentials_missing')
    OUT.mkdir(parents=True,exist_ok=True)
    claim=OUT/('claimed-'+approved_hash+'.json')
    try:
        with claim.open('x',encoding='utf-8') as f:json.dump({'manifest':approved_hash,'cap_usd':approved_cap,'claimed_at':datetime.now(timezone.utc).isoformat()},f)
    except FileExistsError:raise SafeFailure('authorization_already_used_no_automatic_resume') from None
    folder=OUT/('run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]);folder.mkdir()
    rows={r['id']:r for r in data['cases']};records=[];blocked={};last={};versions={};reserved=Decimal(0);run_start=clock()
    save(folder/'manifest.json',manifest)
    for job in manifest['jobs']:
        provider=job['provider'];record={**job,'sent':False};output=None;fatal=None
        # Admit no new attempt unless both the throttle and the adapter's 20s
        # deadline fit. This is a run bound, not a production latency target.
        if clock()-run_start>=config.get('max_wall_seconds',1200)-27:
            blocked.update({p:'run_deadline' for p in config['providers']})
        if provider in blocked:record['status']='not_run_'+blocked[provider]
        else:
            amount=Decimal(job['reservation_usd'])
            if reserved+amount>Decimal(approved_cap):raise SafeFailure('budget_exhausted')
            reserved+=amount;record.update(status='reserved',reserved_total_usd=str(reserved))
            records.append(record);journal(folder,'reserved',record) # Durable before network, bounded write volume.
            wait=max(0,config['provider_interval_seconds']-(clock()-last.get(provider,-100000)))
            if wait:await sleep(wait)
            record['throttle_wait_seconds']=round(wait,3);last[provider]=clock();start=time.perf_counter()
            bridge=Bridge(provider,config['providers'][provider],keys[provider],job['canonical_digest'],digest,record,network=network)
            try:
                output=await invoke(OpenAIPlanner(PLACEHOLDER,transport=bridge),rows[job['case_id']])
                rendered=json.dumps(output,ensure_ascii=False)
                if any(k in rendered for k in keys.values()):raise SafeFailure('credential_marker_in_output')
                record['status']='completed'
            except AgentError as error:
                record['status']='adapter_rejected';record['safe_error_code']=error.code
            except SafeFailure as error:fatal=str(error);record['status']='stopped_'+fatal;output=None
            finally:
                # Identity and billing metadata must be checked even when the
                # adapter rejects the content/schema or the completion truncates.
                observed=record.get('observed_model');usage=record.get('reported_usage')
                if observed and versions.setdefault(provider,observed)!=observed:fatal='provider_version_drift'
                if record.get('identity_error'):fatal='provider_model_mismatch'
                if usage:
                    if usage['input']>job['reserved_input_tokens'] or usage['output']>config['max_output_tokens']:fatal='usage_exceeds_reservation'
                    record['nominal_usage_cost_usd']=str(nominal(config,provider,usage['input'],usage['output']))
                if fatal:record['status']='stopped_'+fatal;output=None
                record['adapter_ms']=round((time.perf_counter()-start)*1000,3)
                record['output']=output;record['score']=score(rows[job['case_id']],output)
                if record.get('safe_error_code') in {'AGENT_INVALID_RESPONSE','AGENT_TOOL_DENIED'}:
                    record['score']['critical'].append('unusable_contract_response')
                if job['stage']=='preflight' and record['status']!='completed':blocked[provider]='preflight_failed'
                elif record.get('http_status') not in (None,200) or record.get('transport_error') or (record.get('sent') and record.get('safe_error_code')=='AGENT_MODEL_UNAVAILABLE'):blocked[provider]='provider_unavailable'
                journal(folder,'result',record)
            print(json.dumps({'progress':job['job_id'],'provider':provider,'stage':job['stage'],'status':record['status']}),flush=True)
            if fatal:
                save(folder/'attempts.json',records);save(folder/'stop.json',{'reason':fatal,'reserved_usd':str(reserved)});raise SafeFailure(fatal)
            continue
        record.update(output=None,score=score(rows[job['case_id']],None));records.append(record);journal(folder,'not_run',record)
    save(folder/'attempts.json',records)
    report={'manifest_hash':approved_hash,'summary':summary(records,data),'reserved_usd':str(reserved),'observed_models':versions,
        'production_changed':False,'invoice_cost_verified':False,'review_pending':True,'scope':'Component model/adapters on fixed candidate context; not retrieval, live RLS, whole workflow SLA or actual business execution.'}
    save(folder/'report.json',report)
    # Label-hidden review packet; the mapping stays in a separate local file.
    baseline_records=[dict(job_id=f'baseline-{repeat}-{row["id"]}',provider='baseline',stage='evaluation',repeat=repeat,case_id=row['id'],output=baseline(row),score=score(row,baseline(row))) for repeat in (1,2) for row in data['cases']]
    packet,mapping=reviewed_packet(records+baseline_records,data,approved_hash)
    save(folder/'review_packet.json',packet);save(folder/'review_mapping.json',mapping)
    report.update(attempts_hash=digest(records),mapping_hash=digest(mapping),packet_hash=packet['packet_hash'])
    save(folder/'report.json',report)
    print(json.dumps({'run_folder':str(folder.relative_to(ROOT)) if folder.is_relative_to(ROOT) else folder.name,'requests_sent':sum(r['sent'] for r in records),'reserved_usd':str(reserved),'review_required':len(packet['items'])}))
    return folder

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','live','assess']);parser.add_argument('--approved-manifest');parser.add_argument('--approved-cap-usd');parser.add_argument('--run-folder',type=Path);parser.add_argument('--review-file',type=Path)
    args=parser.parse_args()
    try:
        if args.command=='prepare':
            manifest=asyncio.run(prepare());print(json.dumps({'manifest_hash':manifest['manifest_hash'],'attempts':len(manifest['jobs']),'reservation_usd':manifest['reservation_usd'],'proposed_cap_usd':manifest['cap_usd'],'provider_calls':0}))
        elif args.command=='live':
            if args.approved_manifest is None or args.approved_cap_usd is None:raise SafeFailure('explicit_manifest_and_cap_required')
            asyncio.run(live(args.approved_manifest,args.approved_cap_usd))
        else:
            if args.run_folder is None or args.review_file is None:raise SafeFailure('review_paths_required')
            print(json.dumps(assess(args.run_folder,args.review_file)))
    except (SafeFailure,ValueError,KeyError,TypeError,OSError):
        print(json.dumps({'failed':'evaluation_stopped_check_sanitized_local_artifacts'}));raise SystemExit(1) from None

if __name__=='__main__':main()
