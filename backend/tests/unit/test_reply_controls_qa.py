"""Independent control/quote privacy probes without provider or database access."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from airport_assistant.modules.mail.inbox import ReceivedContent
from airport_assistant.modules.mail.replies import ReplyTransport, text_body, digest

SECRET = b'qa-isolated-mail-reply-control-secret-32'


@pytest.mark.parametrize('markup', [
    '<blockquote>VERIFY&nbsp;{id}&nbsp;<b>123456</b></blockquote><p>Thank you</p>',
    '<blockquote>CONTINUE SAME ISSUE</blockquote><p>Thank you</p>',
    '<p>Thank you</p><blockquote><div>VERIFY {id} <span>123456</span></div></blockquote>',
])
def test_quoted_html_control_cannot_verify_reopen_or_persist_code(markup):
    async def scenario():
        cid = uuid4()
        content = ReceivedContent('customer@example.com', 'reply@airport.example', 'Re: verification', None,
            markup.format(id=cid), None, None, None, None, 0)
        underlying = AsyncMock(); underlying.receive.return_value = content
        result = await ReplyTransport(underlying, SECRET).receive({})
        assert result.html is None and result.text == 'Thank you'
        assert result.verification_challenge_id is None and result.verification_response_hash is None
        assert '123456' not in str(result.record()) and 'CONTINUE SAME ISSUE' not in result.text
    asyncio.run(scenario())


def test_plaintext_quoted_code_is_redacted_without_verifying():
    async def scenario():
        cid = uuid4()
        content = ReceivedContent('customer@example.com', 'reply@airport.example', 'Re: verification',
            'Thank you\n> VERIFY ' + str(cid) + ' 123456', None, None, None, None, None, 0)
        underlying = AsyncMock(); underlying.receive.return_value = content
        result = await ReplyTransport(underlying, SECRET).receive({})
        assert '123456' not in result.text and result.verification_challenge_id is None
    asyncio.run(scenario())


@pytest.mark.parametrize('html', [False, True])
def test_actual_first_line_code_is_bound_to_challenge_and_only_digest_is_retained(html):
    async def scenario():
        cid = uuid4(); command = 'VERIFY ' + str(cid) + ' 123456'
        content = ReceivedContent('customer@example.com', 'reply@airport.example', 'Re: verification',
            None if html else command, '<p>' + command + '</p>' if html else None, None, None, None, None, 0)
        underlying = AsyncMock(); underlying.receive.return_value = content
        result = await ReplyTransport(underlying, SECRET).receive({})
        assert result.verification_challenge_id == str(cid)
        assert result.verification_response_hash == digest(SECRET, cid, '123456')
        assert result.html is None and '123456' not in str(result.record())
    asyncio.run(scenario())
