"""KNOW-02 isolated pgvector lifecycle; deterministic vectors, never paid API calls."""
import os
from pathlib import Path
from uuid import UUID, uuid4
import pytest
from test_knowledge_neon import target as knowledge_target, auth_target, content, login, create, identity, selection
from test_staff_auth import harness, seed, run, row, change
from airport_assistant.database_config import load_database_settings, MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.knowledge import PostgresKnowledge
from airport_assistant.modules.knowledge.service import KnowledgeService,IndexProof
from airport_assistant.modules.knowledge.retrieval import KnowledgeRetrieval,Search,normalized
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.infrastructure.database.connections import DatabaseError
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from airport_assistant.modules.identity.auth import AuthError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated KNOW-02 pgvector tests.')


@pytest.fixture(scope='module')
def target(knowledge_target):
    t=knowledge_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0025_knowledge_vectors')=='0025_knowledge_vectors'
    return t


class Embed:
    def __init__(self):self.calls=[];self.hook=None;self.fail=False
    async def embed(self,texts):
        self.calls.append(texts)
        if self.hook:await self.hook()
        if self.fail:raise DraftError(503,'EMBEDDING_UNAVAILABLE')
        # Non-collinear vectors; query 'parking' ranks parking above lounge.
        return [normalized([1 if 'parking' in t.lower() else 0,1 if 'lounge' in t.lower() else 0]+[0.01]*1534) for t in texts]


def setup(h,t):
    lifecycle=KnowledgeService(PostgresKnowledge(h.db,t['schema']))
    embedding=Embed();retrieval=KnowledgeRetrieval(lifecycle,embedding);lifecycle.retrieval=retrieval
    return lifecycle,retrieval,embedding


def search(**changes):
    values=dict(query='parking',service_type='parking',language='en',as_of='2025-06-01T00:00:00Z',policy_reference='P-OLD')
    values.update(changes)
    return Search.model_validate(values)


def test_pgvector_real_readiness_publish_retrieval_acl_and_withdraw(target):
    admin=seed(target,roles=('admin',));staff=seed(target,roles=('cs',));foreign=seed(target,roles=('admin',),tenant=target['b'])
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0,max_size=2)
            lifecycle,retrieval,embedding=setup(h,target)
            from airport_assistant.main import create_app
            from airport_assistant.config import Settings
            from airport_assistant.database_config import RuntimeDatabaseSettings
            from airport_assistant.auth_config import AuthSettings
            h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),
                database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),
                auth_settings=AuthSettings(SESSION_SECRET=target['key']),
                services=replace(h.app.state.services,knowledge=lifecycle))
            async with h.client() as ac,h.client() as public,h.client() as cs,h.client('b.example.test') as b:
                await login(h,ac,admin);await h.boot(public);await login(h,cs,staff);await login(h,b,foreign)
                actor=await identity(h,ac);anon=await identity(h,public);csactor=await identity(h,cs)
                # Create through the same lifecycle then real index: no ReadyIndex stub.
                doc=await lifecycle.create(actor,dict(membership_id=str(admin['membership']),document_key='vector-'+uuid4().hex,title='Parking',content=content(body='parking policy '+('details '*100))))
                version=(await lifecycle.list(actor,doc['id'],admin['membership']))['items'][0]
                command=dict(membership_id=str(admin['membership']),expected_revision=1)
                with pytest.raises(DraftError):await lifecycle.publish(actor,version['id'],command)
                url='/api/v1/admin/knowledge/versions/'+str(version['id'])+'/index'
                assert (await ac.post(url,json=command)).status_code==403
                response=await ac.post(url,headers=h.headers(ac),json=command)
                assert response.status_code==200,response.text
                assert response.headers['cache-control']=='no-store'
                proof=await retrieval.readiness(actor,version['id'],version['basis_hash'])
                assert response.json()['generation']==str(proof.generation)
                count=len(embedding.calls)
                assert (await retrieval.prepare(actor,version['id'],command)).generation==proof.generation
                assert len(embedding.calls)==count
                assert await retrieval.search(anon,search(document_key=doc['document_key']))==[]
                published=await lifecycle.publish(actor,version['id'],command)
                result=await retrieval.search(anon,search(document_key=doc['document_key']))
                assert result and result[0]['version_id']==version['id'] and result[0]['score']>.99
                assert result[0]['source_uri']=='urn:airport:parking:v1' and result[0]['generation']==proof.generation
                assert await retrieval.search(anon,search(document_key=doc['document_key'],policy_reference='missing'))==[]
                assert await retrieval.search(anon,search(document_key=doc['document_key'],as_of='2024-01-01T00:00:00Z'))==[]
                # Tenant B and staff from another org do not acquire private chunks.
                private=await lifecycle.create(actor,dict(membership_id=str(admin['membership']),document_key='private-'+uuid4().hex,title='Internal',content=content(body='parking private',visibility='staff',allowed_roles=['cs'])))
                pv=(await lifecycle.list(actor,private['id'],admin['membership']))['items'][0]
                await retrieval.prepare(actor,pv['id'],command);await lifecycle.publish(actor,pv['id'],command)
                for who in (anon,csactor,actor):assert await retrieval.search(who,search(document_key=private['document_key']))==[]
                # A matching role and organization must come from one membership.
                from test_staff_auth import insert
                with target['engine'].begin() as db:
                    mid=insert(db,target['schema'],'staff_memberships',tenant_id=admin['tenant'],principal_id=staff['principal'],organization_id=admin['org'])
                    insert(db,target['schema'],'membership_roles',tenant_id=admin['tenant'],membership_id=mid,role='cs')
                assert await retrieval.search(csactor,search(document_key=private['document_key']))
                async def revoke():
                    embedding.hook=None
                    change(target,"DELETE FROM {s}.membership_roles WHERE membership_id=:m AND role='cs'",m=mid)
                embedding.hook=revoke
                assert await retrieval.search(csactor,search(document_key=private['document_key']))==[]
                from airport_assistant.api.security import SESSION_COOKIE
                other=await h.app.state.services.access.resolve(target['b'],b.cookies.get(SESSION_COOKIE))
                assert await retrieval.search(other,search(document_key=doc['document_key']))==[]
                # Publishing with a forged generation is rejected by SQL, independently of service.
                new=await lifecycle.create(actor,dict(membership_id=str(admin['membership']),document_key='forged-'+uuid4().hex,title='Unindexed',content=content()))
                nv=(await lifecycle.list(actor,new['id'],admin['membership']))['items'][0]
                with pytest.raises(DraftError):
                    async with lifecycle.transaction(actor) as tx:
                        await lifecycle.version(tx,actor,nv['id'],admin['membership'])
                        await tx.publish(nv['id'],IndexProof(target['a'],nv['id'],nv['basis_hash'],uuid4(),datetime.now(timezone.utc)+timedelta(seconds=60)))
                # Runtime chunks are append-only and cannot be rewritten or deleted.
                for statement in ('DELETE FROM {s}.knowledge_chunks WHERE version_id=%s',"UPDATE {s}.knowledge_chunks SET body='fake' WHERE version_id=%s"):
                    with pytest.raises(DatabaseError):
                        async with h.db.transaction(actor.database_context) as db:await db.execute(statement.format(s='"'+target['schema']+'"'),(version['id'],))
                await lifecycle.withdraw(actor,version['id'],dict(membership_id=str(admin['membership']),expected_revision=published['revision'],reason='Expired'))
                assert await retrieval.search(anon,search(document_key=doc['document_key']))==[]
                async def revoke_session():
                    embedding.hook=None
                    change(target,'UPDATE {s}.sessions SET revoked_at=clock_timestamp() WHERE id=:sid',sid=anon.session.id)
                embedding.hook=revoke_session
                with pytest.raises(AuthError):await retrieval.search(anon,search(document_key=doc['document_key']))
    run(scenario())


def test_cached_real_openai_vectors_rank_holdout_in_pgvector(target):
    import json,hashlib
    root=Path(__file__).resolve().parents[3]
    cached=root/'local/provider-selection/run-20261105T012419-318064Z'
    if not (cached/'openai_vectors.json').is_file():pytest.skip('Private recorded provider vectors not present; no live calls permitted.')
    data=json.loads((root/'evaluations/datasets/embedding_selection.json').read_text(encoding='utf-8'))
    manifest=json.loads((cached/'manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()==manifest['dataset_hash']
    vectors=json.loads((cached/'openai_vectors.json').read_text(encoding='utf-8'))
    expected=json.loads((cached/'openai_retrieval.json').read_text(encoding='utf-8'))
    recorded={r['text']:vectors[r['id']] for r in data['chunks']+data['queries']}
    class Recorded:
        async def embed(self,texts):return [recorded[t] for t in texts]
    admin=seed(target,roles=('admin',));version_map={}
    async def scenario():
        async with harness(target) as h:
            lifecycle=KnowledgeService(PostgresKnowledge(h.db,target['schema']))
            retrieval=KnowledgeRetrieval(lifecycle,Recorded());lifecycle.retrieval=retrieval
            async with h.client() as c,h.client() as public:
                await login(h,c,admin);await h.boot(public);actor=await identity(h,c);anon=await identity(h,public)
                for chunk in data['chunks']:
                    assert len(chunk['text'])<384
                    doc=await lifecycle.create(actor,dict(membership_id=str(admin['membership']),document_key='fixture-'+uuid4().hex,title=chunk['id'],content=content(body=chunk['text'],language=chunk['language'],service_type='general',policy_reference=None)))
                    version=(await lifecycle.list(actor,doc['id'],admin['membership']))['items'][0]
                    version_map[version['id']]=chunk['id']
                    command=dict(membership_id=str(admin['membership']),expected_revision=1)
                    await retrieval.prepare(actor,version['id'],command);await lifecycle.publish(actor,version['id'],command)
                results=[]
                for q in data['queries']:
                    if q['split']!='holdout' or not q['answerable']:continue
                    found=await retrieval.search(anon,search(query=q['text'],language=q['source_language'],service_type='general',policy_reference=None))
                    ids=[version_map[r['version_id']] for r in found]
                    reciprocal=next((1/(i+1) for i,cid in enumerate(ids) if cid in q['relevant_ids']),0)
                    results.append(reciprocal)
                assert len(results)==16 and all(r>0 for r in results)
                assert sum(results)/16==pytest.approx(expected['groups']['holdout/all']['mrr5'])
    run(scenario())


def test_index_edit_during_provider_and_failure_leave_no_partial_chunks(target):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            lifecycle,retrieval,embedding=setup(h,target)
            async with h.client() as c:
                await login(h,c,admin);actor=await identity(h,c)
                doc=await lifecycle.create(actor,dict(membership_id=str(admin['membership']),document_key='race-'+uuid4().hex,title='Race',content=content(body='parking '*1000)))
                version=(await lifecycle.list(actor,doc['id'],admin['membership']))['items'][0]
                async def edit():
                    embedding.hook=None
                    await lifecycle.edit(actor,version['id'],dict(membership_id=str(admin['membership']),expected_revision=1,content=content(body='new parking policy')))
                embedding.hook=edit
                with pytest.raises(DraftError) as error:await retrieval.prepare(actor,version['id'],dict(membership_id=str(admin['membership']),expected_revision=1))
                assert error.value.code=='VERSION_CONFLICT'
                assert row(target,'SELECT count(*) FROM {s}.knowledge_chunks WHERE version_id=:v',v=version['id'])[0]==0
                embedding.fail=True
                with pytest.raises(DraftError):await retrieval.prepare(actor,version['id'],dict(membership_id=str(admin['membership']),expected_revision=2))
                assert row(target,'SELECT count(*) FROM {s}.knowledge_chunks WHERE version_id=:v',v=version['id'])[0]==0
    run(scenario())
