"""Case-email approval semantics outside provider/SQL execution."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import datetime,timezone,timedelta
import pytest
from pydantic import ValidationError
from airport_assistant.modules.agent.case_translation import CaseTranslationRequest,CaseTranslationApprovalParticipant
from airport_assistant.modules.agent.case_translation import context_hash
from airport_assistant.modules.agent.translation import TranslationApproval
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.drafts.models import DraftError


def test_case_translation_is_email_only_and_requires_a_different_language():
    value=dict(body='Please wait.',language='en',target_language='id',membership_id=uuid4(),expected_version=1,request_key=uuid4())
    assert CaseTranslationRequest.model_validate(value).target_language=='id'
    for extra in ({'target_language':'en'},{'recipient':'other@example.com'},{'conversation_id':uuid4()},{'channel':'chat'}):
        with pytest.raises(ValidationError):CaseTranslationRequest.model_validate(dict(value,**extra))


def test_ready_translation_cannot_adopt_an_existing_send_key():
    async def run():
        row=dict(id=uuid4(),state='ready',revision=2)
        participant=CaseTranslationApprovalParticipant(SimpleNamespace(),None,uuid4(),row,
            TranslationApproval(text='Reviewed.',approved=True,expected_revision=2),uuid4().hex)
        tx=SimpleNamespace(case_translation=AsyncMock(return_value=row))
        with pytest.raises(DraftError) as conflict:await participant.load(tx,dict(id=uuid4()),{})
        assert conflict.value.code=='IDEMPOTENCY_CONFLICT'
    asyncio.run(run())


def test_deadline_after_commit_is_unavailable_not_a_definite_send_rejection():
    participant=CaseTranslationApprovalParticipant(SimpleNamespace(clock=lambda:2),None,None,None,None,None)
    participant.deadline=1
    with pytest.raises(AgentError):participant.check()
    with pytest.raises(SourceFailure):participant.check(committed=True)


def test_timestamp_response_delay_cannot_extend_approval_deadline():
    async def run():
        clock=[0];now=datetime.now(timezone.utc)
        row=dict(id=uuid4(),state='ready',revision=2,source_context_hash=context_hash({}),expires_at=now+timedelta(seconds=10))
        async def delayed_now():
            clock[0]=20
            return now
        participant=CaseTranslationApprovalParticipant(SimpleNamespace(clock=lambda:clock[0]),None,None,row,
            TranslationApproval(text='Reviewed.',approved=True,expected_revision=2),uuid4().hex)
        tx=SimpleNamespace(case_translation=AsyncMock(return_value=row),now=delayed_now)
        with pytest.raises(AgentError):await participant.load(tx,None,{})
        assert participant.deadline==10
    asyncio.run(run())


def test_repeated_loads_keep_the_earliest_approval_deadline():
    async def run():
        clock=[0];now=datetime.now(timezone.utc)
        row=dict(id=uuid4(),state='ready',revision=2,source_context_hash=context_hash({}),expires_at=now+timedelta(seconds=30))
        participant=CaseTranslationApprovalParticipant(SimpleNamespace(clock=lambda:clock[0]),None,None,row,
            TranslationApproval(text='Reviewed.',approved=True,expected_revision=2),uuid4().hex)
        tx=SimpleNamespace(case_translation=AsyncMock(return_value=row),now=AsyncMock(return_value=now))
        await participant.load(tx,None,{})
        assert participant.deadline==30
        clock[0]=1
        await participant.load(tx,None,{})
        assert participant.deadline==30
    asyncio.run(run())
