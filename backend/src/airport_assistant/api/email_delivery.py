"""Status metadata only; no browser endpoint can forge evidence or resend."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel
from airport_assistant.api.dependencies import get_staff_identity
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1/staff/cases',tags=['email delivery'])


class DeliveryItem(BaseModel):
    job_id:UUID
    app_message_id:UUID|None
    email_message_id:UUID|None
    delivery_status:Literal['queued','sending','accepted','delivered','bounced','failed','delivery_unknown']
    delivery_version:int|None
    needs_review:bool
    review_reason:str|None
    queued_at:datetime
    provider_status_at:datetime|None
    last_checked_at:datetime|None


class DeliveryList(BaseModel):
    items:list[DeliveryItem]
    has_more:bool


@router.get('/{id}/email-deliveries',response_model=DeliveryList)
async def deliveries(id:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):
        raise DraftError(400,'BAD_REQUEST')
    service=request.app.state.services.delivery_status
    if service is None:raise DraftError(503,'MAIL_UNAVAILABLE')
    return await service.list(identity,id)
