import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from airport_assistant.modules.knowledge.models import Content,CreateDocument,EditVersion,Transition,Selection
from airport_assistant.modules.knowledge.service import KnowledgeService,IndexProof,overlaps
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError

NOW=datetime(2026,1,1,tzinfo=timezone.utc)
def content(**changes):return Content.model_validate(dict(body='Official parking instructions.',source_uri='urn:airport:parking:v1',service_type='parking',language='en',visibility='public',valid_from=NOW,policy_reference='P-OLD',**changes))
def version():return dict(id=uuid4(),document_id=uuid4(),revision=1,status='draft',basis_hash='a'*64,**content().model_dump())
class Fixture:
    def __init__(self):
        self.tenant,self.mid,self.org=uuid4(),uuid4(),uuid4();self.v=version();self.d=dict(id=self.v['document_id'],organization_id=self.org,revision=2)
        self.identity=SimpleNamespace(database_context=None,session=SimpleNamespace(kind='staff',tenant_id=self.tenant))
        self.active=True;self.scope=True;self.other=[];self.writes=[];self.tx_open=False;self.hook=None;self.ready=True;self.service=KnowledgeService(self,self)
    @asynccontextmanager
    async def transaction(self,context):
        self.tx_open=True
        try:yield self
        finally:self.tx_open=False
    async def session_active(self):return self.active
    async def admin(self,mid,org):return dict(organization_id=self.org) if self.scope and mid==self.mid else None
    async def parent(self,id):return self.d['id']
    async def document(self,id,lock=False):return self.d
    async def version(self,id,lock=False):return self.v
    async def readiness(self,t,id,basis):
        assert not self.tx_open
        if self.hook:self.hook()
        return IndexProof(t,id,basis,uuid4(),datetime.now(timezone.utc)+timedelta(minutes=1)) if self.ready else None
    async def published(self,id):return self.other
    async def publish(self,id,proof):self.writes.append('publish');return self.v
    async def withdraw(self,id,reason):self.writes.append(reason);return self.v
    async def edit(self,id,c):self.writes.append('edit');return self.v
    async def select(self,c):return self.other
    def command(self):return Transition(membership_id=self.mid,expected_revision=1)

@pytest.mark.parametrize('field,value',[('body',' '),('body','x\0'),('source_uri','http://example.com/a'),('source_uri','https://user:secret@example.com/a'),('source_uri','https://example.com/a?token=secret'),('source_uri','file:///secret'),('valid_from','2026-01-01T00:00:00'),('valid_to',NOW),('allowed_roles',['cs']),('visibility','staff')])
def test_strict_content(field,value):
    values=content().model_dump();values[field]=value
    with pytest.raises(ValidationError):Content.model_validate(values)
@pytest.mark.parametrize('changes',[dict(expected_revision=True),dict(expected_revision='1'),dict(expected_revision=0),dict(tenant_id=str(uuid4()))])
def test_strict_transition(changes):
    values=dict(membership_id=str(uuid4()),expected_revision=1);values.update(changes)
    with pytest.raises(ValidationError):Transition.model_validate(values)
def test_index_probe_outside_transaction_and_boundproof():
    f=Fixture();asyncio.run(f.service.publish(f.identity,f.v['id'],f.command()));assert f.writes==['publish']
@pytest.mark.parametrize('change',['notready','edit','revoke','expiredsession','conflict'])
def test_publish_rechecks_current_basis_acl_and_conflicts(change):
    f=Fixture()
    if change=='notready':f.ready=False
    if change=='edit':f.hook=lambda:f.v.update(revision=2)
    if change=='revoke':f.hook=lambda:setattr(f,'scope',False)
    if change=='expiredsession':f.hook=lambda:setattr(f,'active',False)
    if change=='conflict':f.other=[version()]
    with pytest.raises((DraftError,AuthError)):asyncio.run(f.service.publish(f.identity,f.v['id'],f.command()))
    assert f.writes==[]
def test_published_immutable_and_draft_never_selected():
    f=Fixture();f.v['status']='published'
    with pytest.raises(DraftError):asyncio.run(f.service.edit(f.identity,f.v['id'],EditVersion(membership_id=f.mid,expected_revision=1,content=content())))
    assert f.writes==[]
@pytest.mark.parametrize('field,value,expected',[('policy_reference','P-NEW',False),('valid_from',NOW+timedelta(days=2),True),('language','id',False),('service_type','lounge',False)])
def test_overlap_selection_dimensions(field,value,expected):
    a=version();b=version();b[field]=value;assert overlaps(a,b)==expected
def test_half_open_intervals_and_terminal_scopes():
    a=version();b=version();a['valid_to']=NOW+timedelta(days=1);b['valid_from']=a['valid_to'];assert not overlaps(a,b)
    a['valid_to']=None;a['applicability']['terminal']='T1';b['applicability']['terminal']='T2';assert not overlaps(a,b)
def test_expired_or_other_version_indexproof_does_not_match():
    t,v=uuid4(),uuid4();p=IndexProof(t,v,'a'*64,uuid4(),datetime.now(timezone.utc)-timedelta(seconds=1));assert not p.matches(t,v,'a'*64)
    p=IndexProof(t,v,'a'*64,uuid4(),datetime.now(timezone.utc)+timedelta(minutes=1));assert not p.matches(t,uuid4(),'a'*64)
def test_ambiguous_retrieval_does_not_choose_latest():
    f=Fixture();f.other=[version(),version()]
    with pytest.raises(DraftError):asyncio.run(f.service.select(f.identity,Selection(document_key='parking-policy',service_type='parking',language='en',as_of=NOW,policy_reference='P-OLD')))

@pytest.mark.parametrize('status',[405,413,500])
def test_knowledge_boundary_errors_are_no_store(status):
    import httpx
    from airport_assistant.api.boundary import RequestBoundary
    async def downstream(scope,receive,send):
        if status==500:raise RuntimeError('Private detail must not escape')
        await send({'type':'http.response.start','status':status,'headers':[]});await send({'type':'http.response.body','body':b''})
    async def scenario():
        app=RequestBoundary(downstream,max_body_bytes=10)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test.invalid') as c:
            r=await c.post('/api/v1/admin/knowledge/documents',content='x'*11 if status==413 else '')
            assert r.status_code==status and r.headers['cache-control']=='no-store'
            assert 'Private detail' not in r.text
    asyncio.run(scenario())
