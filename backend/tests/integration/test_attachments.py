"""CASE-04 on isolated Neon roles/schema, controlled scanner (not engine proof)."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import os
from uuid import UUID,uuid4
from unittest.mock import AsyncMock
import pytest
from test_staff_auth import target as auth_target,run,row,change
from test_conversations import target as conversation_target,verified
from test_drafts import target as draft_target,actor,deadline
from test_anonymous_adoption import target as adoption_target,issue,verify
from test_case_commit import target as commit_target,prepared,confirm
from test_case_lifecycle import target as lifecycle_target,lifecycle,operator,login
from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.attachment_repository import PostgresAttachments
from airport_assistant.modules.attachments.service import AttachmentService,MAX_BYTES
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.auth_config import AuthSettings

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated Neon attachment tests.')
BODY=b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'


@pytest.fixture(scope='module')
def target(lifecycle_target):
    t=lifecycle_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0010_attachments')=='0010_attachments'
    return t


@asynccontextmanager
async def files(target,scanner=True):
    async with lifecycle(target) as (h,sender):
        scan=AsyncMock() if scanner else None
        if scan:scan.clean.return_value=True
        attachments=AttachmentService(PostgresAttachments(h.db,target['schema']),scan)
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),auth_settings=AuthSettings(SESSION_SECRET=target['key']),services=replace(h.app.state.services,attachments=attachments))
        yield h,sender,scan


async def upload(h,c,draft,*,id=None,body=BODY,mime='application/pdf',headers=None):
    return await c.post('/api/v1/attachments',params={'draft_id':draft['draft_id'],'expected_version':draft['version']},headers=headers if headers is not None else {**h.headers(c),'Idempotency-Key':str(id or uuid4()),'Content-Type':mime},content=body)


def test_upload_preview_reconfirm_atomic_case_binding_and_staff_access(target):
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c,h.client() as staff,h.client() as admin,h.client() as outsider,h.client('b.example.test') as other:
                draft=await prepared(h,sender,c)
                response=await upload(h,c,draft);assert response.status_code==201,response.text
                data=response.json();updated=data['draft'];id=data['attachment']['id']
                assert updated['version']==draft['version']+1 and updated['payload_hash']!=draft['payload_hash']
                assert updated['payload']['attachment_ids']==[id] and id in updated['summary']
                assert (await confirm(h,c,draft)).status_code==409
                download='/api/v1/attachments/'+id+'/download'
                got=await c.get(download);assert got.status_code==200 and got.content==BODY
                assert got.headers['content-disposition'].startswith('attachment;') and got.headers['x-content-type-options']=='nosniff'
                for stranger in (outsider,other):
                    await h.boot(stranger);r=await stranger.get(download);assert r.status_code==404,r.text
                result=await confirm(h,c,updated);assert result.status_code==201,result.text
                assert (await confirm(h,c,updated)).json()==result.json()
                case_id=result.json()['case_id']
                listed=await c.get('/api/v1/cases/'+case_id+'/attachments')
                assert listed.status_code==200 and listed.json()==[data['attachment']]
                assert str(row(target,'SELECT case_id FROM {s}.attachments WHERE id=:id',id=UUID(id))[0])==case_id
                for client,roles,expected in ((staff,('cs',),200),(admin,('admin',),404)):
                    person=operator(target,case_id,roles);await login(h,client,person)
                    r=await client.get(download);assert r.status_code==expected,r.text
                    listing=await client.get('/api/v1/cases/'+case_id+'/attachments')
                    assert listing.status_code==expected,listing.text
                    if expected==200:
                        change(target,'UPDATE {s}.staff_memberships SET active=false WHERE id=:id',id=person['case_membership'])
                        assert (await client.get(download)).status_code==404
                assert (await c.get(download)).content==BODY
    run(scenario())


def test_retry_binding_conflict_three_limit_and_concurrent_upload(target):
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c:
                draft=await prepared(h,sender,c);id=uuid4()
                first=await upload(h,c,draft,id=id);assert first.status_code==201,first.text
                retry=await upload(h,c,draft,id=id);assert retry.status_code==201 and retry.json()==first.json()
                assert scan.clean.await_count==1
                assert (await upload(h,c,draft,id=id,body=BODY+b'changed')).status_code==409
                draft=first.json()['draft']
                results=await asyncio.gather(upload(h,c,draft),upload(h,c,draft))
                assert sorted(r.status_code for r in results)==[201,409]
                draft=next(r.json()['draft'] for r in results if r.status_code==201)
                third=await upload(h,c,draft);assert third.status_code==201,third.text
                assert (await upload(h,c,third.json()['draft'])).json()['error']['code']=='ATTACHMENT_LIMIT'
                assert row(target,'SELECT count(*) FROM {s}.attachments WHERE draft_id=:id',id=UUID(draft['draft_id']))[0]==3
    run(scenario())


def test_scan_rejected_unavailable_and_cancel_during_scan_leave_no_blob(target):
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c:
                draft=await prepared(h,sender,c)
                scan.clean.return_value=False
                assert (await upload(h,c,draft)).json()['error']['code']=='ATTACHMENT_REJECTED'
                scan.clean.side_effect=DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
                assert (await upload(h,c,draft)).status_code==503
                async def cancel(body):
                    assert (await c.delete('/api/v1/action-drafts/'+draft['draft_id'],headers=h.headers(c))).status_code==200
                    return True
                scan.clean.side_effect=cancel
                result=await upload(h,c,draft);assert result.status_code==409,result.text
                assert row(target,'SELECT count(*) FROM {s}.attachments WHERE draft_id=:id',id=UUID(draft['draft_id']))[0]==0
    run(scenario())


def test_expired_cancelled_and_upgrade_pending_blob_access_denied(target):
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c:
                for terminal in ('expired','cancelled','upgrade'):
                    draft=await prepared(h,sender,c,anonymous=True)
                    result=await upload(h,c,draft);assert result.status_code==201,result.text
                    data=result.json();path='/api/v1/attachments/'+data['attachment']['id']+'/download'
                    if terminal=='expired':await deadline(target,h,c,draft['draft_id'])
                    elif terminal=='cancelled':assert (await c.delete('/api/v1/action-drafts/'+draft['draft_id'],headers=h.headers(c))).status_code==200
                    else:
                        challenge,code=await issue(h,sender,c,'file'+uuid4().hex+'@example.com')
                        assert (await verify(h,c,challenge,code)).status_code==200
                    assert (await c.get(path)).status_code==404
    run(scenario())


def test_removed_selection_not_bound_and_foreign_file_cannot_be_selected(target):
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c,h.client() as stranger:
                draft=await prepared(h,sender,c);added=await upload(h,c,draft);assert added.status_code==201,added.text
                draft=added.json()['draft'];id=added.json()['attachment']['id']
                foreign=await prepared(h,sender,stranger)
                bad={**foreign['payload'],'attachment_ids':[id]}
                denied=await stranger.patch('/api/v1/action-drafts/'+foreign['draft_id'],headers=h.headers(stranger),json={'expected_version':1,'payload':bad})
                assert denied.status_code==404,denied.text
                updated=await c.patch('/api/v1/action-drafts/'+draft['draft_id'],headers=h.headers(c),json={'expected_version':draft['version'],'payload':{**draft['payload'],'attachment_ids':[]}})
                assert updated.status_code==200,updated.text
                result=await confirm(h,c,updated.json());assert result.status_code==201,result.text
                assert row(target,'SELECT case_id FROM {s}.attachments WHERE id=:id',id=UUID(id))[0] is None
                assert (await c.get('/api/v1/attachments/'+id+'/download')).status_code==404
    run(scenario())


def test_transport_limit_type_csrf_and_missing_scanner(target):
    async def scenario():
        async with files(target,scanner=False) as (h,sender,scan):
            async with h.client() as c:
                draft=await prepared(h,sender,c)
                assert (await upload(h,c,draft)).json()['error']['code']=='ATTACHMENT_SCAN_UNAVAILABLE'
                assert (await upload(h,c,draft,body=b'x'*(MAX_BYTES+1))).status_code==413
                assert (await upload(h,c,draft,mime='application/zip')).status_code==415
                assert (await upload(h,c,draft,body=b'<html>')).status_code==415
                assert (await upload(h,c,draft,headers={'Content-Type':'application/pdf','Idempotency-Key':str(uuid4())})).status_code==403
                large=await c.post('/api/v1/action-drafts',headers=h.headers(c),content=b'x'*(1024*1024+1))
                assert large.status_code==413
    run(scenario())


def test_post_binding_fault_rolls_back_file_and_graph_then_retry_succeeds(target,monkeypatch):
    from airport_assistant.infrastructure.database.case_repository import CaseSQL
    original=CaseSQL.record_key
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c:
                draft=await prepared(h,sender,c);added=await upload(h,c,draft);assert added.status_code==201,added.text
                preview=added.json()['draft'];id=UUID(added.json()['attachment']['id']);key=str(uuid4())
                monkeypatch.setattr(CaseSQL,'record_key',AsyncMock(side_effect=RuntimeError('Injected test fault')))
                result=await confirm(h,c,preview,key);assert result.status_code==500
                assert row(target,'SELECT case_id FROM {s}.attachments WHERE id=:id',id=id)[0] is None
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]=='pending'
                assert row(target,'SELECT count(*) FROM {s}.cases WHERE origin_draft_id=:id',id=UUID(preview['draft_id']))[0]==0
                monkeypatch.setattr(CaseSQL,'record_key',original)
                result=await confirm(h,c,preview,key);assert result.status_code==201,result.text
    run(scenario())


def test_raw_runtime_cannot_mutate_body_or_bypass_parent_file_limit(target):
    import hashlib
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with files(target) as (h,sender,scan):
            async with h.client() as c:
                draft=await prepared(h,sender,c)
                for _ in range(3):
                    result=await upload(h,c,draft);assert result.status_code==201,result.text
                    draft=result.json()['draft']
                identity=await actor(h,c);repository=h.app.state.services.attachments._repository
                with pytest.raises(DatabaseError) as e:
                    async with repository.transaction(identity.database_context) as tx:
                        parent=await tx.find_draft(UUID(draft['draft_id']))
                        await tx.insert_attachment(uuid4(),parent,BODY,'application/pdf',hashlib.sha256(BODY).hexdigest())
                assert e.value.sqlstate=='23514'
                with pytest.raises(DatabaseError) as e:
                    async with repository.transaction(identity.database_context) as tx:
                        await tx._c.execute(tx.query('UPDATE {s}.attachments SET body=%s WHERE tenant_id=%s'),(b'altered',identity.session.tenant_id))
                assert e.value.sqlstate=='42501'
    run(scenario())
