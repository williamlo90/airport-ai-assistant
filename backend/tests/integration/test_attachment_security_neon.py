"""Independent CASE-04 race/adoption probes; isolated schema, controlled scanner."""
import os
from uuid import UUID
import pytest

from test_attachments import (
    target, lifecycle_target, commit_target, adoption_target, draft_target,
    conversation_target, auth_target, files, upload, BODY,
)
from test_staff_auth import run, row
from test_case_commit import prepared, confirm
from test_anonymous_adoption import issue, verify
from airport_assistant.infrastructure.database.attachment_repository import AttachmentSQL

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated Neon QA probes.'
)


def test_parent_edit_during_scan_invalidates_upload_without_storing_bytes(target):
    async def scenario():
        async with files(target) as (h, sender, scan):
            async with h.client() as c:
                draft = await prepared(h, sender, c)

                async def edit_during_scan(body):
                    result = await c.patch(
                        '/api/v1/action-drafts/' + draft['draft_id'], headers=h.headers(c),
                        json={'expected_version': draft['version'],
                              'payload': {**draft['payload'], 'summary': 'Changed while scanning'}},
                    )
                    assert result.status_code == 200, result.text
                    return True

                scan.clean.side_effect = edit_during_scan
                result = await upload(h, c, draft)
                assert result.status_code == 409, result.text
                assert result.json()['error']['code'] == 'VERSION_CONFLICT'
                assert row(target, 'SELECT count(*) FROM {s}.attachments WHERE draft_id=:id',
                           id=UUID(draft['draft_id']))[0] == 0
                current = await c.get('/api/v1/action-drafts/' + draft['draft_id'])
                assert current.json()['payload']['summary'] == 'Changed while scanning'
                assert current.json()['version'] == draft['version'] + 1
    run(scenario())


def test_anonymous_adoption_during_scan_revokes_original_upload_identity(target):
    async def scenario():
        async with files(target) as (h, sender, scan):
            async with h.client() as c:
                draft = await prepared(h, sender, c, anonymous=True)

                async def adopt_during_scan(body):
                    challenge, code = await issue(h, sender, c)
                    result = await verify(h, c, challenge, code)
                    assert result.status_code == 200, result.text
                    return True

                scan.clean.side_effect = adopt_during_scan
                result = await upload(h, c, draft)
                assert result.status_code == 401, result.text
                assert row(target, 'SELECT count(*) FROM {s}.attachments WHERE draft_id=:id',
                           id=UUID(draft['draft_id']))[0] == 0
    run(scenario())


def test_bound_anonymous_evidence_survives_adoption_with_original_provenance(target):
    async def scenario():
        async with files(target) as (h, sender, scan):
            async with h.client() as c:
                draft = await prepared(h, sender, c, anonymous=True)
                added = await upload(h, c, draft)
                assert added.status_code == 201, added.text
                attachment = added.json()['attachment']
                committed = await confirm(h, c, added.json()['draft'])
                assert committed.status_code == 201, committed.text
                before = row(target, 'SELECT uploader_session_id,created_session_id FROM {s}.attachments WHERE id=:id',
                             id=UUID(attachment['id']))
                challenge, code = await issue(h, sender, c)
                assert (await verify(h, c, challenge, code)).status_code == 200
                got = await c.get('/api/v1/attachments/' + attachment['id'] + '/download')
                assert got.status_code == 200 and got.content == BODY, got.text
                listing = await c.get('/api/v1/cases/' + committed.json()['case_id'] + '/attachments')
                assert listing.status_code == 200 and listing.json() == [attachment], listing.text
                assert row(target, 'SELECT uploader_session_id,created_session_id FROM {s}.attachments WHERE id=:id',
                           id=UUID(attachment['id'])) == before
    run(scenario())


def test_failed_preview_update_rolls_back_just_inserted_blob(target, monkeypatch):
    async def unavailable(self, proposed):
        return None

    async def scenario():
        async with files(target) as (h, sender, scan):
            async with h.client() as c:
                draft = await prepared(h, sender, c)
                monkeypatch.setattr(AttachmentSQL, 'edit_draft', unavailable)
                result = await upload(h, c, draft)
                assert result.status_code == 410, result.text
                assert row(target, 'SELECT count(*) FROM {s}.attachments WHERE draft_id=:id',
                           id=UUID(draft['draft_id']))[0] == 0
                assert row(target, 'SELECT version,payload_hash FROM {s}.action_drafts WHERE id=:id',
                           id=UUID(draft['draft_id'])) == (draft['version'], draft['payload_hash'])
    run(scenario())
