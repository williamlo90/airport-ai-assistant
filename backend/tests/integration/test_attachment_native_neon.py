"""Real local scanner plus isolated private SQL; synthetic evidence only."""
import hashlib
import os
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_customer_information_neon import target as information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target
from test_private_source_graph_neon import app,run,draft,staff,count
from test_amsi_live import pdf,accepted_header_detection_fixture
from test_staff_auth import insert
from airport_assistant.infrastructure.database.attachment_repository import PostgresAttachments,AttachmentSQL
from airport_assistant.infrastructure.security.amsi_scanner import AmsiScanner
from airport_assistant.modules.attachments.service import AttachmentService,validate_file
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.drafts.models import DraftError,require_integrity
from airport_assistant.modules.identity.auth import AuthError,Session
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.infrastructure.database.migrations import migrate

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_NATIVE_ATTACHMENT_TESTS')!='1' or os.name!='nt',reason='Opt-in actual local scanner/private Neon.')


@pytest.fixture(scope='module')
def target(information_target):
    t=information_target
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0036_private_attachments')=='0036_private_attachments'
    with t.engine.connect() as c:assert c.exec_driver_sql(f'SELECT "{t.schema}".private_attachments_ready()').scalar_one() is True
    return t


class TracedRepository(PostgresAttachments):
    def __init__(self,*args):super().__init__(*args);self.body_reads=0
    def unit(self,c,context):
        owner=self
        class TracedSQL(AttachmentSQL):
            async def attachment(self,id,*,body=False):
                if body:owner.body_reads+=1
                return await super().attachment(id,body=body)
        return TracedSQL(c,self._schema,context)


class Scanner(AmsiScanner):
    def __init__(self,db):super().__init__();self.calls=0;self.db=db;self.results=[]
    async def clean(self,body):
        assert self.db.active==0
        self.calls+=1
        result=await super().clean(body)
        self.results.append(result)
        return result


def other_customer(t,tenant):
    with t.engine.begin() as c:
        principal=insert(c,t.schema,'principals',tenant_id=tenant)
        email='isolated-'+uuid4().hex+'@example.com'
        insert(c,t.schema,'contacts',tenant_id=tenant,principal_id=principal,email_address=email,email_lookup_key=email,verified_at=datetime.now(timezone.utc))
        expires=datetime.now(timezone.utc)+timedelta(hours=1)
        session=insert(c,t.schema,'sessions',tenant_id=tenant,principal_id=principal,session_kind='customer',token_hash=uuid4().hex+uuid4().hex,csrf_secret_hash='b'*64,expires_at=expires)
    return AccessIdentity(Session(session,tenant,principal,'customer',expires,'b'*64))


def test_real_scanned_private_upload_review_confirm_download_isolation_and_revocation(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h);repository=TracedRepository(h.db,target.schema);scanner=Scanner(h.db)
            service=AttachmentService(repository,scanner,h.access);body=pdf();key=uuid4()
            before=count(target,'attachments')
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError):await service.upload(target.identity,created.id,created.version,key,body,'application/pdf')
            assert scanner.calls==0 and count(target,'attachments')==before
            h.grants.denied.clear()
            meta,updated=await service.upload(target.identity,created.id,created.version,key,body,'application/pdf')
            require_integrity(updated)
            assert updated.source_evidence==created.source_evidence and updated.payload_hash!=created.payload_hash
            assert updated.payload.attachment_ids==(meta['id'],) and scanner.results==[True]
            assert await service.upload(target.identity,created.id,created.version,key,body,'application/pdf')==(meta,updated)
            assert scanner.calls==1
            pending,_=await service.download(target.identity,meta['id'])
            assert hashlib.sha256(pending).digest()==hashlib.sha256(body).digest()
            receipt=await h.cases.confirm(target.identity,updated.id,updated.version,updated.payload_hash,uuid4().hex,uuid4())
            listed=await service.list_case(target.identity,receipt.case_id)
            assert listed==[meta]
            downloaded,mime=await service.download(target.identity,meta['id'])
            assert mime=='application/pdf' and hashlib.sha256(downloaded).digest()==hashlib.sha256(body).digest()
            reads=repository.body_reads
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError):await service.download(target.identity,meta['id'])
            with pytest.raises(AuthError):await service.list_case(target.identity,receipt.case_id)
            assert repository.body_reads==reads
            for actor in (other_customer(target,target.a),other_customer(target,target.b)):
                with pytest.raises(AuthError) as e:await service.download(actor,meta['id'])
                assert e.value.status==404
            person=staff(target,receipt.case_id)
            # Staff operational rights do not substitute that staff principal
            # into the customer's source-grant identity.
            assert (await service.list_case(person,receipt.case_id))==[meta]
            assert (await service.download(person,meta['id']))[1]=='application/pdf'
            with target.engine.begin() as c:
                c.execute(text(f'DELETE FROM "{target.schema}".membership_queues WHERE membership_id=:id'),dict(id=person.memberships[0].id))
            with pytest.raises(AuthError):await service.download(person,meta['id'])
    run(scenario())


def test_real_detection_reaches_scanner_after_mime_and_persists_nothing(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h);scanner=Scanner(h.db)
            service=AttachmentService(PostgresAttachments(h.db,target.schema),scanner,h.access)
            body=accepted_header_detection_fixture();validate_file(body,'application/pdf')
            baseline=count(target,'attachments')
            with pytest.raises(DraftError) as e:await service.upload(target.identity,created.id,created.version,uuid4(),body,'application/pdf')
            assert e.value.status==422 and e.value.code=='ATTACHMENT_REJECTED'
            assert scanner.calls==1 and scanner.results==[False]
            assert count(target,'attachments')==baseline
            current=await h.drafts.get(target.identity,created.id)
            assert current.payload_hash==created.payload_hash and current.version==created.version and current.payload.attachment_ids==()
    run(scenario())
