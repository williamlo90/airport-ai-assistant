"""Integrity negatives for OCI/source evidence; no Docker, network or app suite."""
import copy,unittest
from verify_image_evidence import ROLES,verify,validate_sources

class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.images=[];self.meta={};self.cleanup={'owned_resources_removed':True,'synthetic_tls_uploaded':False};self.limits={'Memory':1536*1024*1024,'CpuQuota':100000,'CpuPeriod':100000}
  for i,role in enumerate(ROLES):
   digest='sha256:'+str(i)*64;descriptor={'digest':digest,'mediaType':'application/vnd.oci.image.manifest.v1+json','platform':{'architecture':'amd64','os':'linux'}}
   self.images.append({'Id':'opaque-id','Descriptor':copy.deepcopy(descriptor),'Architecture':'amd64','Os':'linux','Config':{'User':'101:101' if role=='frontend' else '10001:10001'},'RepoTags':['westbridge-'+role+':release-candidate'],'RepoDigests':['westbridge-'+role+'@'+digest],'Size':1})
   self.meta[role]={'containerimage.digest':digest,'containerimage.descriptor':descriptor,'buildx.build.provenance':{'invocation':{'parameters':{'root':{'request':{'args':{'vcs:revision':'built'}}}}}}}
 def check(self):return verify(self.images,self.meta,self.cleanup,self.limits,'built')
 def test_manifest_identity_accepts_opaque_id_without_missing_config_field(self):self.assertEqual(len(self.check()),3)
 def test_wrong_loaded_manifest_rejected(self):
  self.images[0]['Descriptor']['digest']='sha256:'+'f'*64
  with self.assertRaises(AssertionError):self.check()
 def test_wrong_role_rejected(self):
  self.images[0]['Config']['User']='0'
  with self.assertRaises(AssertionError):self.check()
 def test_wrong_platform_rejected(self):
  self.images[0]['Architecture']='arm64'
  with self.assertRaises(AssertionError):self.check()
 def test_wrong_repo_digest_rejected(self):
  self.images[0]['RepoDigests']=[]
  with self.assertRaises(AssertionError):self.check()
 def test_cleanup_failure_rejected(self):
  self.cleanup['owned_resources_removed']=False
  with self.assertRaises(AssertionError):self.check()
 def test_wrong_cpu_budget_rejected(self):
  self.limits['CpuQuota']=200000
  with self.assertRaises(AssertionError):self.check()
 def test_wrong_revision_rejected(self):
  with self.assertRaises(AssertionError):verify(self.images,self.meta,self.cleanup,self.limits,'other')
 def test_unchanged_runtime_can_reuse_after_workflow_only_fix(self):
  validate_sources({'backend/a.py':{'sha256':'same'},'.github/workflows/release.yml':{'sha256':'old'}},{'backend/a.py':'same','.github/workflows/release.yml':'new','scripts/release/verify_image_evidence.py':'verifier'},reuse=True)
 def test_runtime_delta_cannot_reuse(self):
  with self.assertRaises(AssertionError):validate_sources({'backend/a.py':{'sha256':'old'}},{'backend/a.py':'new'},reuse=True)
 def test_unknown_new_source_cannot_reuse(self):
  with self.assertRaises(AssertionError):validate_sources({}, {'backend/other.py':'new'},reuse=True)
 def test_removed_source_cannot_reuse(self):
  with self.assertRaises(AssertionError):validate_sources({'backend/a.py':{'sha256':'old'}},{},reuse=True)

if __name__=='__main__':unittest.main()
