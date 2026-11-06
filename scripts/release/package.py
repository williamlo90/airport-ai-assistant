"""Credential-free release inventory and clean candidate checkout (stdlib + git)."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
ROOT_FILES={'.gitignore','.gitattributes','.dockerignore','.env.example','README.md','PRODUCT_CONTRACT.md','ARCHITECTURE.md','DATA_AND_API_CONTRACTS.md','TEST_PLAN.md'}
PUBLIC_DIRS={'backend','frontend','reference_service','deploy','scripts','.github','tests','evaluations'}

def git(*args,cwd=ROOT):
 return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.PIPE)

def inventory():
 files=[]
 for raw in git('ls-files','--cached','--others','--exclude-standard','-z').split(b'\0'):
  if not raw:continue
  p=Path(raw.decode('utf-8'));name=p.as_posix()
  if p.parts[0] not in PUBLIC_DIRS and name not in ROOT_FILES:continue
  if any(x in {'node_modules','.venv','local','docs','__pycache__','.pytest_cache','.git'} for x in p.parts):continue
  if p.name.startswith('.env') and p.name!='.env.example':continue
  if p.suffix.lower() in {'.key','.pem','.sqlite','.sqlite3','.log','.pyc'}:continue
  full=ROOT/p
  if full.is_symlink():raise ValueError('Release inventory rejects symlinks')
  if not full.is_file():continue
  files.append((name,hashlib.sha256(full.read_bytes()).hexdigest()))
 return dict(sorted(files))

def check():
 plan=json.loads((ROOT/'deploy/release.json').read_text());pins=json.loads((ROOT/'deploy/base-images.json').read_text())
 assert plan['maximum_parallel_builds']==1 and plan['builder_memory']=='1536m'
 for key in ('python','node','nginx'):assert '@sha256:' in pins[key] and len(pins[key].split('@sha256:')[1])==64
 for image in plan['images'].values():
  text=(ROOT/image['dockerfile']).read_text();assert 'USER ' in text and 'COPY . .' not in text
 assert '--locked --no-dev --no-editable' in (ROOT/'deploy/containers/backend.Dockerfile').read_text()
 app=json.loads((ROOT/'deploy/kubernetes/base/web.json').read_text());containers=app['spec']['template']['spec']['containers']
 assert len(containers)==2 and containers[0]['command'][-1]=='--trusted-loopback-proxy'
 assert all(c['securityContext']['readOnlyRootFilesystem'] and c['securityContext']['allowPrivilegeEscalation'] is False for c in containers)
 assert not any(e['name']=='DATABASE_DIRECT_URL' for c in containers for e in c.get('env',[]))
 migration=json.loads((ROOT/'deploy/kubernetes/migration/job.json').read_text());c=migration['spec']['template']['spec']['containers'][0]
 assert [e['name'] for e in c['env']]==['DATABASE_DIRECT_URL'] and migration['spec']['backoffLimit']==0
 assert 'migration' not in (ROOT/'deploy/kubernetes/base/kustomization.yaml').read_text()
 assert json.loads((ROOT/'deploy/kubernetes/base/worker.json').read_text())['spec']['replicas']==0
 proxy=(ROOT/'deploy/containers/proxy.conf').read_text();assert 'X-Forwarded-Proto https;' in proxy and 'X-Forwarded-For $remote_addr;' in proxy
 assert 'Forwarded "";' in proxy and 'X-Forwarded-Host "";' in proxy
 for f in ('web','worker','service','config'):json.loads((ROOT/f'deploy/kubernetes/base/{f}.json').read_text())
 return {'static_release_checks':'PASS','files':len(inventory()),'image_build':'NOT_RUN','deployment':'NOT_RUN'}

def checkout(destination):
 destination=Path(destination).resolve();allowed=(ROOT/'local/team/release-01').resolve()
 if not destination.is_relative_to(allowed) or destination.exists():raise ValueError('Use a fresh ignored release-01 directory')
 source=destination.parent/(destination.name+'-origin')
 if source.exists():raise ValueError('Candidate origin already exists')
 index=Path(git('rev-parse','--git-path','index').decode().strip())
 if not index.is_absolute():index=ROOT/index
 index_before=hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None
 head_before=git('rev-parse','HEAD')
 files=inventory();source.mkdir(parents=True)
 for name,sha in files.items():
  out=source/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes((ROOT/name).read_bytes())
  assert hashlib.sha256(out.read_bytes()).hexdigest()==sha
 git('init','--quiet',cwd=source)
 (source/'.git/info/attributes').write_text('* -text\n',encoding='ascii')
 git('add','--all',cwd=source)
 git('-c','user.name=Release candidate','-c','user.email=release@invalid.example','commit','--quiet','-m','Isolated release candidate snapshot',cwd=source)
 commit=git('rev-parse','HEAD',cwd=source).decode().strip()
 git('clone','--quiet','--no-hardlinks','--no-checkout',str(source),str(destination),cwd=ROOT)
 (destination/'.git/info/attributes').write_text('* -text\n',encoding='ascii')
 git('checkout','--quiet','HEAD',cwd=destination)
 assert not git('status','--porcelain',cwd=destination)
 actual={n:hashlib.sha256((destination/n).read_bytes()).hexdigest() for n in files}
 tracked={n.decode('utf-8') for n in git('ls-files','-z',cwd=destination).split(b'\0') if n}
 assert actual==files and tracked==set(files),'Candidate clone content changed'
 index_after=hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None
 assert index_before==index_after and head_before==git('rev-parse','HEAD'),'Workspace Git state changed'
 manifest={'candidate_commit':commit,'not_workspace_HEAD':True,'files':files,'clean_checkout':True,'destination':str(destination),'clone_hashes':actual,'line_endings':'Exact original bytes; isolated Git info/attributes disables text conversion','root_index_sha256_before':index_before,'root_index_sha256_after':index_after,'root_head':head_before.decode().strip()}
 (destination.parent/(destination.name+'-manifest.json')).write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 return {'candidate_commit':commit,'clean_checkout':True,'files':len(files),'root_index_changed':False}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['check','checkout']);p.add_argument('--destination');a=p.parse_args()
 print(json.dumps(check() if a.action=='check' else checkout(a.destination),indent=2))
