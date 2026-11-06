"""Byte and listing emission boundaries; no actual scanner/provider/DB."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.attachments.service import AttachmentService
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.source.models import SourceFailure
from test_private_content import actor, records


def setup(private=True, grant=True):
    identity, id = actor(), uuid4()
    body = b'%PDF-synthetic-private-evidence'
    file = dict(id=id, draft_id=uuid4(), byte_size=len(body), mime_type='application/pdf',
        scan_status='clean', content_hash=hashlib.sha256(body).hexdigest(), case_id=uuid4(),
        resource_security_labels=records()[:1] if private else [])
    parent = dict(id=file['case_id'], version=1,
                  resource_security_labels=records()[1:] if private else [])
    active = False
    async def attachment(id, *, body=False):
        return {**deepcopy(file), **({'body': globals_body} if body else {})}
    globals_body = body
    tx = SimpleNamespace(session_active=AsyncMock(return_value=True),
        private_attachments_ready=AsyncMock(return_value=True),
        attachment=AsyncMock(side_effect=attachment),
        attachment_parent=AsyncMock(side_effect=lambda row: deepcopy(parent)),
        find_case=AsyncMock(side_effect=lambda id: deepcopy(parent)),
        case_attachments=AsyncMock(side_effect=lambda id: [deepcopy(file)]))
    class Repository:
        @asynccontextmanager
        async def transaction(self, context):
            nonlocal active
            active = True
            try:
                yield tx
            finally:
                active = False
    async def allows(**kwargs):
        assert not active
        assert len(kwargs['labels'].labels) == 2
        if isinstance(grant, Exception):
            raise grant
        return grant
    access = SimpleNamespace(allows=AsyncMock(side_effect=allows))
    service = AttachmentService(Repository(), private_access=access)
    return identity, id, body, file, parent, tx, access, service


@pytest.mark.parametrize('grant', [False, SourceFailure(503)])
def test_source_denial_never_loads_blob_bytes(grant):
    identity, id, body, file, parent, tx, access, service = setup(grant=grant)
    with pytest.raises((AuthError, SourceFailure)):
        asyncio.run(service.download(identity, id))
    assert all(not call.kwargs.get('body') for call in tx.attachment.await_args_list)


def test_positive_download_checks_composite_scopes_and_preserves_exact_bytes():
    identity, id, body, file, parent, tx, access, service = setup()
    assert asyncio.run(service.download(identity, id)) == (body, 'application/pdf')
    access.allows.assert_awaited_once()
    assert sum(bool(c.kwargs.get('body')) for c in tx.attachment.await_args_list) == 1


@pytest.mark.parametrize('private', [False, True])
def test_parent_classification_or_version_change_while_blob_loads_is_not_emitted(private):
    identity, id, body, file, parent, tx, access, service = setup(private=private)
    original = tx.attachment.side_effect
    async def changed(id, *, body=False):
        result = await original(id, body=body)
        if body:
            parent['version'] += 1
            parent['resource_security_labels'] = records()
        return result
    tx.attachment.side_effect = changed
    with pytest.raises(DraftError) as error:
        asyncio.run(service.download(identity, id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'


def test_session_revocation_during_source_await_prevents_byte_fetch():
    identity, id, body, file, parent, tx, access, service = setup()
    async def revoked(**kwargs):
        tx.session_active.return_value = False
        return True
    access.allows.side_effect = revoked
    with pytest.raises(AuthError) as error:
        asyncio.run(service.download(identity, id))
    assert error.value.status == 401
    assert all(not call.kwargs.get('body') for call in tx.attachment.await_args_list)


def test_slow_parent_lookup_expires_check_before_bytes():
    identity, id, body, file, parent, tx, access, service = setup()
    ticks = iter([0, 3])
    service._private_content.clock = lambda: next(ticks)
    with pytest.raises(SourceFailure):
        asyncio.run(service.download(identity, id))
    assert all(not call.kwargs.get('body') for call in tx.attachment.await_args_list)


@pytest.mark.parametrize('failure', ['parent_missing', 'malformed_labels', 'not_clean'])
def test_missing_parent_bad_labels_or_unclean_file_is_concealed(failure):
    identity, id, body, file, parent, tx, access, service = setup()
    if failure == 'parent_missing':
        tx.attachment_parent.side_effect = None
        tx.attachment_parent.return_value = None
    elif failure == 'malformed_labels':
        file['resource_security_labels'] = None
    else:
        file['scan_status'] = 'pending'
    with pytest.raises(AuthError) as error:
        asyncio.run(service.download(identity, id))
    assert error.value.status == 404
    access.allows.assert_not_awaited()


@pytest.mark.parametrize('failure', ['checksum', 'mime'])
def test_unexpected_body_or_metadata_snapshot_is_not_emitted(failure):
    identity, id, body, file, parent, tx, access, service = setup()
    original = tx.attachment.side_effect
    async def changed(id, *, body=False):
        result = await original(id, body=body)
        if body:
            result.update({'body': b'CHANGED'} if failure == 'checksum' else {'mime_type': 'image/jpeg'})
        return result
    tx.attachment.side_effect = changed
    with pytest.raises(DraftError):
        asyncio.run(service.download(identity, id))


def test_list_authorizes_entire_case_and_files_but_exposes_only_public_file_metadata():
    identity, id, body, file, parent, tx, access, service = setup()
    result = asyncio.run(service.list_case(identity, file['case_id']))
    assert result == [dict(id=id, byte_size=len(body), mime_type='application/pdf', scan_status='clean')]
    access.allows.assert_awaited_once()


def test_denied_or_changed_case_cannot_emit_list_ids():
    identity, id, body, file, parent, tx, access, service = setup()
    async def changed(**kwargs):
        parent['version'] += 1
        return True
    access.allows.side_effect = changed
    with pytest.raises(DraftError):
        asyncio.run(service.list_case(identity, file['case_id']))


def test_staff_membership_uses_current_object_acl_without_customer_grant_substitution():
    identity, id, body, file, parent, tx, access, service = setup()
    staff = SimpleNamespace(session=replace(identity.session,kind='staff'),
        memberships=(object(),), database_context=identity.database_context)
    assert asyncio.run(service.download(staff, id)) == (body, 'application/pdf')
    access.allows.assert_not_awaited()
    tx.attachment_parent.side_effect = None
    tx.attachment_parent.return_value = None  # Represents current staff RLS denying that parent.
    with pytest.raises(AuthError):
        asyncio.run(service.download(staff, id))


def test_original_grant_deadline_is_checked_before_loading_blob_bytes():
    identity,id,body,file,parent,tx,access,service=setup()
    mono=[0];calls=[0];service._private_content.clock=lambda:mono[0]
    async def granted(**kwargs):mono[0]=1.9;return True
    async def current_parent(row):
        calls[0]+=1
        if calls[0]==2:mono[0]=2.01
        return deepcopy(parent)
    access.allows.side_effect=granted;tx.attachment_parent.side_effect=current_parent
    with pytest.raises(SourceFailure):asyncio.run(service.download(identity,id))
    assert all(not call.kwargs.get('body') for call in tx.attachment.await_args_list)
