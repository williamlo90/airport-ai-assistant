"""Human-initiated replies only; AI publication has no HTTP endpoint."""
from uuid import UUID
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,Depends,Request,Response,Query
from pydantic import BaseModel
from airport_assistant.api.dependencies import get_mutation_identity,get_staff_identity
from airport_assistant.modules.conversations.publication import StaffMessage
from airport_assistant.modules.conversations.case_email import CaseEmail,CaseEmails
from airport_assistant.modules.conversations.correspondence import StaffCorrespondence
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1/staff/cases',tags=['staff messages'])


class SavedMessage(BaseModel):
    state:Literal['saved']
    message_id:UUID
    conversation_id:UUID|None
    case_id:UUID
    sequence:int
    channel:Literal['chat','email']


class CorrespondenceMessage(BaseModel):
    id:UUID
    case_id:UUID
    conversation_id:UUID|None
    author_type:Literal['customer','staff','ai']
    body:str
    language:Literal['en','id']
    sequence:int
    channel:Literal['chat','email']
    created_at:datetime


class CorrespondencePage(BaseModel):
    items:list[CorrespondenceMessage]
    has_more:bool
    before_time:datetime|None
    before_id:UUID|None


class CommunicationCapability(BaseModel):
    case_id:UUID
    case_version:int
    membership_id:UUID
    email_available:bool


@router.get('/{id}/communication-capabilities',response_model=CommunicationCapability)
async def communication_capability(id:UUID,membership_id:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    response.headers['Cache-Control']='no-store'
    if (set(request.query_params)!={'membership_id'} or len(request.query_params.getlist('membership_id'))!=1
        or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature'))):raise DraftError(400,'BAD_REQUEST')
    service=request.app.state.services.staff_messages
    if service is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await CaseEmails(service).capability(identity,id,membership_id)


@router.get('/{id}/messages',response_model=CorrespondencePage)
async def history(id:UUID,membership_id:UUID,request:Request,response:Response,
                  before_time:datetime|None=None,before_id:UUID|None=None,
                  limit:int=Query(default=50,ge=1,le=50),identity=Depends(get_staff_identity)):
    response.headers['Cache-Control']='no-store'
    keys={'membership_id','before_time','before_id','limit'}
    if (set(request.query_params)-keys or any(len(request.query_params.getlist(k))>1 for k in keys)
        or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature'))):raise DraftError(400,'BAD_REQUEST')
    service=request.app.state.services.staff_messages
    if service is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await StaffCorrespondence(service).read(identity,id,membership_id,
        before_time=before_time,before_id=before_id,limit=limit)


@router.get('/{id}/message-receipts/{key}',response_model=SavedMessage)
async def receipt(id:UUID,key:str,membership_id:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    response.headers['Cache-Control']='no-store'
    if (set(request.query_params)!={'membership_id'} or len(request.query_params.getlist('membership_id'))!=1
        or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature'))):raise DraftError(400,'BAD_REQUEST')
    service=request.app.state.services.staff_messages
    if service is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await service.receipt(identity,id,key,membership_id)


@router.post('/{id}/email-messages',status_code=201,response_model=SavedMessage)
async def send_case_email(id:UUID,body:CaseEmail,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    service=request.app.state.services.staff_messages
    if service is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await CaseEmails(service).send(identity,id,body,keys[0])


@router.post('/{id}/messages',status_code=201,response_model=SavedMessage)
async def send(id:UUID,body:StaffMessage,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    service=request.app.state.services.staff_messages
    if service is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await service.send(identity,id,body,keys[0])
