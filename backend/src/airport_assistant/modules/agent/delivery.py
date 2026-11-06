"""Connect the real workflow to existing publication and human-confirmed drafts."""
import hashlib
import hmac
from airport_assistant.modules.conversations.publication import GenerationTicket
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from .models import AgentError


class AgentDelivery:
    def __init__(self,workflow,publication,secret):
        self.workflow,self.publication,self.secret=workflow,publication,secret

    def ticket(self,identity,run_id):
        values=':'.join(str(v) for v in (identity.session.tenant_id,identity.session.id,run_id))
        nonce=hmac.new(self.secret,b'agent-publication-v1:'+values.encode(),hashlib.sha256).hexdigest()
        return GenerationTicket(run_id,nonce)

    async def start(self,identity,request):
        if (request.booking_references or request.transaction_references) and not self.private_enabled:
            raise AgentError(503,'AGENT_PRIVATE_UNAVAILABLE')
        return await self.workflow.start(identity,request)

    @property
    def private_enabled(self):
        return self.workflow.engine.checkpoints.enabled and getattr(self.publication,'private_capability',None) is not None

    async def status(self,identity,run_id):
        """Read a saved receipt without resuming planning, tools or publication."""
        receipt=await self.publication.receipt(identity,self.ticket(identity,run_id))
        if receipt is not None:return dict(run_id=run_id,**receipt)
        engine=self.workflow.engine
        draft_id=None
        async with engine.checkpoints.unit(identity,run_id) as (tx,conv,run):
            if run['phase']=='done':draft_id=run['result_draft_id']
        if draft_id is not None:
            draft=await engine.foundation.drafts.get(identity,draft_id)
            return dict(run_id=run_id,state='draft',draft_id=draft.id)
        return dict(run_id=run_id,state='unresolved')

    async def resume(self,identity,run_id):
        ticket=self.ticket(identity,run_id)
        receipt=await self.publication.receipt(identity,ticket)
        if receipt is not None:return dict(run_id=run_id,**receipt)
        try:
            work=await self.workflow.engine.load(identity,run_id)
            if work.run['mode'] in {'assist','answer'} and work.result is None:
                await self.publication.capture_bound(identity,work.run['conversation_id'],ticket,
                    work.run['conversation_version'],work.run['generation_epoch'])
                saved=await self.publication.receipt(identity,ticket)
                if saved is not None:return dict(run_id=run_id,**saved)
            result=await self.workflow.resume(identity,run_id)
        except AuthError as error:
            # Handoff can hide the standalone run through RLS. Discard only if
            # the current actor can still authorize the bound generation ticket.
            if error.status!=404:raise
            discarded=await self.publication.receipt(identity,ticket,discard=True)
            if discarded is None:raise
            return dict(run_id=run_id,**discarded)
        except (AgentError,DraftError) as error:
            if error.code not in {'AGENT_CONTEXT_CHANGED','AI_PUBLICATION_BLOCKED'}:raise
            saved=await self.publication.receipt(identity,ticket,discard=True)
            return dict(run_id=run_id,**(saved or dict(state='discarded',reason='STALE_GENERATION')))
        if result.status=='done':
            return dict(run_id=run_id,state='draft',draft_id=result.draft.id)
        if result.status in {'waiting_user','answer'}:
            if result.labels is not None:
                if getattr(self.publication,'private_capability',None) is None:
                    await self.publication.receipt(identity,ticket,discard=True)
                    return dict(run_id=run_id,state='review',reason='PRIVATE_PERSISTENCE_REQUIRED')
                from .private_publication import PrivatePublicationPreparation
                # Reserve durable work first. Fresh facts are collected only
                # after publisher preflight/grants, outside its SQL units.
                fresh=await self.workflow.engine.load(identity,run_id)
                await self.publication.capture_bound(identity,fresh.run['conversation_id'],ticket,
                    fresh.run['conversation_version'],fresh.run['generation_epoch'],requirements=result.labels)
                preparation=PrivatePublicationPreparation(self.workflow.engine)
                try:
                    reserved=await preparation.reserve(identity,run_id,result)
                    saved=await self.publication.publish_prepared(identity,ticket,
                        lambda:preparation.collect(identity,reserved),reserved.requirements)
                except AgentError as error:
                    if error.code not in {'AGENT_CONTEXT_CHANGED','AGENT_STEP_LIMIT'}:raise
                    return dict(run_id=run_id,state='review',reason=error.code)
                return dict(run_id=run_id,**saved)
            # A restart after waiting_user never replays generated private text;
            # it may publish only the workflow's safe localized generic template.
            await self.publication.capture_bound(identity,work.run['conversation_id'],ticket,
                work.run['conversation_version'],work.run['generation_epoch'])
            try:
                if result.grounding is not None:saved=await self.publication.publish(identity,ticket,result.question,grounding=result.grounding)
                else:saved=await self.publication.publish(identity,ticket,result.question)
            except DraftError as error:
                if error.code!='MESSAGE_CONFLICT':raise
                # A concurrent resume may already have published the safe
                # restored template. Read its receipt; never retry/overwrite.
                saved=await self.publication.receipt(identity,ticket)
                if saved is None:raise
            return dict(run_id=run_id,**saved)
        saved=await self.publication.receipt(identity,ticket,discard=True)
        if saved is not None:return dict(run_id=run_id,**saved)
        return dict(run_id=run_id,state='discarded' if result.status=='superseded' else 'review',reason=result.reason)
