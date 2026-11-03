"""Customer case status and explicit, scoped staff lifecycle operations."""
from typing import Literal
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel
from airport_assistant.api.schemas import ErrorResponse
from airport_assistant.api.dependencies import get_identity,get_mutation_identity,get_staff_identity
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.cases.lifecycle import StatusChange,Assignment,ContinueCase
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1',tags=['case lifecycle'],responses={s:{'model':ErrorResponse} for s in (400,401,403,404,409,422,503)})


class HistoryView(BaseModel):
    event_type:Literal['created','identity_verified','assigned','status_changed','reopened']
    previous_version:int
    resulting_version:int
    from_status:str|None
    to_status:str|None
    next_actor:str|None
    next_action:str|None
    close_reason:str|None
    resolution:str|None
    customer_message:str|None
    created_at:datetime


class QueueItem(BaseModel):
    id:UUID
    public_reference:str
    status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']
    summary:str
    assigned_membership_id:UUID|None
    next_actor:Literal['staff','customer','external','none']
    next_action:str
    version:int
    updated_at:datetime


class CaseView(QueueItem):
    category:Literal['information','booking_issue','transaction_issue']
    service_type:Literal['general','parking','lounge']
    customer_report:str
    booking_reference:str|None
    transaction_reference:str|None
    language:Literal['id','en']
    close_reason:Literal['resolved','customer_cancelled','duplicate','no_response']|None
    resolution:str|None
    related_case_ref:str|None=None
    closed_at:datetime|None
    history:list[HistoryView]


class QueueView(BaseModel):
    items:list[QueueItem]


def service(request,response,*,query=False):
    response.headers['Cache-Control']='no-store'
    if (request.query_params and not query) or any(request.headers.getlist(k) for k in
        ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):
        raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.case_lifecycle
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return result


@router.get('/cases/{id}',response_model=CaseView)
async def customer_case(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return await service(request,response).get(identity,id)


@router.get('/staff/queues/cases',response_model=QueueView)
async def queue(request:Request,response:Response,status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']|None=None,identity:AccessIdentity=Depends(get_staff_identity)):
    if set(request.query_params)-{'status'} or len(request.query_params.getlist('status'))>1:raise DraftError(400,'BAD_REQUEST')
    return dict(items=await service(request,response,query=True).queue(identity,status))


@router.get('/staff/cases/{id}',response_model=CaseView)
async def staff_case(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_staff_identity)):
    return await service(request,response).get(identity,id,staff=True)


@router.patch('/staff/cases/{id}/status',response_model=CaseView)
async def status(id:UUID,body:StatusChange,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).status(identity,id,body)


@router.patch('/staff/cases/{id}/assignment',response_model=CaseView)
async def assignment(id:UUID,body:Assignment,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).assign(identity,id,body)


@router.post('/cases/{id}/continue',response_model=CaseView)
async def continuation(id:UUID,body:ContinueCase,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).continue_case(identity,id,body)
