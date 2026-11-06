"""AI-01 real conversation/draft authorization and fences; no live model/provider."""
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from test_knowledge_vectors_neon import target, knowledge_target, auth_target
from test_staff_auth import harness, seed, run, row, change
from test_knowledge_neon import identity
from airport_assistant.modules.conversations.service import ConversationService
from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.infrastructure.database.draft_repository import PostgresDrafts
from airport_assistant.modules.agent.service import AgentFoundation
from airport_assistant.modules.agent.models import Decision,DraftProposal,Clarification,AgentError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated AI-01 conversation/draft Neon tests.')


class Planner:
    def __init__(self):self.calls=[];self.hook=None
    async def decide(self,report,language,tools,*,draft=False):
        self.calls.append((report,language,tools,draft))
        if self.hook:await self.hook()
        return (Decision(kind='draft',proposal=DraftProposal(category='booking_issue',service_type='parking',summary='Please investigate parking'))
            if draft else Decision(kind='clarify',clarification=Clarification(question='What happened?')))


def services(h,target):
    conversations=ConversationService(PostgresConversations(h.db,target['schema']))
    drafts=DraftService(PostgresDrafts(h.db,target['schema']))
    planner=Planner()
    return AgentFoundation(planner,conversations,drafts),conversations,drafts,planner


def counts(target):
    return row(target,'SELECT (SELECT count(*) FROM {s}.cases),(SELECT count(*) FROM {s}.outbox_jobs)')


def test_owned_persisted_report_creates_only_pending_draft_without_case_or_job(target):
    seed(target,roles=('admin',))  # Active parking support destination; no model-chosen queue.
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0,max_size=2)
            foundation,conversations,drafts,planner=services(h,target)
            async with h.client() as c,h.client() as outsider,h.client('b.example.test') as b:
                await h.boot(c);await h.boot(outsider);await h.boot(b)
                actor=await identity(h,c);wrong=await identity(h,outsider)
                from airport_assistant.api.security import SESSION_COOKIE
                foreign=await h.app.state.services.access.resolve(target['b'],b.cookies.get(SESSION_COOKIE))
                conv=await conversations.create(actor,'en')
                message=await conversations.append(actor,conv.id,uuid4(),'My parking booking PK-A-001 needs help.')
                scope=await foundation.scope(actor,conv.id,message.id,booking_references=('PK-A-001',))
                for bad in (wrong,foreign):
                    with pytest.raises(AuthError):await foundation.scope(bad,conv.id,message.id)
                another=await conversations.create(actor,'en')
                with pytest.raises(AuthError):await foundation.scope(actor,another.id,message.id)
                baseline=counts(target)
                result=await foundation.prepare_draft(scope,contact_email='user@example.com')
                assert result.state=='pending' and result.version==1 and result.payload.customer_report==message.body
                assert result.payload.booking_reference=='PK-A-001' and result.destination.service_type=='parking'
                assert not result.resource_security_labels
                assert (await drafts.get(actor,result.id)).state=='pending'
                assert counts(target)==baseline
                assert planner.calls==[(message.body,'en',(),True)]
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=result.id)[0]=='pending'
    run(scenario())


def test_atomic_draft_fence_rejects_new_message_between_post_model_check_and_insert(target):
    seed(target,roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0,max_size=2)
            foundation,conversations,drafts,planner=services(h,target)
            async with h.client() as c:
                await h.boot(c);actor=await identity(h,c)
                conv=await conversations.create(actor,'en');message=await conversations.append(actor,conv.id,uuid4(),'Parking issue')
                scope=await foundation.scope(actor,conv.id,message.id)
                async def raced_create(*args,**kwargs):
                    await conversations.append(actor,conv.id,uuid4(),'Correction: this is a different request.')
                    return await drafts.create(*args,**kwargs)
                foundation.drafts=SimpleNamespace(create=raced_create)
                before=row(target,'SELECT count(*) FROM {s}.action_drafts')[0]
                with pytest.raises(DraftError) as error:await foundation.prepare_draft(scope)
                assert error.value.code=='VERSION_CONFLICT'
                assert row(target,'SELECT count(*) FROM {s}.action_drafts')[0]==before
    run(scenario())


def test_session_revoke_and_report_correction_while_model_in_flight_block_persistence(target):
    seed(target,roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0,max_size=2)
            foundation,conversations,drafts,planner=services(h,target)
            async with h.client() as c:
                await h.boot(c);actor=await identity(h,c)
                conv=await conversations.create(actor,'en');message=await conversations.append(actor,conv.id,uuid4(),'Parking help')
                scope=await foundation.scope(actor,conv.id,message.id)
                before=row(target,'SELECT count(*) FROM {s}.action_drafts')[0]
                async def correction():
                    planner.hook=None
                    await conversations.append(actor,conv.id,uuid4(),'Changed my mind')
                planner.hook=correction
                with pytest.raises(AgentError):await foundation.prepare_draft(scope)
                latest=await conversations.append(actor,conv.id,uuid4(),'Please continue with my corrected parking request.')
                fresh=await foundation.scope(actor,conv.id,latest.id)
                async def revoke():
                    planner.hook=None
                    change(target,'UPDATE {s}.sessions SET revoked_at=clock_timestamp() WHERE id=:id',id=actor.session.id)
                planner.hook=revoke
                with pytest.raises(AuthError):await foundation.prepare_draft(fresh)
                assert row(target,'SELECT count(*) FROM {s}.action_drafts')[0]==before
    run(scenario())
