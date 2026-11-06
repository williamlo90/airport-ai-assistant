"""Knowledge lifecycle; provider readiness is checked outside DB transactions."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime,timezone
from uuid import UUID
from typing import Protocol
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from .models import CreateDocument,CreateVersion,EditVersion,Transition,Withdraw,Selection

@dataclass(frozen=True)
class IndexProof:
    tenant_id:UUID
    version_id:UUID
    basis_hash:str
    generation:UUID
    valid_until:datetime
    def matches(self,tenant,version,basis):
        return (self.tenant_id==tenant and self.version_id==version and self.basis_hash==basis and isinstance(self.generation,UUID)
            and self.valid_until.tzinfo is not None and datetime.now(timezone.utc)<self.valid_until)
class KnowledgeIndex(Protocol):
    async def readiness(self,tenant_id:UUID,version_id:UUID,basis_hash:str)->IndexProof|None:...
class UnavailableIndex:
    async def readiness(self,tenant_id,version_id,basis_hash):return None

def overlaps(a,b):
    if a['service_type']!=b['service_type'] or a['language']!=b['language'] or a['policy_reference']!=b['policy_reference']:return False
    def interval(af,at,bf,bt):return (at is None or bf is None or bf<at) and (bt is None or af is None or af<bt)
    if not interval(a['valid_from'],a['valid_to'],b['valid_from'],b['valid_to']):return False
    x,y=a['applicability'],b['applicability']
    if x.get('terminal') and y.get('terminal') and x['terminal']!=y['terminal']:return False
    return interval(x.get('booking_created_from'),x.get('booking_created_to'),y.get('booking_created_from'),y.get('booking_created_to'))

class KnowledgeService:
    def __init__(self,repository,index=None):self.repository=repository;self.index=index or UnavailableIndex();self.retrieval=None
    @asynccontextmanager
    async def transaction(self,identity):
        async with self.repository.transaction(identity.database_context) as tx:
            if not await tx.session_active():raise AuthError(401,'AUTH_REQUIRED')
            yield tx
            if not await tx.session_active():raise AuthError(401,'AUTH_REQUIRED')
    async def admin(self,tx,identity,mid,organization=None):
        if identity.session.kind!='staff':raise AuthError(401,'AUTH_REQUIRED')
        scope=await tx.admin(mid,organization)
        if scope is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return scope
    async def document(self,tx,identity,id,mid):
        d=await tx.document(id,lock=True)
        if d is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        await self.admin(tx,identity,mid,d['organization_id'])
        return d
    async def version(self,tx,identity,id,mid):
        # Resolve parent first, then lock document -> version consistently.
        parent=await tx.parent(id)
        if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        d=await self.document(tx,identity,parent,mid)
        v=await tx.version(id,lock=True)
        if v is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return d,v
    @staticmethod
    def revision(v,expected):
        if v['revision']!=expected:raise DraftError(409,'VERSION_CONFLICT')
    @staticmethod
    def draft(v):
        if v['status']!='draft':raise DraftError(409,'KNOWLEDGE_NOT_DRAFT')
    async def index_status(self,identity,id,mid):
        async with self.transaction(identity) as tx:
            d,v=await self.version(tx,identity,id,mid)
            proof=await tx.index_proof(id,v['basis_hash'])
            ready=isinstance(proof,IndexProof) and proof.matches(identity.session.tenant_id,id,v['basis_hash'])
            return dict(version_id=id,basis_hash=v['basis_hash'],ready=ready,
                generation=proof.generation if ready else None,indexing_available=self.retrieval is not None)

    async def directory(self,identity,mid,*,after=None,limit=25,document_key=None):
        if type(limit) is not int or not 1<=limit<=50:raise DraftError(422,'VALIDATION_ERROR')
        async with self.transaction(identity) as tx:
            scope=await self.admin(tx,identity,mid)
            rows=await tx.documents(scope['organization_id'],after,limit+1,document_key)
            if scope!=await self.admin(tx,identity,mid):raise DraftError(409,'MEMBERSHIP_SCOPE_CHANGED')
            selected=rows[:limit];more=len(rows)>limit
            return dict(membership_id=mid,organization_id=scope['organization_id'],items=selected,limit=limit,
                has_more=more,next_after=selected[-1]['id'] if more else None,consistency='live_keyset')

    async def create(self,identity,command):
        c=command if isinstance(command,CreateDocument) else CreateDocument.model_validate(command)
        async with self.transaction(identity) as tx:
            scope=await self.admin(tx,identity,c.membership_id)
            d=await tx.create_document(c,scope['organization_id'])
            await tx.create_version(d,c.content)
            return await tx.document(d)
    async def predecessor(self,tx,doc,content,number=None):
        if content.supersedes_version_id is not None:
            old=await tx.predecessor(doc,content.supersedes_version_id)
            if old is None or (number is not None and old>=number):raise DraftError(422,'VALIDATION_ERROR')
    async def add_version(self,identity,id,command):
        c=command if isinstance(command,CreateVersion) else CreateVersion.model_validate(command)
        async with self.transaction(identity) as tx:
            d=await self.document(tx,identity,id,c.membership_id);self.revision(d,c.expected_document_revision)
            await self.predecessor(tx,id,c.content)
            vid=await tx.create_version(id,c.content)
            return await tx.version(vid)
    async def edit(self,identity,id,command):
        c=command if isinstance(command,EditVersion) else EditVersion.model_validate(command)
        async with self.transaction(identity) as tx:
            d,v=await self.version(tx,identity,id,c.membership_id);self.revision(v,c.expected_revision);self.draft(v)
            await self.predecessor(tx,d['id'],c.content,v['version'])
            return await tx.edit(id,c.content)
    async def publish(self,identity,id,command):
        c=command if isinstance(command,Transition) else Transition.model_validate(command)
        async with self.transaction(identity) as tx:
            d,v=await self.version(tx,identity,id,c.membership_id);self.revision(v,c.expected_revision);self.draft(v)
            basis=v['basis_hash']
        proof=await self.retrieval.readiness(identity,id,basis) if self.retrieval is not None else await self.index.readiness(identity.session.tenant_id,id,basis)
        if not isinstance(proof,IndexProof) or not proof.matches(identity.session.tenant_id,id,basis):raise DraftError(503,'KNOWLEDGE_INDEX_UNAVAILABLE')
        async with self.transaction(identity) as tx:
            d,v=await self.version(tx,identity,id,c.membership_id);self.revision(v,c.expected_revision);self.draft(v)
            if v['basis_hash']!=basis or not proof.matches(identity.session.tenant_id,id,basis):raise DraftError(409,'VERSION_CONFLICT')
            for other in await tx.published(d['id']):
                if overlaps(v,other):raise DraftError(409,'KNOWLEDGE_AMBIGUOUS')
            return await tx.publish(id,proof)
    async def withdraw(self,identity,id,command):
        c=command if isinstance(command,Withdraw) else Withdraw.model_validate(command)
        async with self.transaction(identity) as tx:
            d,v=await self.version(tx,identity,id,c.membership_id);self.revision(v,c.expected_revision)
            if v['status']!='published':raise DraftError(409,'KNOWLEDGE_NOT_PUBLISHED')
            return await tx.withdraw(id,c.reason)
    async def get(self,identity,id,mid,*,version=False):
        async with self.transaction(identity) as tx:
            if version:return (await self.version(tx,identity,id,mid))[1]
            return await self.document(tx,identity,id,mid)
    async def list(self,identity,id,mid,after=0,limit=20,*,audit=False):
        async with self.transaction(identity) as tx:
            await self.document(tx,identity,id,mid)
            items=await tx.list(id,after,limit+1,audit=audit)
            selected=items[:limit]
            return dict(items=selected,has_more=len(items)>limit,next_after=selected[-1]['document_revision' if audit else 'version'] if len(items)>limit else None)
    async def select(self,identity,selection):
        c=selection if isinstance(selection,Selection) else Selection.model_validate(selection)
        async with self.transaction(identity) as tx:
            found=await tx.select(c)
            if len(found)>1:raise DraftError(409,'KNOWLEDGE_AMBIGUOUS')
            return found[0] if found else None
