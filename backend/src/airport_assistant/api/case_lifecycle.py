"""Customer case status and explicit, scoped staff lifecycle operations."""
from typing import Literal
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response,Query
from pydantic import BaseModel,Field
from airport_assistant.api.schemas import ErrorResponse
from airport_assistant.api.dependencies import get_identity,get_mutation_identity,get_staff_identity
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.cases.lifecycle import StatusChange,Assignment,ContinueCase
from airport_assistant.modules.cases.duplicate import DuplicateSearch
from airport_assistant.modules.cases.information_preview import InformationInput,InformationConfirmation
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1',tags=['case lifecycle'],responses={s:{'model':ErrorResponse} for s in (400,401,403,404,409,410,422,503)})


class HistoryView(BaseModel):
    event_type:Literal['created','identity_verified','assigned','status_changed','reopened','email_received','customer_information']
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


class ScopeLabel(BaseModel):
    id:UUID
    name:str


class ScopedQueueItem(QueueItem):
    queue_id:UUID
    created_at:datetime


class ScopedQueueView(BaseModel):
    membership_id:UUID
    membership_version:int=Field(ge=1)
    organization:ScopeLabel
    queues:list[ScopeLabel]
    queue_id:UUID|None
    view:Literal['all','mine','unassigned']
    status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']|None
    sort:Literal['created_asc']
    limit:int=Field(ge=1,le=100)
    items:list[ScopedQueueItem]
    has_more:bool
    next_cursor:str|None
    consistency:Literal['live_keyset']
    watermark:datetime
    observed_at:datetime


def service(request,response,*,query=False):
    response.headers['Cache-Control']='no-store'
    if (request.query_params and not query) or any(request.headers.getlist(k) for k in
        ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):
        raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.case_lifecycle
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return result


class CustomerCaseItem(BaseModel):
    id:UUID
    public_reference:str
    status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']
    summary:str
    next_actor:Literal['staff','customer','external','none']
    next_action:str
    version:int
    created_at:datetime
    updated_at:datetime


class CustomerCasePage(BaseModel):
    items:list[CustomerCaseItem]
    status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']|None
    limit:int
    sort:Literal['created_asc']
    has_more:bool
    next_cursor:str|None
    watermark:datetime
    consistency:Literal['live_keyset']


@router.get('/cases',response_model=CustomerCasePage)
async def customer_cases(request:Request,response:Response,status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']|None=None,
    limit:int=Query(default=25,ge=1,le=100),cursor:str|None=None,identity:AccessIdentity=Depends(get_identity)):
    strict_query(request,{'status','limit','cursor'})
    service(request,response,query=True)
    result=request.app.state.services.customer_cases
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await result.page(identity,status=status,limit=limit,cursor=cursor)


@router.get('/cases/{id}',response_model=CaseView)
async def customer_case(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return await service(request,response).get(identity,id)


@router.get('/staff/queues/cases',response_model=ScopedQueueView|QueueView)
async def queue(request:Request,response:Response,status:Literal['new','in_progress','awaiting_customer','awaiting_external','closed']|None=None,
    membership_id:UUID|None=None,queue_id:UUID|None=None,view:Literal['all','mine','unassigned']='all',sort:Literal['created_asc']='created_asc',
    limit:int=Query(default=25,ge=1,le=100),cursor:str|None=None,identity:AccessIdentity=Depends(get_staff_identity)):
    strict_query(request,{'status','membership_id','queue_id','view','sort','limit','cursor'})
    original=service(request,response,query=True)
    if membership_id is None:
        if set(request.query_params)-{'status'}:raise DraftError(400,'BAD_REQUEST')
        return dict(items=await original.queue(identity,status))
    result=request.app.state.services.staff_queue
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await result.page(identity,membership_id,queue_id=queue_id,view=view,status=status,sort=sort,limit=limit,cursor=cursor)


@router.get('/staff/cases/{id}',response_model=CaseView)
async def staff_case(id:UUID,request:Request,response:Response,membership_id:UUID|None=None,identity:AccessIdentity=Depends(get_staff_identity)):
    strict_query(request,{'membership_id'})
    return await service(request,response,query=True).get(identity,id,staff=True,membership=membership_id)


def strict_query(request,allowed):
    if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
        raise DraftError(400,'BAD_REQUEST')


class AssigneeLabel(BaseModel):
    membership_id:UUID
    display_name:str|None


class ActiveConversation(BaseModel):
    id:UUID
    version:int=Field(ge=1)
    mode:Literal['waiting_staff','human']


class CapabilityView(BaseModel):
    """Advisory snapshot; mutations revalidate authority, versions and required evidence."""
    case_id:UUID
    case_version:int=Field(ge=1)
    actor_principal_id:UUID
    membership_id:UUID
    membership_version:int=Field(ge=1)
    organization:ScopeLabel
    queue:ScopeLabel
    assignee:AssigneeLabel|None
    active_conversation:ActiveConversation|None
    self_claim_membership_id:UUID|None
    allowed_actions:list[Literal['claim','assign','unassign','update_status','close']]
    allowed_statuses:list[Literal['in_progress','awaiting_customer','awaiting_external']]
    allowed_close_reasons:list[Literal['resolved','customer_cancelled','duplicate','no_response']]


class AssigneePage(BaseModel):
    case_id:UUID
    case_version:int=Field(ge=1)
    membership_id:UUID
    membership_version:int=Field(ge=1)
    items:list[AssigneeLabel]
    has_more:bool
    next_cursor:str|None


class DuplicateCandidate(BaseModel):
    case_id:UUID
    public_reference:str


class DuplicateCandidatesView(BaseModel):
    case_id:UUID
    case_version:int=Field(ge=1)
    membership_id:UUID
    membership_version:int=Field(ge=1)
    items:list[DuplicateCandidate]=Field(max_length=1)


def capability_service(request,response,allowed):
    strict_query(request,allowed)
    service(request,response,query=True)
    result=request.app.state.services.staff_capabilities
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return result


@router.get('/staff/cases/{id}/capabilities',response_model=CapabilityView)
async def capabilities(id:UUID,request:Request,response:Response,membership_id:UUID,identity:AccessIdentity=Depends(get_staff_identity)):
    return await capability_service(request,response,{'membership_id'}).get(identity,id,membership_id)


@router.get('/staff/cases/{id}/eligible-assignees',response_model=AssigneePage)
async def eligible_assignees(id:UUID,request:Request,response:Response,membership_id:UUID,cursor:str|None=None,limit:int=Query(default=25,ge=1,le=100),identity:AccessIdentity=Depends(get_staff_identity)):
    return await capability_service(request,response,{'membership_id','cursor','limit'}).assignees(identity,id,membership_id,cursor,limit)


@router.patch('/staff/cases/{id}/status',response_model=CaseView)
async def status(id:UUID,body:StatusChange,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).status(identity,id,body)


@router.post('/staff/cases/{id}/duplicate-candidates/search',response_model=DuplicateCandidatesView)
async def duplicate_candidates(id:UUID,body:DuplicateSearch,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await capability_service(request,response,set()).duplicate_candidates(identity,id,body)


@router.patch('/staff/cases/{id}/assignment',response_model=CaseView)
async def assignment(id:UUID,body:Assignment,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).assign(identity,id,body)


@router.post('/cases/{id}/continue',response_model=CaseView)
async def continuation(id:UUID,body:ContinueCase,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).continue_case(identity,id,body)


class InformationPreviewView(BaseModel):
    preview_id:UUID
    case_id:UUID
    expected_version:int
    message:str
    language:Literal['id','en']
    expires_at:int
    confirmation_token:str=Field(repr=False)


class InformationReceiptView(BaseModel):
    state:Literal['saved']
    preview_id:UUID
    case_id:UUID
    message_id:UUID
    sequence:int
    case_version:int


def information_service(request,response):
    service(request,response)
    result=request.app.state.services.customer_information
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return result


@router.post('/cases/{id}/information-previews',response_model=InformationPreviewView)
async def information_preview(id:UUID,body:InformationInput,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await information_service(request,response).preview(identity,id,body)


@router.post('/cases/{id}/information',status_code=201,response_model=InformationReceiptView)
async def information_confirm(id:UUID,body:InformationConfirmation,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    result=information_service(request,response)
    keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    return await result.confirm(identity,id,body.confirmation_token,keys[0])


@router.get('/cases/{id}/information-receipts/{key}',response_model=InformationReceiptView)
async def information_receipt(id:UUID,key:str,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return await information_service(request,response).receipt(identity,id,key)


class EmailReplyView(BaseModel):
    email_id:UUID
    state:str
    reason:str|None
    app_message_id:UUID|None
    created_at:datetime
    body:str|None
    verification_state:str|None
    send_state:str|None
    attempts:int|None


@router.get('/staff/cases/{id}/email-replies',response_model=list[EmailReplyView])
async def email_replies(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_staff_identity)):
    return await service(request,response).email_replies(identity,id)
