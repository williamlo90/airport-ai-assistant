"""12-slot bounded production-adapter model sample, with durable reservation and no retries."""
import asyncio,json,os,sys,time,traceback
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local/team/quality-final';sys.path[:0]=[str(OUT),str(ROOT/'backend/src'),str(ROOT/'evaluations/runners')]
from common import save,snapshot,preflight,freeze_guard,digest,percentiles,memory,artifact
from agent_selection import canonical,invoke
from agent_transport import Bridge
from airport_assistant.integrations.agent.openai import OpenAIPlanner
MODEL='gpt-5.4-mini-2026-03-17';CAP=Decimal('.25');MAX=12

def rows(stage):return json.loads((OUT/(('calibration' if stage=='calibration' else 'holdout')+'-dataset.json')).read_text(encoding='utf-8'))['cases']
def reserve(payload):return (Decimal(len(json.dumps(payload,ensure_ascii=False).encode())+2048)*Decimal('.75')+Decimal(1024)*Decimal('4.50'))/Decimal(1000000)
def journal(record):
 with (OUT/'provider-ledger.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record,ensure_ascii=True)+'\n');f.flush();os.fsync(f.fileno())
def ledger():return [json.loads(l) for l in (OUT/'provider-ledger.jsonl').read_text(encoding='utf-8').splitlines()] if (OUT/'provider-ledger.jsonl').exists() else []
async def prepare(*,persist=True):
 payloads={r['id']:await canonical(r) for stage in ('calibration','acceptance') for r in rows(stage)}
 total=sum((reserve(p) for p in payloads.values()),Decimal(0));assert len(payloads)==MAX and total<=CAP
 hashes=snapshot();v={'model':MODEL,'jobs':[{ 'id':r['id'],'stage':stage,'family':r['family'],'payload_hash':digest(payloads[r['id']]),'reservation_usd':str(reserve(payloads[r['id']]))} for stage in ('calibration','acceptance') for r in rows(stage)],'total_reservation_usd':str(total),'cap_usd':str(CAP),'max_physical_requests':MAX,'retry':False,'hashes':hashes,'canonical_payloads':payloads}
 v['manifest_hash']=digest(v)
 if not persist:return v
 save('model-manifest.json',v)
 # Exact current request-payload parity for previously accepted component qualification.
 parity=[]
 for folder,dataset in [('local/ai-04-acceptance-v3','evaluations/datasets/agent_acceptance_v3.json'),('local/ai-04-translation-v1','evaluations/datasets/translation_acceptance_v1.json')]:
  m=json.loads((ROOT/folder/'prepared_manifest.json').read_text(encoding='utf-8'));prior=m['canonical_payloads'];data=json.loads((ROOT/dataset).read_text(encoding='utf-8'))
  mismatches=[]
  for row in data['cases']:
   if await canonical(row)!=prior[row['id']]:mismatches.append(row['id'])
  parity.append({'manifest':folder+'/prepared_manifest.json','payloads':len(prior),'current_wire_mismatches':mismatches,'scope':'Exact model request parity, not global code hash identity or a new model run'})
 save('model-payload-parity.json',parity)
 print('Prepared 12 slots, reservation',total,'parity',[(p['payloads'],len(p['current_wire_mismatches'])) for p in parity],flush=True)
 return v
async def live(stage):
 preflight()
 lock=freeze_guard('model') if stage=='acceptance' else None
 m=json.loads(artifact('model-manifest.json').read_text(encoding='utf-8'));current=snapshot()
 if m!=await prepare(persist=False):raise RuntimeError('Prepared manifest changed from exact source/dataset/config')
 if any(current.get(k)!=v for k,v in m['hashes'].items()):raise RuntimeError('Model manifest inputs changed')
 if artifact(stage+'-model-result.json').exists():raise RuntimeError('Result namespace already exists')
 jobs=[j for j in m['jobs'] if j['stage']==stage];cases={r['id']:r for r in rows(stage)}
 assert len(jobs)==(4 if stage=='calibration' else 8)
 old=ledger();sent=[r for r in old if r['event']=='reserved'];reserved=sum((Decimal(r['reservation_usd']) for r in sent),Decimal(0))
 if {r['id'] for r in sent}&{j['id'] for j in jobs}:raise RuntimeError('One-shot stage already reserved; no resume/retry')
 # Load only inside authorized script after all admission checks; never emit credential.
 from dotenv import dotenv_values
 key=os.environ.get('EVAL_OPENAI_API_KEY') or dotenv_values(ROOT/'.env').get('EVAL_OPENAI_API_KEY')
 if not isinstance(key,str) or not key.strip():raise RuntimeError('OpenAI credential missing')
 results=[];start=time.monotonic();last=0;config={'model':MODEL}
 for job in jobs:
  delay=max(0,6.5-(time.monotonic()-last))
  if delay:await asyncio.sleep(delay)
  if len(sent)>=MAX or reserved+Decimal(job['reservation_usd'])>CAP or memory()['peak_rss_mib']>768:raise RuntimeError('Admission budget/resource exceeded')
  payload=await canonical(cases[job['id']]);assert digest(payload)==job['payload_hash']
  reservation={'event':'reserved','id':job['id'],'stage':stage,'reservation_usd':job['reservation_usd'],'manifest_hash':m['manifest_hash'],'at':time.monotonic()};journal(reservation);sent.append(reservation);reserved+=Decimal(job['reservation_usd'])
  record={'id':job['id'],'stage':stage,'task':cases[job['id']]['task'],'language':cases[job['id']]['language'],'reservation_usd':job['reservation_usd'],'status':'unavailable','output':None};bridge=Bridge('openai',config,key,job['payload_hash'],digest,record);planner=OpenAIPlanner(key,MODEL,transport=bridge);at=time.monotonic();last=at
  try:record['output']=await invoke(planner,cases[job['id']]);record['status']='completed'
  except Exception as e:record['error_type']=type(e).__name__;record['safe_error_code']=getattr(e,'code',None)
  finally:
   record['logical_ms']=round((time.monotonic()-at)*1000,3);usage=record.get('reported_usage');record['cost_upper_usd']=str((Decimal(usage['input'])*Decimal('.75')+Decimal(usage['output'])*Decimal('4.50'))/Decimal(1000000)) if usage else job['reservation_usd'];record['invoice_verified']=False
   if Decimal(record['cost_upper_usd'])>Decimal(job['reservation_usd']):record['reservation_violation']=True
   results.append(record);journal({'event':'result',**record});save(stage+'-model-progress.json',results)
  if record.get('reservation_violation') or record.get('provider_error_code') in {'invalid_api_key','insufficient_quota'}:break
 report={'stage':stage,'freeze_hash':lock['freeze_hash'] if lock else None,'model':MODEL,'manifest_hash':m['manifest_hash'],'planned':len(jobs),'completed':sum(r['status']=='completed' for r in results),'records':results,'request_latency':percentiles([r['logical_ms'] for r in results]),'total_reserved_to_date_usd':str(reserved),'observed_upper_cost_usd':str(sum((Decimal(r['cost_upper_usd']) for r in results),Decimal(0))),'unknown_usage_attempts':sum(r.get('reported_usage') is None for r in results),'job_seconds':round(time.monotonic()-start,2),'semantic_review_pending':True,'structural_pass':len(results)==len(jobs) and all(r['status']=='completed' and not r.get('reservation_violation') for r in results)}
 save(stage+'-model-result.json',report);print(stage,'model',report['completed'],'/',report['planned'],'cost upper',report['observed_upper_cost_usd'],flush=True)
async def main():
 stage=sys.argv[1];assert stage in {'prepare','calibration','acceptance'};preflight()
 if os.environ.get('QUALITY_COHORT') and stage=='calibration':raise RuntimeError('Follow-up cannot rerun calibration')
 if stage=='prepare':await prepare()
 else:await live(stage)
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as e:save('model-runner-error.json',{'type':type(e).__name__,'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(e.__traceback__)]});print('Model runner stopped:',type(e).__name__);raise SystemExit(1)
