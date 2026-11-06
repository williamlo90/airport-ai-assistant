"""Private upload and replay keep source review and current authority intact."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_private_draft_mutations import setup as base
from airport_assistant.modules.attachments.service import AttachmentService
from airport_assistant.modules.drafts.models import DraftError,seal,reviewed_summary,require_integrity
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure

BODY=b'%PDF-1.7\nSynthetic private evidence.'


def setup(allowed=True,private=True):
    identity,state,counts,tx,access,drafts=base(allowed=allowed,private=private)
    state['blobs']={}
    tx.private_attachments_ready=AsyncMock(return_value=True)
    tx.attachment=AsyncMock(side_effect=lambda id:deepcopy(state['blobs'].get(id)))
    tx.attachment_count=AsyncMock(side_effect=lambda id:len(state['blobs']))
    async def insert(id,draft,body,mime,digest):
        counts['writes']+=1
        row=dict(id=id,draft_id=draft.id,byte_size=len(body),mime_type=mime,scan_status='clean',content_hash=digest,
            case_id=None,resource_security_labels=list(draft.resource_security_labels))
        state['blobs'][id]=row
        return deepcopy(row)
    tx.insert_attachment=AsyncMock(side_effect=insert)
    tx.attachment_parent=AsyncMock(side_effect=lambda row:dict(id=state['draft'].id,version=state['draft'].version,
        payload_hash=state['draft'].payload_hash,resource_security_labels=list(state['draft'].resource_security_labels)))
    tx.selected_attachment_context.side_effect=lambda parent,ids:[
        {k:deepcopy(state['blobs'][id][k]) for k in ('id','content_hash','resource_security_labels')}
        for id in ids if id in state['blobs']]
    scanner=AsyncMock();scanner.clean.return_value=True
    service=AttachmentService(drafts._repository,scanner,access)
    return identity,state,counts,tx,access,scanner,service


def upload(f,key,version=None):
    identity,state,_,_,_,_,service=f
    return asyncio.run(service.upload(identity,state['draft'].id,version or state['draft'].version,key,BODY,'application/pdf'))


def test_private_source_review_survives_upload_and_actual_receipt_replay():
    f=setup();identity,state,counts,tx,access,scanner,service=f
    original=state['draft'];key=uuid4()
    meta,updated=upload(f,key)
    require_integrity(updated)
    assert updated.source_evidence==original.source_evidence and updated.resource_security_labels==original.resource_security_labels
    assert updated.payload.attachment_ids==(meta['id'],) and meta['id'] in state['blobs']
    assert updated.payload_hash!=original.payload_hash and updated.version==original.version+1
    assert updated.displayed_summary==reviewed_summary(updated.action_type,updated.payload,updated.destination,updated.language,
        updated.case_id,source_evidence=updated.source_evidence)
    assert access.allows.await_count==2
    assert upload(f,key,version=1)==(meta,updated)
    assert scanner.clean.await_count==1 and len(state['blobs'])==1


def test_denied_private_preflight_never_invokes_scanner_or_stores_bytes():
    f=setup(allowed=False)
    with pytest.raises(AuthError):upload(f,uuid4())
    f[5].clean.assert_not_awaited();assert not f[1]['blobs'] and f[2]['writes']==0


@pytest.mark.parametrize('change',['grant','session','expiry','edit','capacity'])
def test_revalidation_after_scan_rejects_changes_without_persistence(change):
    f=setup();identity,state,counts,tx,access,scanner,service=f
    async def changed(body):
        if change=='grant':access.allows.side_effect=None;access.allows.return_value=False
        elif change=='session':state['active']=False
        elif change=='expiry':state['now']=state['draft'].expires_at
        elif change=='edit':state['draft']=seal(replace(state['draft'],version=state['draft'].version+1))
        else:tx.attachment_count.side_effect=None;tx.attachment_count.return_value=3
        return True
    scanner.clean.side_effect=changed
    with pytest.raises((AuthError,DraftError)):upload(f,uuid4())
    assert not state['blobs'] and counts['writes']==0


def test_scan_does_not_extend_or_reuse_preflight_source_lease():
    f=setup();mono=[0];f[6]._private_content.clock=lambda:mono[0]
    async def scan(body):mono[0]=10;return True
    f[5].clean.side_effect=scan
    upload(f,uuid4())
    assert f[4].allows.await_count==2 and len(f[1]['blobs'])==1


def test_fault_after_file_write_rolls_back_preview_and_blob():
    f=setup();original=f[1]['draft']
    async def fail(value):raise RuntimeError('Controlled preview write fault')
    f[3].edit_draft.side_effect=fail
    with pytest.raises(RuntimeError):upload(f,uuid4())
    assert not f[1]['blobs'] and f[1]['draft']==original and f[2]['rollbacks']==1


def test_late_commit_is_unknown_and_same_key_recovery_does_not_rescan():
    f=setup();mono=[0];f[6]._private_content.clock=lambda:mono[0]
    f[1]['after_commit']=lambda:mono.__setitem__(0,2.01) if f[2]['writes']>=2 else None
    key=uuid4()
    with pytest.raises(SourceFailure):upload(f,key)
    assert len(f[1]['blobs'])==1
    _,saved=upload(f,key,version=1)
    assert saved.version==2 and f[5].clean.await_count==1


def test_replay_returns_current_selection_and_rechecks_current_grants():
    f=setup();key=uuid4();meta,updated=upload(f,key)
    payload=updated.payload.model_copy(update={'attachment_ids':()})
    f[1]['draft']=seal(replace(updated,payload=payload,version=3,displayed_summary=reviewed_summary(
        updated.action_type,payload,updated.destination,updated.language,updated.case_id,source_evidence=updated.source_evidence)))
    _,current=upload(f,key,version=1)
    assert current.payload.attachment_ids==() and current.version==3 and f[5].clean.await_count==1
    f[4].allows.side_effect=None;f[4].allows.return_value=False
    with pytest.raises(AuthError):upload(f,key,version=1)
    assert f[5].clean.await_count==1


def test_public_expiry_during_commit_is_unknown_and_expired_bytes_stay_hidden(monkeypatch):
    from types import SimpleNamespace
    from airport_assistant.modules.attachments import service as module
    f=setup(private=False);identity,state,counts,tx,access,scanner,service=f
    utc=[state['draft'].created_at]
    monkeypatch.setattr(module,'datetime',SimpleNamespace(now=lambda zone:utc[0]))
    def expired():
        if counts['writes']>=2:
            utc[0]=state['draft'].expires_at
            state['now']=state['draft'].expires_at
    state['after_commit']=expired
    # Mirror the existing RLS expiry boundary: pending expired attachments cannot
    # be read to manufacture a saved receipt. Missing access remains unknown.
    original_attachment=tx.attachment.side_effect
    original_selected=tx.selected_attachment_context.side_effect
    tx.attachment.side_effect=lambda id:None if state['now']>=state['draft'].expires_at else original_attachment(id)
    tx.selected_attachment_context.side_effect=lambda parent,ids:[] if state['now']>=state['draft'].expires_at else original_selected(parent,ids)
    key=uuid4()
    with pytest.raises(SourceFailure):upload(f,key)
    assert len(state['blobs'])==1 and state['draft'].version==2
    with pytest.raises((AuthError,DraftError)):upload(f,key,version=1)
    assert scanner.clean.await_count==1
