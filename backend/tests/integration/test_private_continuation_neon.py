"""Current source authority is required for continuation and historical replay."""
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_customer_information_neon import target,page_target,translation_target,email_target,mail_target,graph_target,schema_target
from test_private_source_graph_neon import app,run,draft,human,staff,count
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,ContinueCase,StatusChange,Assignment
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_PRIVATE_CONTINUATION_TESTS')!='1',reason='Opt-in isolated continuation SQL.')


async def closed_case(t,h):
    created,_=await draft(t,h)
    cid=(await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())).case_id
    person=staff(t,cid);lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,t.schema),h.access)
    view=await lifecycle.get(person,cid,staff=True)
    view=await lifecycle.assign(person,cid,Assignment(expected_version=view['version'],assignee_id=person.memberships[0].id))
    view=await lifecycle.status(person,cid,StatusChange(expected_version=view['version'],status='closed',next_action='Finished',close_reason='resolved',resolution='Initial issue reviewed'))
    return cid,person,lifecycle,view


def test_private_continue_and_replay_deny_revoked_grants_without_mutation(target):
    async def scenario():
        async with app(target) as h:
            cid,person,lifecycle,before=await closed_case(target,h)
            command=ContinueCase(expected_version=before['version'],intent='same_issue',message='The same issue needs follow-up.',client_request_id=uuid4())
            baseline=count(target,'case_history');h.grants.denied={'booking:read'}
            with pytest.raises(AuthError) as e:await lifecycle.continue_case(target.identity,cid,command)
            assert e.value.status==404 and count(target,'case_history')==baseline
            assert (await lifecycle.get(person,cid,staff=True))['version']==before['version']
            h.grants.denied.clear()
            saved=await lifecycle.continue_case(target.identity,cid,command)
            assert saved['version']==before['version']+1 and saved['status']=='in_progress'
            assert await lifecycle.continue_case(target.identity,cid,command)==saved
            assert count(target,'case_history')==baseline+1
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError):await lifecycle.continue_case(target.identity,cid,command)
            assert count(target,'case_history')==baseline+1
    run(scenario())


@pytest.mark.parametrize('revoked',['participant','session'])
def test_current_authority_revoked_between_grant_check_and_write_rolls_back(target,revoked):
    async def scenario():
        async with app(target) as h:
            cid,person,lifecycle,before=await closed_case(target,h)
            command=ContinueCase(expected_version=before['version'],intent='same_issue',message='Still need help.',client_request_id=uuid4())
            table='case_participants' if revoked=='participant' else 'sessions'
            where='case_id=:id AND principal_id=:principal' if revoked=='participant' else 'id=:session'
            params=dict(id=cid,principal=target.principal,session=target.identity.session.id)
            baseline=count(target,'case_history')
            def revoke():
                with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".{table} SET revoked_at=clock_timestamp() WHERE '+where),params)
            h.grants.hook=revoke
            try:
                with pytest.raises(AuthError) as e:await lifecycle.continue_case(target.identity,cid,command)
                assert e.value.status==(404 if revoked=='participant' else 401)
            finally:
                with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".{table} SET revoked_at=NULL WHERE '+where),params)
            assert count(target,'case_history')==baseline
            assert (await lifecycle.get(person,cid,staff=True))['version']==before['version']
    run(scenario())


def test_late_private_commit_is_unknown_then_get_recovers_and_ended_chat_stays_ended(target):
    async def scenario():
        async with app(target) as h:
            conv,cid,person,chat=await human(target,h)
            normal=CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access)
            closed=await normal.status(person,cid,StatusChange(expected_version=chat['case_version'],expected_conversation_version=chat['version'],status='closed',next_action='Finished',close_reason='resolved',resolution='Initial issue reviewed'))
            clock=[0];committed=[False]
            class MarkSQL(LifecycleSQL):
                async def history(self,*args,**kwargs):
                    result=await super().history(*args,**kwargs)
                    if args[2]=='reopened':committed[0]=True
                    return result
            class SlowExit(PostgresCaseLifecycle):
                def unit(self,c,context):return MarkSQL(c,self._schema,context)
                @asynccontextmanager
                async def transaction(self,context):
                    async with super().transaction(context) as tx:yield tx
                    if committed[0]:clock[0]=2.01
            lifecycle=CaseLifecycle(SlowExit(h.db,target.schema),h.access)
            lifecycle._private_content.clock=lambda:clock[0]
            command=ContinueCase(expected_version=closed['version'],intent='same_issue',message='Explicit same issue continuation.',client_request_id=uuid4())
            baseline=count(target,'case_history')
            with pytest.raises(SourceFailure):await lifecycle.continue_case(target.identity,cid,command)
            recovered=await normal.get(target.identity,cid)
            assert recovered['version']==closed['version']+1 and count(target,'case_history')==baseline+1
            assert await normal.continue_case(target.identity,cid,command)==recovered
            with target.engine.connect() as c:
                assert c.execute(text(f'SELECT mode FROM "{target.schema}".conversations WHERE id=:id'),dict(id=conv)).scalar_one()=='ended'
    run(scenario())
