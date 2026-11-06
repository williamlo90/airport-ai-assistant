"""Raw single-file upload; parent/version in query, stable UUID in retry header."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Request,Response,Query
from pydantic import BaseModel,Field
from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.api.drafts import view,DraftView
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.attachments.service import MEDIA
from airport_assistant.modules.identity.auth import AuthError

router=APIRouter(prefix='/api/v1',tags=['attachments'])


class AttachmentCapabilities(BaseModel):
    upload_enabled:bool
    max_bytes:Literal[5242880]=5242880
    max_files:Literal[3]=3
    media_types:list[Literal['image/png','image/jpeg','application/pdf']]


@router.get('/attachments/capabilities',response_model=AttachmentCapabilities)
async def capabilities(request:Request,response:Response,identity=Depends(get_identity)):
    if identity.session.kind!='customer':raise AuthError(401,'AUTH_REQUIRED')
    if request.query_params or any(request.headers.getlist(k) for k in
        ('x-tenant-id','x-principal-id','x-session-id','x-subject-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    response.headers['Cache-Control']='no-store'
    handler=request.app.state.services.attachments
    # Configuration availability is not authorization for a draft or a promise
    # that a scanner cannot fail later. Upload still checks its current parent.
    return dict(upload_enabled=handler is not None and handler.uploads_configured,
        max_bytes=5242880,max_files=3,media_types=list(MEDIA))


class AttachmentView(BaseModel):
    id:UUID
    byte_size:int=Field(ge=1,le=5242880)
    mime_type:Literal['image/png','image/jpeg','application/pdf']
    scan_status:Literal['clean']


class UploadView(BaseModel):
    attachment:AttachmentView
    draft:DraftView


def service(request):
    if any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-session-id','x-subject-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.attachments
    if result is None:raise DraftError(503,'ATTACHMENT_UNAVAILABLE')
    return result


@router.post('/attachments',status_code=201,response_model=UploadView)
async def upload(request:Request,response:Response,draft_id:UUID,expected_version:int=Query(ge=1),identity=Depends(get_mutation_identity)):
    handler=service(request)
    if set(request.query_params)!={'draft_id','expected_version'} or len(request.query_params.multi_items())!=2:raise DraftError(400,'BAD_REQUEST')
    try:
        keys=request.headers.getlist('idempotency-key')
        if len(keys)!=1:raise ValueError()
        id=UUID(keys[0])
    except ValueError:raise DraftError(422,'IDEMPOTENCY_KEY_REQUIRED') from None
    types=request.headers.getlist('content-type')
    if len(types)!=1 or types[0] not in MEDIA:raise DraftError(415,'UNSUPPORTED_MEDIA_TYPE')
    attachment,draft=await handler.upload(identity,draft_id,expected_version,id,await request.body(),types[0])
    response.headers['Cache-Control']='no-store'
    return dict(attachment=attachment,draft=view(draft))


@router.get('/attachments/{id}/download')
async def download(id:UUID,request:Request,identity=Depends(get_identity)):
    handler=service(request)
    if request.query_params:raise DraftError(400,'BAD_REQUEST')
    body,mime=await handler.download(identity,id)
    return Response(body,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"sandbox; default-src 'none'",'Content-Disposition':f'attachment; filename="{id}.{MEDIA[mime]}"'})


@router.get('/cases/{id}/attachments',response_model=list[AttachmentView])
async def list_case(id:UUID,request:Request,response:Response,identity=Depends(get_identity)):
    handler=service(request)
    if request.query_params:raise DraftError(400,'BAD_REQUEST')
    response.headers['Cache-Control']='no-store'
    return await handler.list_case(identity,id)
