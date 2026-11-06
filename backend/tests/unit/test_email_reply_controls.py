"""Email control extraction excludes quotations and never persists quoted codes."""
from dataclasses import replace
from uuid import UUID
import asyncio
from airport_assistant.modules.mail.replies import text_body,ReplyTransport,digest
from airport_assistant.modules.mail.inbox import ReceivedContent

ID=UUID('11111111-1111-4111-8111-111111111111')
SECRET=b'unit-test-only-reply-secret-value'


def test_quoted_html_is_not_continuation_intent():
    assert text_body(None,'<blockquote>CONTINUE SAME ISSUE</blockquote><p>Thank you</p>')=='Thank you'


def test_quoted_code_redacted_without_verifying():
    async def scenario():
        class Transport:
            async def receive(self,snapshot):
                return ReceivedContent('a@example.com','b@example.com','reply','Thank you\n> VERIFY '+str(ID)+' 123456',None,None,None,None,None,0,None)
        result=await ReplyTransport(Transport(),SECRET).receive({})
        assert '123456' not in result.text
        assert result.verification_challenge_id is None
    asyncio.run(scenario())


def test_first_line_code_becomes_only_digest():
    async def scenario():
        class Transport:
            async def receive(self,snapshot):
                return ReceivedContent('a@example.com','b@example.com','reply','VERIFY '+str(ID)+' 123456',None,None,None,None,None,0,None)
        result=await ReplyTransport(Transport(),SECRET).receive({})
        assert result.verification_response_hash==digest(SECRET,ID,'123456')
        assert '123456' not in result.text and result.html is None
    asyncio.run(scenario())


def test_encoded_html_quote_is_discarded_without_verifying():
    async def scenario():
        class Transport:
            async def receive(self,snapshot):
                return ReceivedContent('a@example.com','b@example.com','reply',None,'<p>Thanks</p><blockquote>VERIFY&nbsp;'+str(ID)+'&nbsp;<b>123456</b></blockquote>',None,None,None,None,0,None)
        result=await ReplyTransport(Transport(),SECRET).receive({})
        assert result.html is None and result.text=='Thanks'
        assert result.verification_challenge_id is None
    asyncio.run(scenario())


def test_unknown_challenge_send_is_recorded_and_never_reposted():
    from types import SimpleNamespace
    from airport_assistant.modules.mail.replies import ChallengeWorker,code
    from airport_assistant.modules.mail.service import SendResult
    async def scenario():
        class Repository:
            state='queued'
            async def challenge(self,lease):
                return dict(id=str(ID),code_hash=digest(SECRET,ID,code(SECRET,ID)),send_state=self.state,app_message_id=str(ID),account_id=str(ID),sender='support@example.com',recipient='customer@example.com',reply_address='reply@example.com',provider_account_ref='test')
            async def reserve(self,lease):self.state='reserved';return True
            async def sent(self,lease,result):self.state=result.state;return True
        class Transport:
            calls=0
            async def send(self,envelope):self.calls+=1;return SendResult('delivery_unknown')
        repo=Repository();transport=Transport();worker=ChallengeWorker(repo,transport,SECRET);lease=SimpleNamespace(tenant_id=ID)
        assert (await worker(lease)).state=='unknown'
        assert repo.state=='delivery_unknown'
        assert (await worker(lease)).state=='succeeded'
        assert transport.calls==1
    asyncio.run(scenario())


def test_reply_configuration_requires_receiving():
    import pytest
    from airport_assistant.mail_config import MailSettings
    with pytest.raises(ValueError):MailSettings(reply_enabled=True)


def test_client_quote_containers_do_not_supply_intent():
    assert text_body(None,'<div class="gmail_quote"><div>CONTINUE SAME ISSUE</div></div><p>Thanks</p>')=='Thanks'
    assert text_body(None,'<p>Thanks</p><div id="divRplyFwdMsg">Original message</div><p>CONTINUE SAME ISSUE</p>')=='Thanks'
