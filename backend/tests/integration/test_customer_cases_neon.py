"""Actual owner-only case paging, private grants and ancestry on isolated Neon."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_case_translation_neon import target as translation_target, email_target, mail_target, graph_target, schema_target
from test_private_source_graph_neon import app, run, draft, staff
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.modules.cases.customer import CustomerCases, CustomerCaseCursor
from airport_assistant.modules.drafts.models import ReportPayload, DraftError
from airport_assistant.modules.identity.auth import AuthError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_CUSTOMER_CASE_TESTS')!='1',reason='Opt-in isolated customer case SQL.')


@pytest.fixture(scope='module')
def target(translation_target):
    t=translation_target
    import hashlib
    t.page_owner='reply_owner_'+hashlib.sha256(t.schema.encode()).hexdigest()[:24]
    with t.engine.connect() as c:
        assert not c.execute(text("SELECT has_schema_privilege(:role,:schema,'CREATE')"),dict(role=t.page_owner,schema=t.schema)).scalar_one()
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0034_customer_case_pages')=='0034_customer_case_pages'
    with t.engine.connect() as c:
        assert not c.execute(text("SELECT has_schema_privilege(:role,:schema,'CREATE')"),dict(role=t.page_owner,schema=t.schema)).scalar_one()
        assert not c.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"),dict(role=t.roles[3],fn=t.schema+'.customer_case_ancestry(uuid)')).scalar_one()
    return t


def test_actual_owner_pagination_private_denial_and_no_outside_transaction_source_io(target):
    async def scenario():
        async with app(target) as h:
            repository=PostgresCaseLifecycle(h.db,target.schema)
            service=CustomerCases(repository,CustomerCaseCursor(b'c'*32),h.access)
            created,_=await draft(target,h)
            private=(await h.cases.confirm(target.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())).case_id
            # A second public case gives a real multi-page graph; existing public
            # fixture remains intact and participates in the current owner list.
            conv=await h.conversations.create(target.identity,'en')
            report=ReportPayload(category='information',service_type='general',summary='Other information',customer_report='Another question.',contact_email='customer@example.com')
            other=await h.drafts.create(target.identity,conv.id,'create_case',report)
            other_id=(await h.cases.confirm(target.identity,other.id,other.version,other.payload_hash,uuid4().hex,uuid4())).case_id
            first=await service.page(target.identity,limit=1)
            ids=[v['id'] for v in first['items']];token=first['next_cursor'];watermark=first['watermark']
            while token:
                page=await service.page(target.identity,limit=1,cursor=token)
                assert page['watermark']==watermark
                ids.extend(v['id'] for v in page['items']);token=page['next_cursor']
            assert target.public_case in ids and private in ids and other_id in ids and len(ids)==len(set(ids))
            assert h.grants.calls>0
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError) as e:await service.page(target.identity,limit=100)
            assert e.value.status==404
            h.grants.denied.clear()
            # The stored owner predicate remains explicit even if a valid staff
            # session can see all queue cases under the underlying RLS.
            person=staff(target,private)
            with pytest.raises(AuthError):await service.page(person)
            async with repository.transaction(person.database_context) as tx:
                assert await tx.customer_case_page(None,watermark,None,100)==[]
                assert (await(await tx._c.execute(tx.query('SELECT {s}.customer_case_ancestry(%s)'),(private,))).fetchone())[0] is None
    run(scenario())


def test_revoked_participant_ancestry_is_not_treated_as_public(target):
    async def scenario():
        async with app(target) as h:
            service=CustomerCases(PostgresCaseLifecycle(h.db,target.schema),CustomerCaseCursor(b'c'*32),h.access)
            cid=target.public_case
            with target.engine.begin() as c:
                c.execute(text(f'UPDATE "{target.schema}".case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id AND principal_id=:p'),dict(id=cid,p=target.identity.session.principal_id))
            try:
                with pytest.raises(AuthError) as e:await service.page(target.identity,limit=100)
                assert e.value.status==404
            finally:
                with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".case_participants SET revoked_at=NULL WHERE case_id=:id AND principal_id=:p'),dict(id=cid,p=target.identity.session.principal_id))
    run(scenario())
