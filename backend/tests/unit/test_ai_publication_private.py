"""Standalone customer capability contracts; not SQL/RLS activation proof."""
import asyncio,copy,hashlib
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest

from airport_assistant.modules.conversations.ai_publication import PrivateAIPublication,PublicationInput,RESTORED_TEMPLATES,text_hash
from airport_assistant.modules.conversations.publication import GenerationTicket
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from test_staff_disclosure import labels


class Store:
    def __init__(self,identity,ticket):
        self.identity=identity;self.ticket=ticket;self.active=True;self.in_tx=False;self.clock=0.
        self.time=datetime.now(timezone.utc);self.g=None;self.graph=None;self.corrupt=None;self.late=None
        self.conv=dict(id=uuid4(),tenant_id=identity.session.tenant_id,customer_principal_id=identity.session.principal_id,
            anonymous_session_id=None,case_id=None,language='en',mode='ai',version=2,generation_epoch=3,
            last_sequence=4,last_event_sequence=5,resource_security_labels=list(labels()))
        self.run=dict(id=ticket.id,tenant_id=identity.session.tenant_id,session_id=identity.session.id,
            principal_id=identity.session.principal_id,conversation_id=self.conv['id'],phase='waiting_user',revision=1)
    @asynccontextmanager
    async def transaction(self,context):
        assert not self.in_tx;self.in_tx=True
        before=copy.deepcopy((self.conv,self.g,self.graph))
        try:yield self
        except BaseException:self.conv,self.g,self.graph=before;raise
        finally:self.in_tx=False
    async def session_active(self):return self.active
    @asynccontextmanager
    async def savepoint(self):
        before=copy.deepcopy((self.conv,self.g,self.graph))
        try:yield
        except BaseException:self.conv,self.g,self.graph=before;raise
    async def now(self):return self.time
    async def ai_generation_context(self,id):return copy.deepcopy(self.g) if id==self.ticket.id else None
    async def ai_publication_context(self,cid,gid):
        if cid!=self.conv['id']:return None
        return copy.deepcopy(dict(conversation=self.conv,generation=self.g,parent=None,run=self.run,run_labels=list(labels())))
    async def ai_publication_result(self,id):
        result=copy.deepcopy(self.graph)
        if self.late:self.late()
        return result
    async def ai_create_generation(self,ticket,conv,requirements):
        self.conv['resource_security_labels']=requirements
        self.g=dict(id=ticket.id,tenant_id=self.identity.session.tenant_id,conversation_id=conv['id'],
            origin_session_id=self.identity.session.id,principal_id=self.identity.session.principal_id,
            input_version=conv['version'],generation_epoch=conv['generation_epoch'],language=conv['language'],
            nonce_hash=text_hash(ticket.nonce),state='pending',message_id=None,expires_at=self.time+timedelta(minutes=5),
            resource_security_labels=requirements)
    async def ai_stale_generation(self,id):self.g['state']='stale'
    async def ai_write_publication(self,id,body,requirements,*,knowledge_evidence=None):
        self.conv.update(version=self.conv['version']+1,last_sequence=self.conv['last_sequence']+1,
            last_event_sequence=self.conv['last_event_sequence']+1,resource_security_labels=requirements)
        mid=uuid4();self.g.update(state='published',message_id=mid,resource_security_labels=requirements)
        message=dict(id=mid,tenant_id=self.identity.session.tenant_id,conversation_id=self.conv['id'],generation_id=id,
            created_session_id=self.identity.session.id,author_type='ai',source_kind='ai_response',body=body,language='en',
            sequence=self.conv['last_sequence'],basis_conversation_version=2,basis_epoch=3,resource_security_labels=requirements,
            knowledge_evidence=knowledge_evidence)
        event=dict(id=uuid4(),tenant_id=self.identity.session.tenant_id,conversation_id=self.conv['id'],message_id=mid,
            language='en',event_type='ai_message',sequence=self.conv['last_event_sequence'])
        if self.corrupt=='message':message['body']='Changed'
        if self.corrupt=='event':event['message_id']=uuid4()
        if self.corrupt=='generation':self.g['message_id']=uuid4()
        if self.corrupt=='counter':self.conv['last_sequence']+=1
        self.graph=dict(generation=copy.deepcopy(self.g),message=message,event=event)
        return dict(id=mid,sequence=message['sequence'])
    async def ai_citation_context(self,cid,mid):
        if self.graph is None or self.graph['message']['id']!=mid:return None
        return dict(context=await self.ai_publication_context(cid,self.ticket.id),message=copy.deepcopy(self.graph['message']),
            generation_labels=copy.deepcopy(self.g['resource_security_labels']),generation_id=self.ticket.id)


def setup():
    identity=SimpleNamespace(session=SimpleNamespace(kind='customer',tenant_id=uuid4(),principal_id=uuid4(),id=uuid4()),database_context=None)
    ticket=GenerationTicket(uuid4(),'internal-nonce')
    store=Store(identity,ticket)
    async def grants(**kw):assert not store.in_tx;return True
    access=SimpleNamespace(allows=AsyncMock(side_effect=grants))
    service=PrivateAIPublication(store,access,clock=lambda:store.clock)
    req=SecurityLabels.from_records(identity.session.tenant_id,list(labels()))
    body=RESTORED_TEMPLATES['en']
    proof=PublicationInput(identity.session.tenant_id,identity.session.id,identity.session.principal_id,ticket.id,
        store.conv['id'],2,3,'en',text_hash(body),req,0.,'generic_restore',None,1)
    return identity,ticket,store,access,service,body,proof


async def captured():
    args=setup();identity,ticket,store,access,service,body,proof=args
    await service.capture_bound(identity,store.conv['id'],ticket,2,3,requirements=proof.requirements)
    return args


def test_private_capture_publish_exact_graph_and_historical_receipt_no_text_replay():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        result=await service.publish(identity,ticket,body,private_input=proof)
        assert result['message_id']==store.graph['message']['id'] and 'body' not in result
        store.conv.update(mode='human',version=20,generation_epoch=30)
        assert await service.receipt(identity,ticket)==result
        assert await service.receipt(identity,ticket,discard=True)==result
        access.allows.side_effect=None;access.allows.return_value=False
        with pytest.raises(AuthError):await service.receipt(identity,ticket)
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,None,'true',SourceFailure(503)])
def test_source_grants_cannot_be_bypassed_by_capture_or_replay(verdict):
    async def run():
        identity,ticket,store,access,service,body,proof=setup()
        async def denied(**kw):
            assert not store.in_tx
            if isinstance(verdict,Exception):raise verdict
            return verdict
        access.allows.side_effect=denied
        with pytest.raises((AuthError,SourceFailure)):await service.capture_bound(identity,store.conv['id'],ticket,2,3)
        assert store.g is None and store.graph is None
    asyncio.run(run())


@pytest.mark.parametrize('part',['message','event','generation','counter'])
def test_exact_graph_corruption_rolls_back_whole_publication(part):
    async def run():
        identity,ticket,store,access,service,body,proof=await captured();store.corrupt=part
        with pytest.raises(DraftError):await service.publish(identity,ticket,body,private_input=proof)
        assert store.g['state']=='pending' and store.graph is None and store.conv['version']==2
    asyncio.run(run())


def test_final_graph_read_changes_owner_without_clock_movement_is_denied():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        store.late=lambda:store.conv.update(customer_principal_id=uuid4())
        with pytest.raises((AuthError,DraftError)):await service.publish(identity,ticket,body,private_input=proof)
        assert store.g['state']=='pending' and store.graph is None
    asyncio.run(run())


def test_last_await_expiry_aborts_inside_transaction():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        store.late=lambda:setattr(store,'clock',2.25)
        with pytest.raises(SourceFailure):await service.publish(identity,ticket,body,private_input=proof)
        assert store.g['state']=='pending' and store.graph is None
    asyncio.run(run())


def test_discard_pending_requires_grants_and_verifies_stale_record():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        assert (await service.receipt(identity,ticket,discard=True))['state']=='discarded'
        assert store.g['state']=='stale' and store.graph is None
    asyncio.run(run())


def test_private_citations_and_empty_citations_require_current_whole_grants():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        result=await service.publish(identity,ticket,body,private_input=proof)
        citation=await service.citations(identity,store.conv['id'],result['message_id'])
        assert citation['citations']==[] and citation['status']=='historical_publication'
        access.allows.side_effect=None;access.allows.return_value=False
        with pytest.raises(AuthError):await service.citations(identity,store.conv['id'],result['message_id'])
    asyncio.run(run())


def test_outer_publication_dispatch_is_explicit_and_default_stays_off():
    async def run():
        from airport_assistant.modules.conversations.publication import AIPublication
        identity,ticket,store,access,capability,body,proof=setup()
        outer=AIPublication(store,private_capability=capability)
        await outer.capture_bound(identity,store.conv['id'],ticket,2,3,requirements=proof.requirements)
        saved=await outer.publish(identity,ticket,body,private_input=proof)
        assert await outer.receipt(identity,ticket)==saved
        assert AIPublication(store).private_capability is None
        with pytest.raises(DraftError):await AIPublication(store).publish(identity,ticket,body,private_input=proof)
    asyncio.run(run())


@pytest.mark.parametrize('private',[False,True])
def test_sql_ai_message_and_generation_private_parameters_preserve_public_columns(private):
    async def run():
        from airport_assistant.infrastructure.database.publication import PublicationSQL
        from airport_assistant.modules.conversations.service import Conversation
        identity,ticket,store,access,service,body,proof=setup()
        cursor=SimpleNamespace(fetchone=AsyncMock(side_effect=[(5,),(uuid4(),5)]))
        connection=SimpleNamespace(execute=AsyncMock(return_value=cursor))
        context=SimpleNamespace(tenant_id=identity.session.tenant_id,principal_id=identity.session.principal_id,session_id=identity.session.id)
        tx=PublicationSQL(connection,'app',context)
        conv=Conversation(store.conv['id'],identity.session.tenant_id,identity.session.principal_id,None,'en',2,3,4)
        g=dict(id=ticket.id,language='en',input_version=2,generation_epoch=3)
        requirements=proof.requirements if private else None
        await tx.ai_message(conv,g,body,requirements=requirements)
        await tx.create_generation(ticket.id,conv,text_hash(ticket.nonce),requirements=requirements)
        for call in connection.execute.await_args_list:
            sql=call.args[0].as_string()
            assert sql.count('%s')==len(call.args[1])
            # Runtime owns counters, not root classification. Labelled INSERTs
            # use the narrowly privileged database inheritance trigger.
            assert ('resource_security_labels' in sql) is (private and sql.startswith('INSERT'))
    asyncio.run(run())


def test_opt_in_capability_public_path_uses_no_source_grants():
    async def run():
        identity,ticket,store,access,service,body,proof=setup()
        store.conv['resource_security_labels']=[]
        original=store.ai_publication_context
        async def public(*args):
            result=await original(*args);result['run_labels']=[];return result
        store.ai_publication_context=public
        await service.capture_bound(identity,store.conv['id'],ticket,2,3)
        await service.publish(identity,ticket,'Public question')
        access.allows.assert_not_awaited()
    asyncio.run(run())


def test_public_grounding_fallback_keeps_same_atomic_graph_under_capability():
    async def run():
        from airport_assistant.modules.agent.answers import KnowledgeAnswerUnavailable,staff_offer
        identity,ticket,store,access,service,body,proof=setup()
        store.conv['resource_security_labels']=[]
        original=store.ai_publication_context
        async def public(*args):
            result=await original(*args);result['run_labels']=[];return result
        store.ai_publication_context=public
        service.answers=SimpleNamespace(validate_transaction=AsyncMock(side_effect=KnowledgeAnswerUnavailable()))
        await service.capture_bound(identity,store.conv['id'],ticket,2,3)
        saved=await service.publish(identity,ticket,'Old public source answer',grounding=object())
        assert store.graph['message']['body']==staff_offer('en')
        assert store.graph['message']['resource_security_labels']==[] and saved['message_id']==store.g['message_id']
        access.allows.assert_not_awaited()
    asyncio.run(run())


def test_exit_session_await_cannot_commit_after_source_deadline():
    async def run():
        identity,ticket,store,access,service,body,proof=await captured()
        original=store.session_active
        async def slow_exit():
            result=await original()
            if store.g is not None and store.g['state']=='published':store.clock=2.25
            return result
        store.session_active=slow_exit
        with pytest.raises(SourceFailure):await service.publish(identity,ticket,body,private_input=proof)
        assert store.g['state']=='pending' and store.graph is None and store.conv['version']==2
    asyncio.run(run())


@pytest.mark.parametrize('records',[None,{},[dict(source='reference',resource_type='booking',reference='PRIVATE',required_source_scopes=['booking:read'])]])
def test_default_capability_off_does_not_return_old_private_generation_receipt(records):
    async def run():
        from airport_assistant.modules.conversations.publication import AIPublication
        identity,ticket,store,access,service,body,proof=setup()
        store.generation=AsyncMock(return_value=dict(resource_security_labels=records))
        store.generated_message=AsyncMock()
        with pytest.raises(DraftError):await AIPublication(store).receipt(identity,ticket)
        store.generated_message.assert_not_awaited()
    asyncio.run(run())
