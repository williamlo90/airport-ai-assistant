"""UI routing hints never substitute for the publication gate."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from test_staff_recovery import Store
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.conversations.case_email import CaseEmails
from airport_assistant.modules.identity.auth import AuthError


def setup():
    db=Store();db.case.update(status='in_progress',assigned_membership_id=db.member)
    principal=uuid4()
    recipient=dict(tenant_id=db.actor.session.tenant_id,principal_id=principal,owner_principal_id=principal,
        tenant_active=True,principal_active=True,participant_active=True,contact_id=uuid4(),account_id=uuid4(),
        verified=True,account_active=True,recipient='customer@example.com')
    db.case_email_ready=AsyncMock(return_value=True)
    db.case_email_context=AsyncMock(return_value=dict(recipient=recipient))
    db.case_correspondence_ancestry=AsyncMock(return_value=[])
    return db,recipient,CaseEmails(StaffMessages(db,private_disclosure=True))


@pytest.mark.parametrize('reason',['available','missing_contact','revoked_recipient','closed','unassigned','old_schema'])
def test_capability_is_advisory_and_conservative(reason):
    async def run():
        db,recipient,service=setup()
        if reason=='missing_contact':recipient['contact_id']=None
        if reason=='revoked_recipient':recipient['participant_active']=False
        if reason=='closed':db.case['status']='closed'
        if reason=='unassigned':db.case['assigned_membership_id']=None
        if reason=='old_schema':db.case_email_ready.return_value=False
        result=await service.capability(db.actor,db.case_id,db.member)
        assert result['email_available'] is (reason=='available')
        assert set(result)=={'case_id','case_version','membership_id','email_available'}
        assert 'recipient' not in result
    asyncio.run(run())


def test_revoked_selected_workspace_cannot_read_an_email_capability():
    async def run():
        db,recipient,service=setup();db.visible=False
        with pytest.raises(AuthError):await service.capability(db.actor,db.case_id,db.member)
        db.case_email_context.assert_not_awaited()
    asyncio.run(run())
