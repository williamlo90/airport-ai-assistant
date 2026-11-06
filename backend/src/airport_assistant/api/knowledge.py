"""Admin knowledge control plane. No public fetch URL or index-ready override."""
from datetime import datetime
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Request,Response,Query
from pydantic import BaseModel
from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.knowledge.models import Content,CreateDocument,CreateVersion,EditVersion,Transition,Withdraw
router=APIRouter(prefix='/api/v1/admin/knowledge',tags=['knowledge administration'])

class DocumentView(BaseModel):
    id:UUID
    organization_id:UUID
    document_key:str
    title:str
    revision:int
    created_by:UUID
    created_at:datetime
    updated_at:datetime
class VersionView(Content):
    id:UUID
    document_id:UUID
    version:int
    revision:int
    organization_id:UUID|None
    content_hash:str
    basis_hash:str
    status:Literal['draft','published','withdrawn']
    published_at:datetime|None
    published_by:UUID|None
    withdrawn_at:datetime|None
    withdrawn_by:UUID|None
    withdrawal_reason:str|None
    index_generation:UUID|None
    created_by:UUID
    created_at:datetime
    updated_at:datetime
class VersionPage(BaseModel):
    items:list[VersionView]
    has_more:bool
    next_after:int|None
class AuditView(BaseModel):
    id:UUID
    document_id:UUID
    version_id:UUID|None
    document_revision:int
    version_revision:int|None
    actor_principal_id:UUID
    actor_membership_id:UUID
    event_type:str
    basis_hash:str|None
    index_generation:UUID|None
    reason:str|None
    created_at:datetime
class AuditPage(BaseModel):
    items:list[AuditView]
    has_more:bool
    next_after:int|None

class IndexView(BaseModel):
    version_id:UUID
    generation:UUID
    basis_hash:str
    valid_until:datetime

def service(request,response,identity,allowed=()):
    response.headers['Cache-Control']='no-store'
    if identity.session.kind!='staff':raise AuthError(401,'AUTH_REQUIRED')
    if set(request.query_params)-set(allowed) or any(len(request.query_params.getlist(k))>1 for k in allowed) or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.knowledge
    if result is None:raise DraftError(503,'KNOWLEDGE_UNAVAILABLE')
    return result

class DocumentPage(BaseModel):
    membership_id:UUID
    organization_id:UUID
    items:list[DocumentView]
    limit:int
    has_more:bool
    next_after:UUID|None
    consistency:Literal['live_keyset']

@router.get('/documents',response_model=DocumentPage)
async def directory(request:Request,response:Response,membership_id:UUID,after:UUID|None=None,
    limit:int=Query(25,ge=1,le=50),document_key:str|None=Query(None,min_length=1,max_length=100,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$'),identity=Depends(get_identity)):
    return await service(request,response,identity,('membership_id','after','limit','document_key')).directory(identity,membership_id,after=after,limit=limit,document_key=document_key)

@router.post('/documents',status_code=201,response_model=DocumentView)
async def create(body:CreateDocument,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,identity).create(identity,body)
@router.post('/documents/{id}/versions',status_code=201,response_model=VersionView)
async def add(id:UUID,body:CreateVersion,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,identity).add_version(identity,id,body)
@router.get('/documents/{id}',response_model=DocumentView)
async def document(id:UUID,request:Request,response:Response,membership_id:UUID,identity=Depends(get_identity)):
    return await service(request,response,identity,('membership_id',)).get(identity,id,membership_id)
@router.get('/documents/{id}/versions',response_model=VersionPage)
async def versions(id:UUID,request:Request,response:Response,membership_id:UUID,after:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=50),identity=Depends(get_identity)):
    return await service(request,response,identity,('membership_id','after','limit')).list(identity,id,membership_id,after,limit)
@router.get('/documents/{id}/audit',response_model=AuditPage)
async def audit(id:UUID,request:Request,response:Response,membership_id:UUID,after:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=50),identity=Depends(get_identity)):
    return await service(request,response,identity,('membership_id','after','limit')).list(identity,id,membership_id,after,limit,audit=True)
@router.get('/versions/{id}',response_model=VersionView)
async def version(id:UUID,request:Request,response:Response,membership_id:UUID,identity=Depends(get_identity)):
    return await service(request,response,identity,('membership_id',)).get(identity,id,membership_id,version=True)
@router.patch('/versions/{id}',response_model=VersionView)
async def edit(id:UUID,body:EditVersion,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,identity).edit(identity,id,body)
@router.post('/versions/{id}/publish',response_model=VersionView)
async def publish(id:UUID,body:Transition,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,identity).publish(identity,id,body)
@router.post('/versions/{id}/withdraw',response_model=VersionView)
async def withdraw(id:UUID,body:Withdraw,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,identity).withdraw(identity,id,body)

@router.post('/versions/{id}/index',response_model=IndexView)
async def index(id:UUID,body:Transition,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    lifecycle=service(request,response,identity)
    if lifecycle.retrieval is None:raise DraftError(503,'KNOWLEDGE_INDEX_UNAVAILABLE')
    proof=await lifecycle.retrieval.prepare(identity,id,body)
    return dict(version_id=proof.version_id,generation=proof.generation,basis_hash=proof.basis_hash,valid_until=proof.valid_until)

class IndexStatus(BaseModel):
    version_id:UUID
    basis_hash:str
    ready:bool
    generation:UUID|None
    indexing_available:bool

@router.get('/versions/{id}/index',response_model=IndexStatus)
async def index_status(id:UUID,request:Request,response:Response,membership_id:UUID,identity=Depends(get_identity)):
    return await service(request,response,identity,('membership_id',)).index_status(identity,id,membership_id)
