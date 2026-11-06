"""CHAT-01 tamper, scope and expiry protection without a database."""
from dataclasses import replace
from uuid import uuid4
import pytest
from test_draft_rules import setup
from airport_assistant.modules.conversations.events import EventCursor
from airport_assistant.modules.conversations.service import ConversationError


def test_cursor_roundtrip_scope_and_authentication(monkeypatch):
    actor,conv,*_=setup();codec=EventCursor(b's'*48);scope=codec.scope(actor,conv.id,20)
    token=codec.issue(scope,19)
    assert codec.read(token,scope)==19
    bad=[token[:-3]+'abc','!',token[:10],token+'!', 'x'*2049]
    for value in bad:
        with pytest.raises(ConversationError):codec.read(value,scope)
    for changed in (codec.scope(actor,uuid4(),20),codec.scope(actor,conv.id,21),codec.scope(replace(actor,session=replace(actor.session,id=uuid4())),conv.id,20)):
        with pytest.raises(ConversationError):codec.read(token,changed)
    with pytest.raises(ConversationError):EventCursor(b'd'*48).read(token,scope)
    import airport_assistant.modules.conversations.events as events
    monkeypatch.setattr(events.time,'time',lambda:10**12)
    with pytest.raises(ConversationError):codec.read(token,scope)
