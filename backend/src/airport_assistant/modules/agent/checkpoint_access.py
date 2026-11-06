"""Internal checkpoint access; references/labels are requirements, not permits.

Private persistence is deliberately opt-in until the complete SQL inheritance
graph is qualified. The normal runtime keeps its existing public-only units.
"""
from contextlib import asynccontextmanager

from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.customer_transaction import customer_transaction,require_customer_session
from airport_assistant.modules.source.private_content import PrivateContentGate,PrivateProjection,inherited_records
from airport_assistant.modules.source.labels import SecurityLabels


def conversation_records(conversation):
    records=getattr(conversation,'resource_security_labels',None)
    if type(records) not in {tuple,list}:raise AuthError(404,'RESOURCE_NOT_FOUND')
    return list(records)


class CheckpointAccess:
    def __init__(self,engine,*,enabled=False):
        if type(enabled) is not bool:raise ValueError('Explicit private persistence switch required.')
        self.engine,self.enabled=engine,enabled
        self.gate=PrivateContentGate(engine.foundation.private_access)

    @property
    def repository(self):return self.engine.repository

    def result_labels(self,scope,run):
        if not self.enabled:
            self.public_header(run)
            if scope.labels is not None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            return None
        records=inherited_records(scope.identity.session.tenant_id,
            scope.labels.to_records() if scope.labels is not None else [],run.get('resource_security_labels'))
        return SecurityLabels.from_records(scope.identity.session.tenant_id,records) if records else None

    def draft_labels(self,scope,run,draft):
        labels=self.result_labels(scope,run)
        if not self.enabled:return labels
        stored=getattr(draft,'resource_security_labels',None)
        if type(stored) not in {list,tuple}:raise AuthError(404,'RESOURCE_NOT_FOUND')
        records=inherited_records(scope.identity.session.tenant_id,
            labels.to_records() if labels is not None else [],list(stored))
        return SecurityLabels.from_records(scope.identity.session.tenant_id,records) if records else None

    @staticmethod
    def public_header(run):
        if type(run.get('resource_security_labels')) is not list or run['resource_security_labels']!=[]:
            raise AuthError(404,'RESOURCE_NOT_FOUND')

    async def child(self,tx,identity,conv,run):
        if run.get('result_draft_id') is None:return None
        child=await tx.run_result_context(run['result_draft_id'])
        if (child is None or child.get('id')!=run['result_draft_id'] or child.get('tenant_id')!=identity.session.tenant_id
            or child.get('conversation_id')!=conv.id or child.get('created_session_id')!=identity.session.id
            or child.get('actor_principal_id')!=identity.session.principal_id
            or child.get('actor_session_id')!=(identity.session.id if identity.session.kind=='anonymous' else None)):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        return child

    async def public_run(self,tx,identity,conv,run):
        self.public_header(run)
        if conversation_records(conv)!=[]:raise AuthError(404,'RESOURCE_NOT_FOUND')
        child=await self.child(tx,identity,conv,run)
        if child is not None and (type(child.get('resource_security_labels')) is not list or child['resource_security_labels']!=[]):
            raise AuthError(404,'RESOURCE_NOT_FOUND')

    @staticmethod
    def require_stored(projection):
        _,run=projection.value
        own=inherited_records(projection.tenant_id,run.get('resource_security_labels'))
        expected=projection.labels.to_records() if projection.labels is not None else []
        if own!=expected:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')

    async def project(self,tx,identity,id):
        packet=getattr(tx,'checkpoint_candidate',None)
        if callable(packet):
            current=await packet(id)
            if current is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            conv,run,active=current
            if active is not True:raise AuthError(401,'AUTH_REQUIRED')
            from airport_assistant.modules.conversations.service import ConversationService
            if conv is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            ConversationService._owned_candidate(identity,conv.id,conv)
            if run is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        else:
            conv,run=await self.engine._locked(tx,identity,id,candidate_only=True)
        if (run.get('id')!=id or run.get('tenant_id')!=identity.session.tenant_id
            or run.get('session_id')!=identity.session.id or run.get('principal_id')!=identity.session.principal_id
            or run.get('conversation_id')!=conv.id):
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        groups=[conversation_records(conv),run.get('resource_security_labels')]
        child=await self.child(tx,identity,conv,run)
        if child is not None:
            groups.append(child.get('resource_security_labels'))
        records=inherited_records(identity.session.tenant_id,*groups)
        await require_customer_session(tx)
        return PrivateProjection.capture(identity.session.tenant_id,id,value=(conv,run),records=records,
            basis=dict(run=run,child=child,conversation=dict(id=conv.id,version=conv.version,
                epoch=conv.generation_epoch,language=conv.language,mode=conv.mode,case_id=conv.case_id,
                last_sequence=conv.last_sequence,labels=conversation_records(conv))))

    @asynccontextmanager
    async def unit(self,identity,id,*,additional_labels=None,after_check=None,before_commit=None):
        if not self.enabled:
            async with customer_transaction(self.repository,identity,before_commit=before_commit) as tx:
                conv,run=await self.engine._locked(tx,identity,id)
                yield tx,conv,run
            return
        current=None
        revision=None
        async def load(tx):
            nonlocal current,revision
            current=await self.project(tx,identity,id)
            if revision is None:revision=current.value[1]['revision']
            return current
        async def after(tx):
            result=await self.project(tx,identity,id)
            if result.value[1]['revision']!=revision:self.require_stored(result)
            if after_check is not None:await after_check(tx)
            return result
        async with self.gate.mutation(identity,lambda *,before_commit:customer_transaction(self.repository,identity,before_commit=before_commit),load,
                additional_labels=additional_labels,after_loader=after,before_commit=before_commit) as tx:
            yield tx,*current.value

    async def update(self,tx,conv,run,*,additional_labels=None,**changes):
        if self.enabled:
            if additional_labels is not None and (type(additional_labels) is not SecurityLabels
                    or additional_labels.tenant_id!=run['tenant_id']):
                raise ValueError('Trusted same-tenant checkpoint labels required.')
            records=inherited_records(run['tenant_id'],conversation_records(conv),run.get('resource_security_labels'),
                *([additional_labels.to_records()] if additional_labels is not None else []))
            if records!=run.get('resource_security_labels'):changes['resource_security_labels']=records
        return await tx.update_run(run,**changes)

    @asynccontextmanager
    async def start_unit(self,identity,request):
        current=None
        existed=None
        async def load(tx):
            nonlocal current,existed
            conv=await self.engine.foundation.drafts._conversation(tx,identity,request.conversation_id,
                **({'candidate_only':True} if self.enabled else {}))
            old=await tx.request_run(conv.id,request.request_key)
            if old is None:old=await tx.message_run(conv.id,request.message_id,request.mode)
            records=conversation_records(conv);old_basis=None
            if not self.enabled:
                if records:raise AuthError(404,'RESOURCE_NOT_FOUND')
                if old is not None:await self.public_run(tx,identity,conv,old)
            if old is not None and self.enabled:
                projection=await self.project(tx,identity,old['id'])
                records=inherited_records(identity.session.tenant_id,records,
                    projection.labels.to_records() if projection.labels is not None else [])
                old_basis=projection.basis_hash
            current=(conv,old)
            if existed is None:existed=old is not None
            return PrivateProjection.capture(identity.session.tenant_id,conv.id,value=current,records=records,
                basis=dict(version=conv.version,epoch=conv.generation_epoch,mode=conv.mode,case_id=conv.case_id,
                    language=conv.language,last_sequence=conv.last_sequence,old=old_basis))
        if not self.enabled:
            async with customer_transaction(self.repository,identity) as tx:
                await load(tx)
                yield tx,*current
            return
        async def after(tx):
            result=await load(tx)
            if current[1] is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if not existed:self.require_stored(await self.project(tx,identity,current[1]['id']))
            return result
        async with self.gate.mutation(identity,lambda *,before_commit:customer_transaction(self.repository,identity,before_commit=before_commit),load,after_loader=after) as tx:
            yield tx,*current

