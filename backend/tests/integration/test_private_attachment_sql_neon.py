"""Private-file SQL negatives; controlled scanner, isolated schema only."""
import os
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from sqlalchemy import text

from test_attachment_native_neon import target,information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target
from test_private_source_graph_neon import app,run,draft,count
from airport_assistant.infrastructure.database.attachment_repository import PostgresAttachments,AttachmentSQL
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.attachments.service import AttachmentService
from airport_assistant.modules.drafts.models import seal,reviewed_summary
from airport_assistant.modules.identity.auth import AuthError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_PRIVATE_ATTACHMENT_TESTS')!='1',reason='Opt-in isolated private attachment SQL.')
BODY=b'%PDF-1.7\nControlled SQL evidence.'


def test_sibling_selection_and_post_write_fault_rollback_then_explicit_retry(target):
    seen=[]
    class FaultSQL(AttachmentSQL):
        async def edit_draft(self,value):
            saved=await super().edit_draft(value)
            seen.append(saved.version)
            raise RuntimeError('Controlled post-write rollback')
    class FaultRepo(PostgresAttachments):
        def unit(self,c,context):return FaultSQL(c,self._schema,context)
    async def scenario():
        async with app(target) as h:
            first,_=await draft(target,h);scanner=SimpleNamespace(clean=AsyncMock(return_value=True))
            files=AttachmentService(PostgresAttachments(h.db,target.schema),scanner,h.access)
            meta,_=await files.upload(target.identity,first.id,first.version,uuid4(),BODY,'application/pdf')
            second,_=await draft(target,h)
            payload=second.payload.model_copy(update={'attachment_ids':(meta['id'],)})
            with pytest.raises(AuthError):await h.drafts.edit(target.identity,second.id,second.version,payload)
            proposed=seal(replace(second,payload=payload,version=second.version+1,displayed_summary=reviewed_summary(
                second.action_type,payload,second.destination,second.language,source_evidence=second.source_evidence)))
            changed=False
            with pytest.raises(DatabaseError) as e:
                async with PostgresAttachments(h.db,target.schema).transaction(target.identity.database_context) as tx:
                    await tx.edit_draft(proposed);changed=True
            assert e.value.sqlstate=='23514' and not changed
            key=uuid4();baseline=count(target,'attachments')
            fault=AttachmentService(FaultRepo(h.db,target.schema),scanner,h.access)
            with pytest.raises(RuntimeError):await fault.upload(target.identity,second.id,second.version,key,BODY,'application/pdf')
            assert seen==[second.version+1] and count(target,'attachments')==baseline
            assert (await h.drafts.get(target.identity,second.id)).payload_hash==second.payload_hash
            saved,updated=await files.upload(target.identity,second.id,second.version,key,BODY,'application/pdf')
            assert count(target,'attachments')==baseline+1
            assert await files.upload(target.identity,second.id,second.version,key,BODY,'application/pdf')==(saved,updated)
    run(scenario())


@pytest.mark.parametrize('extra',[False,True])
def test_direct_sql_cannot_persist_downgraded_or_extra_classification(target,extra):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h);q='"'+target.schema+'"';role='"'+target.roles[0]+'"'
            labels=[dict(source='reference',resource_type='booking',reference='FOREIGN',required_source_scopes=['booking:read'])] if extra else []
            baseline=count(target,'attachments');mid=uuid4();inserted=False
            # Isolated-role-only temporary privilege lets the graph guard itself
            # be tested; application admission correctly reports false meanwhile.
            with target.engine.begin() as c:c.exec_driver_sql(f'GRANT INSERT(resource_security_labels) ON {q}.attachments TO {role}')
            async def attempt():
                nonlocal inserted
                async with PostgresAttachments(h.db,target.schema).transaction(target.identity.database_context) as tx:
                    await tx._c.execute(tx.query('''INSERT INTO {s}.attachments(tenant_id,id,draft_id,uploader_principal_id,
                        created_session_id,body,mime_type,byte_size,content_hash,scan_status,resource_security_labels)
                        VALUES(%s,%s,%s,%s,%s,%s,'application/pdf',%s,%s,'clean',%s)'''),
                        (target.a,mid,created.id,target.principal,target.identity.session.id,BODY,len(BODY),hashlib.sha256(BODY).hexdigest(),Jsonb(labels)))
                    inserted=True
                    stored=await tx.attachment(mid)
                    assert stored['resource_security_labels']==list(created.resource_security_labels)
            try:
                if extra:
                    # The insertion guard rejects requirements outside the
                    # actual draft after inheritance, before INSERT succeeds.
                    with pytest.raises(DatabaseError) as e:await attempt()
                    assert e.value.sqlstate=='23514' and not inserted and count(target,'attachments')==baseline
                else:
                    await attempt()
                    assert inserted and count(target,'attachments')==baseline+1
            finally:
                with target.engine.begin() as c:c.exec_driver_sql(f'REVOKE INSERT(resource_security_labels) ON {q}.attachments FROM {role}')
            with target.engine.connect() as c:assert c.exec_driver_sql(f'SELECT {q}.private_attachments_ready()').scalar_one() is True
    run(scenario())


@pytest.mark.parametrize('tamper',['inherit','selection','binding','worker_column','policy','canonical'])
def test_admission_detects_dependency_or_privilege_drift_then_restores(target,tamper):
    q='"'+target.schema+'"';role='"'+target.roles[0]+'"'
    with target.engine.connect() as c:
        assert c.exec_driver_sql(f'SELECT {q}.private_attachments_ready()').scalar_one() is True
        c.rollback();transaction=c.begin()
        try:
            if tamper in {'inherit','selection','binding'}:
                table,trigger={'inherit':('attachments','a_private_source_inherit'),'selection':('action_drafts','draft_attachment_selection'),
                    'binding':('action_drafts','case_attachment_binding')}[tamper]
                c.exec_driver_sql(f'ALTER TABLE {q}.{table} DISABLE TRIGGER {trigger}')
            elif tamper=='worker_column':c.exec_driver_sql(f'GRANT SELECT(body) ON {q}.attachments TO "{target.roles[3]}"')
            elif tamper=='canonical':c.exec_driver_sql(f'CREATE OR REPLACE FUNCTION {q}.source_labels_valid(value jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT true $$')
            else:
                condition=c.exec_driver_sql("SELECT pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid=%s::regclass AND polname='runtime_insert'",(target.schema+'.attachments',)).scalar_one()
                c.exec_driver_sql(f'DROP POLICY runtime_insert ON {q}.attachments')
                c.exec_driver_sql(f'CREATE POLICY runtime_insert ON {q}.attachments AS RESTRICTIVE FOR INSERT TO {role} WITH CHECK({condition})')
            assert c.exec_driver_sql(f'SELECT {q}.private_attachments_ready()').scalar_one() is False
        finally:transaction.rollback()
        assert c.exec_driver_sql(f'SELECT {q}.private_attachments_ready()').scalar_one() is True
