"""Customer preview lifecycle; confirmation is not exposed until CASE-02."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel,Field

from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.api.schemas import RequestModel,ErrorResponse
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.drafts.models import ReportPayload,Destination,DraftError

router=APIRouter(prefix='/api/v1',tags=['action drafts'],responses={status:{'model':ErrorResponse} for status in (400,401,403,404,409,410,422,503)})


class CreateDraft(RequestModel):
    conversation_id:UUID=Field(strict=False)
    action_type:Literal['create_case','request_handoff']
    payload:ReportPayload


class EditDraft(RequestModel):
    expected_version:int=Field(ge=1,strict=True)
    payload:ReportPayload


class DraftView(BaseModel):
    draft_id:UUID
    conversation_id:UUID
    action_type:Literal['create_case','request_handoff']
    payload:ReportPayload
    destination:Destination
    summary:str
    language:Literal['id','en']
    version:int
    payload_hash:str
    expires_at:datetime
    state:Literal['pending','cancelled','expired']


class ActionStatus(BaseModel):
    draft_id:UUID
    state:Literal['pending','cancelled','expired']
    version:int
    expires_at:datetime
    result_ref:None=None


def service(request,response):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(key) for key in
        ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):
        raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.drafts
    if result is None:raise DraftError(503,'DRAFT_UNAVAILABLE')
    return result


def view(draft):
    return DraftView(draft_id=draft.id,conversation_id=draft.conversation_id,action_type=draft.action_type,
        payload=draft.payload,destination=draft.destination,summary=draft.displayed_summary,
        language=draft.language,version=draft.version,payload_hash=draft.payload_hash,
        expires_at=draft.expires_at,state=draft.state)


@router.post('/action-drafts',status_code=201,response_model=DraftView)
async def create(body:CreateDraft,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return view(await service(request,response).create(identity,body.conversation_id,body.action_type,body.payload))


@router.get('/action-drafts/{id}',response_model=DraftView)
async def get(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return view(await service(request,response).get(identity,id))


@router.patch('/action-drafts/{id}',response_model=DraftView)
async def edit(id:UUID,body:EditDraft,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return view(await service(request,response).edit(identity,id,body.expected_version,body.payload))


@router.delete('/action-drafts/{id}',response_model=DraftView)
async def cancel(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return view(await service(request,response).cancel(identity,id))


@router.get('/actions/{id}',response_model=ActionStatus)
async def status(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    draft=await service(request,response).get(identity,id)
    return ActionStatus(draft_id=draft.id,state=draft.state,version=draft.version,expires_at=draft.expires_at)
