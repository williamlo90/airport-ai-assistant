"""Explicit customer UI confirmation; model tools do not call this endpoint."""

from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import Field
from airport_assistant.api.schemas import RequestModel,ErrorResponse
from airport_assistant.api.dependencies import get_mutation_identity
from airport_assistant.api.drafts import service as draft_boundary
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.cases.models import CommitResult

router=APIRouter(prefix='/api/v1',tags=['case confirmation'],responses={status:{'model':ErrorResponse} for status in (400,401,403,404,409,410,422,503)})


class ConfirmDraft(RequestModel):
    expected_version:int=Field(ge=1,strict=True)
    payload_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/action-drafts/{id}/confirm',status_code=201,response_model=CommitResult)
async def confirm(id:UUID,body:ConfirmDraft,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    draft_boundary(request,response)
    keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    service=request.app.state.services.cases
    if service is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return await service.confirm(identity,id,body.expected_version,body.payload_hash,keys[0],UUID(request.state.request_id))
