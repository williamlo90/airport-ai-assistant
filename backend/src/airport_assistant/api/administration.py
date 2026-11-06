"""Staff access configuration, with no customer-case or credential fields."""
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response,Query
from pydantic import BaseModel
from airport_assistant.api.dependencies import get_staff_identity,get_mutation_identity
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.administration import MembershipEdit
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1/admin',tags=['staff access administration'])


class OrganizationView(BaseModel):
    id:UUID
    name:str


class QueueView(OrganizationView):
    active:bool


class AdminWorkspace(BaseModel):
    membership_id:UUID
    membership_version:int
    organization:OrganizationView
    queues:list[QueueView]


class MemberView(BaseModel):
    membership_id:UUID
    principal_id:UUID
    organization_id:UUID
    display_name:str|None
    principal_active:bool
    active:bool
    version:int
    roles:list[str]
    queue_ids:list[UUID]


class MemberResult(BaseModel):
    workspace:AdminWorkspace
    member:MemberView


class StaffPage(BaseModel):
    workspace:AdminWorkspace
    items:list[MemberView]
    limit:int
    has_more:bool
    next_after:UUID|None
    consistency:str


def service(request,response,allowed=()):
    response.headers['Cache-Control']='no-store'
    if any(key not in allowed for key in request.query_params) or len(request.query_params.multi_items())!=len(request.query_params) or any(
        request.headers.getlist(key) for key in ('x-tenant-id','x-principal-id','x-session-id','x-subject-id','x-source-subject','x-source-context','x-source-signature')):
        raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.administration
    if result is None:raise DraftError(503,'STAFF_UNAVAILABLE')
    return result


@router.get('/workspace',response_model=AdminWorkspace)
async def workspace(request:Request,response:Response,membership_id:UUID,identity:AccessIdentity=Depends(get_staff_identity)):
    return await service(request,response,{'membership_id'}).workspace(identity,membership_id)


@router.get('/staff',response_model=StaffPage)
async def directory(request:Request,response:Response,membership_id:UUID,after:UUID|None=None,
    limit:int=Query(default=25,ge=1,le=100),identity:AccessIdentity=Depends(get_staff_identity)):
    return await service(request,response,{'membership_id','after','limit'}).directory(identity,membership_id,after=after,limit=limit)


@router.get('/staff/{principal_id}/memberships',response_model=MemberResult)
async def member(principal_id:UUID,request:Request,response:Response,membership_id:UUID,identity:AccessIdentity=Depends(get_staff_identity)):
    return await service(request,response,{'membership_id'}).get(identity,principal_id,membership_id)


@router.patch('/staff/{principal_id}/memberships',response_model=MemberResult)
async def change(principal_id:UUID,body:MembershipEdit,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).update(identity,principal_id,body)
