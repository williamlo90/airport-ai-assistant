"""Private committed-but-unavailable recovery and real anonymous auth adoption."""
import os
from uuid import uuid4
import pytest
from sqlalchemy import text
from test_administration_ratings_neon import target, rating_target, information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target,customer
from test_private_source_graph_neon import app,run,evidence,payload,staff,count
from airport_assistant.infrastructure.database.ratings import PostgresRatings
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.modules.cases.ratings import CustomerRatings,RatingInput
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,StatusChange
from airport_assistant.modules.conversations.publication import AIPublication
from airport_assistant.modules.identity.auth import AuthService,AuthError
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.source.models import SourceFailure

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_RATING_RECOVERY_TESTS')!='1',reason='Opt-in isolated feedback recovery/adoption.')


async def private_case(t,h,recoveries):
    # Fixture observes only the opaque ID of its own single creation attempt;
    # no replay, source bypass, altered clocks or deadline widening.
    conversation=await h.conversations.create(t.identity,'en')
    await h.conversations.append(t.identity,conversation.id,uuid4(),'Please check the existing parking transaction.')
    observed,*_=evidence(t.a)
    try:
        created=await h.drafts.create(t.identity,conversation.id,'create_case',payload(),source_evidence=observed)
    except SourceFailure:
        with t.engine.connect() as c:
            ids=c.execute(text(f'''SELECT id FROM "{t.schema}".action_drafts WHERE tenant_id=:tenant
              AND conversation_id=:conversation AND actor_principal_id=:principal AND created_session_id=:session'''),
              dict(tenant=t.a,conversation=conversation.id,principal=t.identity.session.principal_id,session=t.identity.session.id)).scalars().all()
        assert len(ids)==1,'Creation was not committed; fixture cannot proceed'
        created=await h.drafts.get(t.identity,ids[0]);recoveries.append('draft-current-authorized-get')
    try:
        cid=(await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())).case_id
    except SourceFailure:
        receipt=await h.cases.status(t.identity,created.id)
        assert receipt['state']=='committed','Confirmation was not committed; do not infer success'
        cid=receipt['case_id'];recoveries.append('case-current-authorized-receipt-get')
    person=staff(t,cid);lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,t.schema),h.access)
    before=await lifecycle.get(person,cid,staff=True)
    await lifecycle.status(person,cid,StatusChange(expected_version=before['version'],status='closed',next_action='No further action pending.',
        close_reason='resolved',resolution='The parking procedure was explained.'))
    return cid


def test_private_feedback_actual_labels_denial_foreign_sessions_and_get_recovery(target):
    async def scenario():
        async with app(target) as h:
            recoveries=[];cid=await private_case(target,h,recoveries)
            ratings=CustomerRatings(PostgresRatings(h.db,target.schema),h.access);before=count(target,'rating_events')
            try:saved=await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=5,comment='Original private feedback.'))
            except SourceFailure:
                saved=await ratings.get(target.identity,'case',cid);recoveries.append('rating-current-authorized-get')
            assert saved['rating'] is not None and saved['rating']['version']==1
            assert(saved['rating']['score'],saved['rating']['comment'])==(5,'Original private feedback.')
            assert count(target,'rating_events')==before+1
            for stranger in (customer(target),customer(target,target.b)):
                with pytest.raises(AuthError) as error:await ratings.get(stranger,'case',cid)
                assert error.value.status==404
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError):await ratings.get(target.identity,'case',cid)
            with pytest.raises(AuthError):await ratings.save(target.identity,'case',cid,RatingInput(expected_version=1,score=1))
            h.grants.denied.clear();assert await ratings.get(target.identity,'case',cid)==saved
            with target.engine.connect() as c:
                labels=c.execute(text(f'SELECT resource_security_labels FROM "{target.schema}".ratings WHERE case_id=:id'),dict(id=cid)).scalar_one()
                assert labels
            assert count(target,'rating_events')==before+1
            print('Feedback recovery metadata:',dict(recovery_reads=recoveries,source_denial=True,foreign_sessions=2,private_labels=True))
    run(scenario())


def test_actual_otp_adoption_keeps_single_rating_original_session_and_current_owner_scope(target):
    class ControlledSender:
        def __init__(self):self.messages={}
        async def send(self,message):self.messages[message.challenge_id]=message
    async def scenario():
        async with app(target) as h:
            sender=ControlledSender();auth=AuthService(PostgresAuthRepository(h.db,target.schema),b'controlled-auth-key-for-feedback1',sender=sender)
            initial=await auth.bootstrap(target.a,None,'127.0.0.63');owner=AccessIdentity(initial.session)
            conversation=await h.conversations.create(owner,'en')
            await h.conversations.append(owner,conversation.id,uuid4(),'Please help with airport facilities.')
            publication=AIPublication(PostgresPublication(h.db,target.schema))
            ticket=await publication.capture(owner,conversation.id)
            assert(await publication.publish(owner,ticket,'Controlled published airport help.'))['state']=='published'
            ratings=CustomerRatings(PostgresRatings(h.db,target.schema),h.access)
            first=await ratings.save(owner,'conversation',conversation.id,RatingInput(expected_version=0,score=3,comment='Initial feedback.'))
            challenge=await auth.request_challenge(target.a,initial.token,initial.csrf,'rating-adoption-'+uuid4().hex+'@example.com','127.0.0.63')
            verified=await auth.verify_challenge(target.a,challenge,initial.token,initial.csrf,sender.messages[challenge].code,'127.0.0.63')
            current=AccessIdentity(verified.session)
            read=await ratings.get(current,'conversation',conversation.id)
            assert read['rating']['id']==first['rating']['id'] and read['rating']['version']==1
            with pytest.raises(AuthError):await ratings.get(owner,'conversation',conversation.id)
            changed=await ratings.save(current,'conversation',conversation.id,RatingInput(expected_version=1,score=4))
            assert changed['rating']['id']==first['rating']['id'] and changed['rating']['version']==2
            with target.engine.connect() as c:
                rows=c.execute(text(f'SELECT created_session_id,updated_session_id FROM "{target.schema}".ratings WHERE conversation_id=:id'),dict(id=conversation.id)).all()
                assert rows==[(initial.session.id,verified.session.id)]
                assert c.execute(text(f'SELECT count(*) FROM "{target.schema}".rating_events WHERE rating_id=:id'),dict(id=first['rating']['id'])).scalar_one()==2
            assert len(sender.messages)==1 # Controlled in-memory OTP, no provider/email call.
    run(scenario())
