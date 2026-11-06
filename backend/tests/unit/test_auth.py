"""Offline authentication policy/crypto boundaries, without delivery or DB I/O."""

import asyncio
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.auth_config import load_auth_settings
from airport_assistant.config import ConfigurationError
from airport_assistant.modules.identity.auth import AuthService,AuthPolicy,AuthError,Session,SessionCredentials,VerificationMessage,normalized_contact


def subject(sender=None,policy=AuthPolicy()):
    tenant=uuid4();token="A"*43;csrf="B"*43
    repository=AsyncMock()
    service=AuthService(repository,b"k"*48,policy,sender)
    session=Session(uuid4(),tenant,None,"anonymous",datetime.now(timezone.utc)+timedelta(hours=1),service.digest("csrf",tenant,csrf))
    repository.session.return_value=session
    repository.issue.return_value="ok"
    return service,repository,session,token,csrf


def test_digest_is_keyed_tenant_and_purpose_separated():
    service,_,session,_,_=subject()
    first=service.digest("session",session.tenant_id,"input")
    assert len(first)==64 and "input" not in first
    assert first!=service.digest("csrf",session.tenant_id,"input")
    assert first!=service.digest("session",uuid4(),"input")
    with pytest.raises(ValueError):AuthService(AsyncMock(),b"short")


def test_normalization_preserves_local_part_dots_plus_and_case():
    assert normalized_contact("First.Last+tag@EXAMPLE.COM")=="First.Last+tag@example.com"
    assert normalized_contact("firstlast@example.com")!="First.Last+tag@example.com"


@pytest.mark.parametrize("value",["bad","a\r\nBcc:x@example.com","x@@example.com"])
def test_invalid_contact_has_no_input_in_error(value):
    with pytest.raises(AuthError) as error:normalized_contact(value)
    assert error.value.code=="VALIDATION_ERROR" and value not in str(error.value)


def test_plaintext_values_are_absent_from_domain_reprs():
    _,_,session,_,_=subject()
    message=VerificationMessage(uuid4(),session.tenant_id,"secret-sentinel@example.com","87654321")
    assert "secret-sentinel" not in repr(message) and "87654321" not in repr(message)
    assert "raw-secret-sentinel" not in repr(SessionCredentials(session,"raw-secret-sentinel","raw-secret-sentinel"))
    assert session.csrf_hash not in repr(session)


def test_unconfigured_sender_never_reports_fake_delivery():
    service,repository,session,token,csrf=subject()
    with pytest.raises(AuthError) as error:
        asyncio.run(service.request_challenge(session.tenant_id,token,csrf,"one@example.com","127.0.0.1"))
    assert (error.value.status,error.value.code)==(503,"VERIFICATION_UNAVAILABLE")
    repository.issue.assert_not_awaited()


def test_sender_failure_cancels_owned_challenge_and_redacts_exception():
    sender=AsyncMock();sender.send.side_effect=RuntimeError("secret-sentinel")
    service,repository,session,token,csrf=subject(sender)
    with pytest.raises(AuthError) as error:
        asyncio.run(service.request_challenge(session.tenant_id,token,csrf,"one@example.com","127.0.0.1"))
    assert error.value.status==503 and "secret-sentinel" not in str(error.value)
    repository.cancel.assert_awaited_once()
    message=sender.send.await_args.args[0]
    args=repository.issue.await_args.args
    assert message.code not in args and token not in args and csrf not in args
    assert len(message.code)==8


def test_sender_timeout_is_bounded_and_retains_pending_challenge():
    sender=AsyncMock()
    async def stall(message):await asyncio.Event().wait()
    sender.send.side_effect=stall
    service,repository,session,token,csrf=subject(sender,AuthPolicy(delivery_timeout=0.01))
    result=asyncio.run(service.request_challenge(session.tenant_id,token,csrf,"one@example.com","127.0.0.1"))
    assert result==sender.send.await_args.args[0].challenge_id
    repository.cancel.assert_not_awaited()


def test_wrong_csrf_never_reaches_challenge_repository():
    service,repository,session,token,_=subject(AsyncMock())
    with pytest.raises(AuthError) as error:
        asyncio.run(service.request_challenge(session.tenant_id,token,"X"*43,"one@example.com","127.0.0.1"))
    assert error.value.status==403
    repository.issue.assert_not_awaited()


def test_auth_settings_redact_invalid_key_and_remain_explicit(tmp_path,monkeypatch):
    monkeypatch.delenv("SESSION_SECRET",raising=False)
    file=tmp_path/".env";file.write_text("SESSION_SECRET=secret-sentinel\n",encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:load_auth_settings(file)
    assert "SESSION_SECRET" in str(error.value) and "secret-sentinel" not in str(error.value)
