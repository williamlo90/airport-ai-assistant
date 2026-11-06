"""Independent CASE-04 domain race and preview consistency probes."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_draft_rules import setup
from airport_assistant.modules.attachments.service import AttachmentService
from airport_assistant.modules.drafts.models import DraftError, displayed_summary
from airport_assistant.modules.identity.auth import AuthError


def fixture():
    actor, conversation, draft, tx, outcomes, drafts = setup()
    aid = uuid4()
    tx.attachment = AsyncMock(return_value=None)
    tx.attachment_count = AsyncMock(return_value=0)
    tx.insert_attachment = AsyncMock(return_value=dict(id=aid,byte_size=8,mime_type='application/pdf',scan_status='clean'))
    # Persist the mock records so post-write verification observes stored data,
    # not an old preview or a fabricated adapter return value.
    async def insert(id,parent,body,mime,digest):
        row=dict(id=id,draft_id=parent.id,byte_size=len(body),mime_type=mime,scan_status='clean',
            content_hash=digest,resource_security_labels=list(parent.resource_security_labels),case_id=None)
        tx.attachment.return_value=row
        return row
    async def edit(value):
        tx.find_draft.return_value=value
        return value
    tx.insert_attachment.side_effect=insert
    tx.edit_draft.side_effect=edit
    tx.selected_attachment_context=AsyncMock(side_effect=lambda parent,ids:[
        {k:tx.attachment.return_value[k] for k in ('id','content_hash','resource_security_labels')}
        for id in ids if tx.attachment.return_value is not None and tx.attachment.return_value['id']==id])
    tx.attachment_parent=AsyncMock(side_effect=lambda row:dict(id=draft.id,version=tx.find_draft.return_value.version,
        payload_hash=tx.find_draft.return_value.payload_hash,resource_security_labels=list(tx.find_draft.return_value.resource_security_labels)))
    scan = AsyncMock()
    scan.clean.return_value = True
    return actor, conversation, draft, tx, outcomes, AttachmentService(drafts._repository, scan), scan, aid


def test_upload_summary_uses_the_language_recorded_on_its_returned_preview():
    async def scenario():
        actor, conv, draft, tx, _, service, _, aid = fixture()
        tx.find.return_value = replace(conv, language='en')
        _, updated = await service.upload(actor,draft.id,1,aid,b'%PDF-1.7','application/pdf')
        assert updated.displayed_summary == displayed_summary(updated.action_type,updated.payload,updated.destination,updated.language)
    asyncio.run(scenario())


def test_session_lost_during_scan_never_inserts_a_blob():
    async def scenario():
        actor, _, draft, tx, _, service, scan, aid = fixture()
        async def revoke(_):
            tx.session_active.return_value = False
            return True
        scan.clean.side_effect = revoke
        with pytest.raises(AuthError) as error:
            await service.upload(actor,draft.id,1,aid,b'%PDF-1.7','application/pdf')
        assert error.value.status == 401
        tx.insert_attachment.assert_not_awaited()
    asyncio.run(scenario())


def test_expiry_during_scan_never_inserts_a_blob():
    async def scenario():
        actor, _, draft, tx, _, service, scan, aid = fixture()
        async def expire(_):
            tx.now.return_value = draft.expires_at
            return True
        scan.clean.side_effect = expire
        with pytest.raises(DraftError) as error:
            await service.upload(actor,draft.id,1,aid,b'%PDF-1.7','application/pdf')
        assert error.value.status == 410
        tx.insert_attachment.assert_not_awaited()
    asyncio.run(scenario())
