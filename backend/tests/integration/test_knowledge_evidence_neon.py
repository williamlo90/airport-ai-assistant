"""KNOW-03 current-source consumption on isolated Neon; never live model calls."""
from uuid import uuid4
from datetime import datetime, timezone
import pytest
from test_knowledge_vectors_neon import target, knowledge_target, auth_target, setup, search
from test_knowledge_vectors_neon import test_cached_real_openai_vectors_rank_holdout_in_pgvector
from test_knowledge_neon import content, login, identity
from test_staff_auth import harness, seed, run, row, change, insert
from airport_assistant.modules.knowledge.evidence import KnowledgeEvidence, EvidencePolicy
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError

import os
pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated KNOW-03 source evidence tests.')


def evidence(retrieval):
    # Explicit deterministic-vector fixture policy, not a production threshold.
    return KnowledgeEvidence(retrieval,EvidencePolicy(.7,'deterministic-test-only-v1',300),b'k'*48)


async def document(lifecycle,retrieval,actor,admin,**changes):
    doc=await lifecycle.create(actor,dict(membership_id=str(admin['membership']),document_key='evidence-'+uuid4().hex,title='Policy',content=content(**changes)))
    version=(await lifecycle.list(actor,doc['id'],admin['membership']))['items'][0]
    await publish(lifecycle,retrieval,actor,admin,version)
    return doc,version


async def publish(lifecycle,retrieval,actor,admin,version,raw=False):
    command=dict(membership_id=str(admin['membership']),expected_revision=version['revision'])
    proof=await retrieval.prepare(actor,version['id'],command)
    if raw:
        # Deliberately emulate conflicting legacy data through a valid runtime index,
        # bypassing service overlap detection, to test the reader fail-closed path.
        async with lifecycle.transaction(actor) as tx:
            await lifecycle.version(tx,actor,version['id'],admin['membership'])
            return await tx.publish(version['id'],proof)
    return await lifecycle.publish(actor,version['id'],command)


def test_history_update_new_publication_and_withdraw_invalidate_cached_sources(target):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            lifecycle,retrieval,_=setup(h,target);ev=evidence(retrieval)
            async with h.client() as c,h.client() as public:
                await login(h,c,admin);await h.boot(public);actor=await identity(h,c);anon=await identity(h,public)
                doc,old=await document(lifecycle,retrieval,actor,admin,body='parking old policy',applicability=dict(terminal='T2',booking_created_from='2024-01-01T00:00:00Z',booking_created_to='2026-01-01T00:00:00Z'))
                historic=search(document_key=doc['document_key'],as_of='2026-02-01T00:00:00Z',terminal='T2',booking_created_at='2025-01-01T00:00:00Z')
                first=await ev.collect(anon,historic)
                assert first.status=='sources_available' and first.chunks[0].body=='parking old policy'
                for changes in (dict(terminal=None),dict(terminal='T1'),dict(booking_created_at=None),dict(booking_created_at=datetime(2026,1,1,tzinfo=timezone.utc)),dict(policy_reference='MISSING')):
                    assert (await ev.collect(anon,historic.model_copy(update=changes))).status=='insufficient_sources'
                current=await lifecycle.get(actor,doc['id'],admin['membership'])
                new=await lifecycle.add_version(actor,doc['id'],dict(membership_id=str(admin['membership']),expected_document_revision=current['revision'],content=content(body='parking new policy',policy_reference='P-NEW',valid_from='2026-01-01T00:00:00Z',supersedes_version_id=str(old['id']))))
                assert (await ev.consume(anon,historic,first.snapshot)).status=='stale_sources'
                # Draft NEW never replaces the applicable OLD reference.
                assert (await ev.collect(anon,historic)).chunks[0].body=='parking old policy'
                new_query=historic.model_copy(update={'policy_reference':'P-NEW'})
                assert (await ev.collect(anon,new_query)).status=='insufficient_sources'
                await publish(lifecycle,retrieval,actor,admin,new)
                assert (await ev.collect(anon,new_query)).chunks[0].body=='parking new policy'
                still_old=await ev.collect(anon,historic)
                assert still_old.chunks[0].citation.version_id==old['id']
                # A new independently published document must invalidate a broad cached
                # query even though its originally cited document revision is unchanged.
                broad=historic.model_copy(update={'document_key':None})
                cached_broad=await ev.collect(anon,broad)
                revision=still_old.chunks[0].citation.document_revision
                await document(lifecycle,retrieval,actor,admin,body='parking related information')
                assert row(target,'SELECT revision FROM {s}.knowledge_documents WHERE id=:id',id=doc['id'])[0]==revision
                assert (await ev.consume(anon,broad,cached_broad.snapshot)).status=='stale_sources'
                await lifecycle.withdraw(actor,old['id'],dict(membership_id=str(admin['membership']),expected_revision=2,reason='Retired'))
                assert (await ev.consume(anon,historic,still_old.snapshot)).status=='stale_sources'
                assert (await ev.collect(anon,historic)).status=='insufficient_sources'
                assert (await ev.collect(anon,new_query)).status=='sources_available'
    run(scenario())


def test_empty_low_similarity_and_metadata_conflict_do_not_return_source_context(target):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            lifecycle,retrieval,embedding=setup(h,target);ev=evidence(retrieval)
            async with h.client() as c,h.client() as public:
                await login(h,c,admin);await h.boot(public);actor=await identity(h,c);anon=await identity(h,public)
                assert (await ev.collect(anon,search(document_key='does-not-exist'))).status=='insufficient_sources'
                doc,old=await document(lifecycle,retrieval,actor,admin,body='parking instructions')
                query=search(document_key=doc['document_key'],limit=1)
                assert (await ev.collect(anon,query.model_copy(update={'query':'astronomy and telescopes'}))).status=='insufficient_sources'
                embedding.fail=True
                failed=await ev.collect(anon,query)
                assert failed.status=='source_unavailable' and not failed.chunks
                embedding.fail=False
                current=await lifecycle.get(actor,doc['id'],admin['membership'])
                new=await lifecycle.add_version(actor,doc['id'],dict(membership_id=str(admin['membership']),expected_document_revision=current['revision'],content=content(body='parking contradictory instructions')))
                await publish(lifecycle,retrieval,actor,admin,new,raw=True)
                result=await ev.collect(anon,query)
                assert result.status=='conflicting_sources' and result.chunks==() and result.snapshot is None
                with pytest.raises(DraftError):await lifecycle.select(anon,query.model_dump(exclude={'query','limit'}))
    run(scenario())


def test_cached_evidence_rechecks_live_acl_session_and_tenant(target):
    admin=seed(target,roles=('admin',));cs=seed(target,roles=('cs',));foreign=seed(target,roles=('admin',),tenant=target['b'])
    with target['engine'].begin() as db:
        mid=insert(db,target['schema'],'staff_memberships',tenant_id=admin['tenant'],principal_id=cs['principal'],organization_id=admin['org'])
        insert(db,target['schema'],'membership_roles',tenant_id=admin['tenant'],membership_id=mid,role='cs')
    async def scenario():
        async with harness(target) as h:
            lifecycle,retrieval,_=setup(h,target);ev=evidence(retrieval)
            async with h.client() as ac,h.client() as staff,h.client() as public,h.client('b.example.test') as b:
                await login(h,ac,admin);await login(h,staff,cs);await h.boot(public);await login(h,b,foreign)
                actor=await identity(h,ac);reader=await identity(h,staff);anon=await identity(h,public)
                from airport_assistant.api.security import SESSION_COOKIE
                other=await h.app.state.services.access.resolve(target['b'],b.cookies.get(SESSION_COOKIE))
                doc,version=await document(lifecycle,retrieval,actor,admin,body='parking private policy',visibility='staff',allowed_roles=['cs'])
                query=search(document_key=doc['document_key'])
                ready=await ev.collect(reader,query)
                assert ready.status=='sources_available'
                for wrong in (anon,other,actor):
                    result=await ev.consume(wrong,query,ready.snapshot)
                    assert result.status=='stale_sources' and not result.chunks
                change(target,"DELETE FROM {s}.membership_roles WHERE membership_id=:m AND role='cs'",m=mid)
                assert (await ev.consume(reader,query,ready.snapshot)).status=='stale_sources'
                assert (await ev.collect(reader,query)).status=='insufficient_sources'
                change(target,'UPDATE {s}.sessions SET revoked_at=clock_timestamp() WHERE id=:id',id=reader.session.id)
                with pytest.raises(AuthError):await ev.consume(reader,query,ready.snapshot)
    run(scenario())
