"""Draft-owned evidence. Scan outside DB locks, then revalidate before storage."""
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
from typing import Protocol
from uuid import UUID,uuid5

from airport_assistant.modules.drafts.models import DraftError,reviewed_summary,seal
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import customer_transaction

MAX_BYTES=5*1024*1024
MEDIA={'image/png':'png','image/jpeg':'jpg','application/pdf':'pdf'}


class FileScanner(Protocol):
    async def clean(self,body:bytes)->bool:...


def validate_file(body,mime):
    if not body or len(body)>MAX_BYTES:raise DraftError(413,'PAYLOAD_TOO_LARGE')
    signature=(mime=='image/png' and body.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/jpeg' and body.startswith(b'\xff\xd8\xff')) or (mime=='application/pdf' and body.startswith(b'%PDF-'))
    if mime not in MEDIA or not signature:raise DraftError(415,'UNSUPPORTED_MEDIA_TYPE')


class AttachmentService:
    def __init__(self,repository,scanner:FileScanner|None=None,private_access=None):
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._repository,self._scanner=repository,scanner
        self._drafts=DraftService(repository,private_access)
        self._private_content=PrivateContentGate(private_access)

    @property
    def uploads_configured(self):
        return self._scanner is not None

    def _operation(self):
        from airport_assistant.modules.source.operation import SourceReadOperation
        from airport_assistant.modules.source.private_content import PrivateContentGate
        source=SourceReadOperation(self._private_content.source_access,self._private_content.clock)
        return PrivateContentGate(source,clock=self._private_content.clock),source.fresh

    async def _pending(self,tx,draft,version):
        if draft.case_id is not None:raise DraftError(422,'VALIDATION_ERROR')
        if draft.state=='expired' or draft.expires_at<=await tx.now():raise DraftError(410,'DRAFT_EXPIRED')
        if draft.state!='pending':raise DraftError(409,'DRAFT_NOT_PENDING',draft.result_ref)
        if draft.version!=version:raise DraftError(409,'VERSION_CONFLICT')

    async def upload(self,identity,draft_id,version,id,body,mime):
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        if type(version) is not int or version<1 or not isinstance(id,UUID):raise DraftError(422,'VALIDATION_ERROR')
        # Retry keys are scoped to this draft, not a caller-chosen shared object
        # namespace which could probe another uploader's attachment ID.
        id=uuid5(draft_id,str(id))
        validate_file(body,mime)
        digest=hashlib.sha256(body).hexdigest()
        preflight_projection=None
        async def candidate(tx):
            base=await self._drafts._mutation_candidate(tx,identity,draft_id,'edit_draft',historical_receipt=True)
            conversation,draft=base.value
            old=await tx.attachment(id)
            records=base.labels.to_records() if base.labels is not None else []
            if records and await tx.private_attachments_ready() is not True:raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
            parent=None
            if old is not None:
                if old['draft_id']!=draft_id or old['content_hash']!=digest or old['mime_type']!=mime:raise DraftError(409,'ATTACHMENT_CONFLICT')
                if old['id']!=id or old['scan_status']!='clean':raise AuthError(404,'RESOURCE_NOT_FOUND')
                parent=await tx.attachment_parent(old)
                if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                records=inherited_records(identity.session.tenant_id,records,old.get('resource_security_labels'),parent.get('resource_security_labels'))
            else:
                await self._pending(tx,draft,version)
                if await tx.attachment_count(draft_id)>=3:raise DraftError(409,'ATTACHMENT_LIMIT')
            return PrivateProjection.capture(identity.session.tenant_id,draft_id,value=(conversation,draft,old),
                basis=dict(draft_basis=base.basis_hash,attachment=old,parent=parent),records=records)
        async def preflight():
            nonlocal preflight_projection
            async with customer_transaction(self._repository,identity) as tx:
                preflight_projection=await candidate(tx)
                return preflight_projection
        gate,source_fresh=self._operation()
        before=await gate.read(identity,preflight,materialize=None)
        source_fresh()
        conversation,draft,old=before
        if old is not None:return self.view(old),draft
        if self._scanner is None:raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
        if await self._scanner.clean(body) is not True:raise DraftError(422,'ATTACHMENT_REJECTED')
        # Scanning may take longer than a source authorization lease. This is a
        # new authorization after scanning, never an extension of preflight.
        gate,source_fresh=self._operation();current=None;written=None
        def fresh():
            source_fresh()
            if written is not None and datetime.now(timezone.utc)>=written.expires_at:
                from airport_assistant.modules.source.models import SourceFailure
                raise SourceFailure(503)
        async def load(tx):
            nonlocal current
            current=await candidate(tx)
            _,stored,receipt=current.value
            if receipt is None and current.basis_hash!=preflight_projection.basis_hash:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            if written is not None and (receipt is None or stored.payload_hash!=written.payload_hash
                or stored.version!=written.version or receipt['content_hash']!=digest or receipt['byte_size']!=len(body)):
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            return current
        transaction=lambda *,before_commit=None:customer_transaction(self._repository,identity,before_commit=before_commit)
        async with gate.mutation(identity,transaction,load,before_commit=fresh) as tx:
            conversation,stored,old=current.value
            if old is None:
                row=await tx.insert_attachment(id,stored,body,mime,digest)
                payload=stored.payload.model_copy(update={'attachment_ids':stored.payload.attachment_ids+(id,)})
                try:summary=reviewed_summary(stored.action_type,payload,stored.destination,conversation.language,stored.case_id,source_evidence=stored.source_evidence)
                except ValueError:raise DraftError(422,'VALIDATION_ERROR') from None
                proposed=seal(replace(stored,payload=payload,version=stored.version+1,language=conversation.language,displayed_summary=summary))
                written=await tx.edit_draft(proposed)
                if written is None:raise DraftError(410,'DRAFT_EXPIRED')
        fresh()
        _,stored,old=current.value
        if old is None:raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
        return self.view(old),stored

    @staticmethod
    def view(row):
        return dict(id=row['id'],byte_size=row['byte_size'],mime_type=row['mime_type'],scan_status=row['scan_status'])

    async def download(self,identity,id):
        gate,source_fresh=self._operation()
        # Repository RLS also performs current parent/session/staff checks.
        # The transaction rechecks after fetching, before bytes leave the service.
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        async def metadata(tx):
            row=await tx.attachment(id)
            if row is None or row['scan_status']!='clean':raise AuthError(404,'RESOURCE_NOT_FOUND')
            parent=await tx.attachment_parent(row)
            if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
            labels=inherited_records(identity.session.tenant_id,row.get('resource_security_labels'),parent.get('resource_security_labels'))
            if labels and await tx.private_attachments_ready() is not True:raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
            return PrivateProjection.capture(identity.session.tenant_id,id,value=row,
                basis=dict(attachment=row,parent=parent),records=labels)
        async def load(check=None):
            async with self._repository.transaction(identity.database_context) as tx:
                if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
                before=await metadata(tx)
                result=before
                if check is not None:
                    check(before)
                    source_fresh()
                    row=await tx.attachment(id,body=True)
                    if row is None or row['scan_status']!='clean':raise AuthError(404,'RESOURCE_NOT_FOUND')
                    if {k:v for k,v in row.items() if k!='body'}!=before.value:
                        raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                    if hashlib.sha256(row['body']).hexdigest()!=row['content_hash']:raise DraftError(503,'ATTACHMENT_UNAVAILABLE')
                    after=await metadata(tx)
                    # Attachment content/parent binding is immutable for a read,
                    # including unlabeled blobs; do not relabel old bytes as new.
                    if before.basis_hash!=after.basis_hash:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                    check(after)
                    source_fresh()
                    result=replace(after,value=(row['body'],row['mime_type']))
                if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
                return result
        result=await gate.read(identity,load,materialize=load,
            purpose='staff' if identity.session.kind=='staff' else 'customer')
        source_fresh()
        return result

    async def list_case(self,identity,id):
        gate,source_fresh=self._operation()
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        async def load():
            async with self._repository.transaction(identity.database_context) as tx:
                if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
                if await tx.find_case(id) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                rows=await tx.case_attachments(id)
                parent=await tx.find_case(id)
                if parent is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                labels=inherited_records(identity.session.tenant_id,parent.get('resource_security_labels'),
                    *(row.get('resource_security_labels') for row in rows))
                if labels and await tx.private_attachments_ready() is not True:raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
                if await tx.session_active() is not True:raise AuthError(401,'AUTH_REQUIRED')
                return PrivateProjection.capture(identity.session.tenant_id,id,value=[self.view(row) for row in rows],
                    basis=dict(case=parent,attachments=rows),records=labels)
        result=await gate.read(identity,load,
            purpose='staff' if identity.session.kind=='staff' else 'customer')
        source_fresh()
        return result
