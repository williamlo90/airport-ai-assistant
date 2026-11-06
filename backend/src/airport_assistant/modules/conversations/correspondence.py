"""Bounded published correspondence; quarantine/provider bodies are excluded."""
from datetime import datetime
from uuid import UUID
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records


class StaffCorrespondence:
    def __init__(self,messages):self.owner=messages

    async def read(self,identity,case_id,membership,*,before_time=None,before_id=None,limit=50):
        if (type(limit) is not int or not 1<=limit<=50 or (before_time is None)!=(before_id is None)
            or before_time is not None and (not isinstance(before_time,datetime) or before_time.utcoffset() is None
                or not isinstance(before_id,UUID))):raise DraftError(400,'BAD_REQUEST')
        async def load():
            async with lifecycle_transaction(self.owner._repository,identity,staff=True) as tx:
                case=await tx.find_case(case_id)
                if case is None or await tx.staff_workspace(case_id,membership) is None:
                    raise AuthError(404,'RESOURCE_NOT_FOUND')
                ancestry=await tx.case_correspondence_ancestry(case_id)
                if ancestry is None:raise DraftError(503,'MAIL_UNAVAILABLE')
                rows=await tx.staff_correspondence(case_id,before_time,before_id,limit+1)
                records=inherited_records(identity.session.tenant_id,case.get('resource_security_labels'),ancestry,
                    *(r.get('resource_security_labels') for r in rows),
                    *(r.get('conversation_labels') for r in rows))
                if any(r.get('case_id')!=case_id for r in rows):raise AuthError(404,'RESOURCE_NOT_FOUND')
                if await tx.staff_workspace(case_id,membership) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                current=await tx.find_case(case_id)
                final_ancestry=await tx.case_correspondence_ancestry(case_id)
                if current!=case or final_ancestry!=ancestry:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                page=rows[:limit]
                items=[{k:r[k] for k in ('id','case_id','conversation_id','author_type','body','language','sequence','channel','created_at')}
                       for r in reversed(page)]
                oldest=page[-1] if len(rows)>limit else None
                value=dict(items=items,has_more=len(rows)>limit,
                    before_time=oldest['created_at'] if oldest else None,before_id=oldest['id'] if oldest else None)
                return PrivateProjection.capture(identity.session.tenant_id,case_id,value=value,
                    basis=dict(case=current,ancestry=final_ancestry,rows=rows,membership=membership),records=records)
        return await self.owner._history.read(identity,load,purpose='staff')
