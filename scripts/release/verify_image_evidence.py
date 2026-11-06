"""Verify OCI manifest identity and bounded release evidence without rebuilding."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ALLOWED_REUSE_DELTA={'.github/workflows/release.yml','scripts/release/verify_image_evidence.py','scripts/release/test_verify_image_evidence.py'}
ROLES=('backend','frontend','reference')

def validate_sources(expected,current,*,reuse=False):
 old={name:record['sha256'] for name,record in expected.items()}
 if not reuse:
  assert old==current,'Current checkout differs from built source'
 else:
  assert set(old)<=set(current),'Built source path was removed'
  assert set(current)-set(old)<=ALLOWED_REUSE_DELTA,'Unqualified new source path'
  assert all(current[p]==sha for p,sha in old.items() if p not in ALLOWED_REUSE_DELTA),'Runtime/build-context source changed'

def verify(images,metadata,cleanup,limits,commit):
 assert len(images)==3 and cleanup['owned_resources_removed'] is True and cleanup['synthetic_tls_uploaded'] is False
 assert (limits['Memory'],limits['CpuQuota'],limits['CpuPeriod'])==(1536*1024*1024,100000,100000)
 result={}
 for role,image in zip(ROLES,images):
  meta=metadata[role];digest=meta['containerimage.digest'];descriptor=meta['containerimage.descriptor'];loaded=image['Descriptor']
  assert digest.startswith('sha256:') and len(digest)==71
  assert descriptor['digest']==digest==loaded['digest'],'Manifest digest mismatch'
  assert descriptor['mediaType']==loaded['mediaType'] and descriptor['mediaType'] in {'application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'}
  assert image['Architecture']=='amd64' and image['Os']=='linux'
  assert descriptor['platform']=={'architecture':'amd64','os':'linux'}
  assert image['Config']['User']==('101:101' if role=='frontend' else '10001:10001')
  tag='westbridge-'+role+':release-candidate';assert tag in image['RepoTags']
  assert 'westbridge-'+role+'@'+digest in image['RepoDigests'],'Loaded image repository digest differs'
  revision=meta['buildx.build.provenance']['invocation']['parameters']['root']['request']['args']['vcs:revision']
  assert revision==commit,'Image build source revision differs'
  # Docker Id can be a config or manifest identity depending on image store.
  # Descriptor/RepoDigests above bind the actual OCI manifest; do not conflate them.
  result[role]={'manifest_digest':digest,'reported_image_id':image['Id'],'identity_kind':'manifest_descriptor','user':image['Config']['User'],'reported_size_bytes':image['Size']}
 return result

def current_sources(root):
 files={}
 for raw in subprocess.check_output(['git','ls-files','-z'],cwd=root).split(b'\0'):
  if raw:
   p=raw.decode();files[p]=hashlib.sha256((root/p).read_bytes()).hexdigest()
 return files

def main():
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--reuse',action='store_true');a=p.parse_args();out=a.directory;root=Path(__file__).resolve().parents[2]
 load=lambda name:json.loads((out/name).read_text())
 provenance=load('source-provenance.json');commit=provenance['commit'];current=current_sources(root);validate_sources(provenance['files'],current,reuse=a.reuse)
 images=verify(load('images-inspected.json'),{role:load(role+'.json') for role in ROLES},load('cleanup.json'),load('builder-limits.json'),commit)
 report={'passed':True,'built_commit':commit,'verified_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),'reused_build_outputs':a.reuse,'source_files_verified':len(current),'runtime_context_unchanged':True,'images':images,'cleanup_accepted':True,'application_deployment':False,'provider_calls':0,'emails_sent':0}
 (out/'qualification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
