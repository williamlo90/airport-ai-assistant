"""Cursor cross-purpose/scope isolation and exact microsecond keyset positions."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import pytest
from airport_assistant.modules.cases.queue import QueueCursor,stamp
from airport_assistant.modules.cases.capabilities import AssigneeCursor
from airport_assistant.modules.drafts.models import DraftError


def test_exact_microseconds_scope_binding_fixed_expiry_and_cross_purpose(monkeypatch):
    monkeypatch.setattr('airport_assistant.modules.cases.queue.time.time',lambda:1000)
    codec=QueueCursor(b'x'*32);dt=datetime(2026,11,4,10,5,3,987654,tzinfo=timezone.utc);position=(dt-timedelta(microseconds=1),uuid4())
    scope=['tenant','session','actor','member',1,'org','fingerprint',None,'all',None,'created_asc',25]
    token=codec.issue(scope,dt,position,1900)
    assert len(token)<2048 and codec.read(token,scope)==(dt,position,1900)
    for i in range(len(scope)):
        other=scope.copy();other[i]='changed'
        with pytest.raises(DraftError):codec.read(token,other)
    with pytest.raises(DraftError):codec.read(token+'x',scope)
    other_purpose=AssigneeCursor(b'x'*32).issue(scope,position[1])
    with pytest.raises(DraftError):codec.read(other_purpose,scope)
    # Reissuing subsequent page preserves original expiry rather than extending it.
    monkeypatch.setattr('airport_assistant.modules.cases.queue.time.time',lambda:1850)
    values=codec.read(token,scope);last=codec.issue(scope,values[0],values[1],values[2])
    monkeypatch.setattr('airport_assistant.modules.cases.queue.time.time',lambda:1900)
    with pytest.raises(DraftError):codec.read(last,scope)


@pytest.mark.parametrize('bad',['','x'*2049,'☃','not.a.cursor'])
def test_invalid_tokens_fail_without_fallback(bad):
    with pytest.raises(DraftError):QueueCursor(b'x'*32).read(bad,[])


def test_position_cannot_exceed_watermark():
    dt=datetime.now(timezone.utc)
    codec=QueueCursor(b'x'*32)
    with pytest.raises(DraftError):codec.read(codec.issue([],dt,(dt+timedelta(microseconds=1),uuid4()),9999999999),[])
