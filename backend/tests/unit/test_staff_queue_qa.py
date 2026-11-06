"""Independent queue service regressions with synthetic rows and no I/O."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from airport_assistant.modules.cases.queue import QueueCursor, StaffQueue
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


class QueueFixture:
    def __init__(self):
        self.member, self.queue = uuid4(), uuid4()
        self.identity = SimpleNamespace(database_context=None, session=SimpleNamespace(
            kind='staff', tenant_id=uuid4(), id=uuid4(), principal_id=uuid4()))
        self.workspace = dict(membership_version=1, organization=dict(id=str(uuid4()), name='Org'),
                              queues=[dict(id=str(self.queue), name='Queue')])
        self.clock = datetime(2026, 11, 4, 1, 2, 3, 456789, tzinfo=timezone.utc)
        self.rows = [dict(id=UUID(int=i), created_at=self.clock - timedelta(microseconds=1),
                         queue_id=self.queue) for i in range(1, 5)]
        self.calls = []
        self.codec = QueueCursor(b'q' * 32)
        self.service = StaffQueue(self, self.codec)

    @asynccontextmanager
    async def transaction(self, context):
        yield self

    async def session_active(self):
        return True

    async def queue_workspace(self, membership, queue_id):
        assert membership == self.member
        self.calls.append('scope')
        return self.workspace

    async def queue_clock(self):
        return self.clock

    async def queue_page(self, organization, queues, membership, view, status, watermark, position, limit):
        self.calls.append(dict(organization=organization, queues=queues, membership=membership,
                               view=view, status=status, watermark=watermark, position=position, limit=limit))
        return [r for r in self.rows if r['queue_id'] in queues and r['created_at'] <= watermark
                and (position is None or (r['created_at'], r['id']) > position)][:limit]

    def page(self, **kwargs):
        return asyncio.run(self.service.page(self.identity, self.member, **kwargs))


def test_each_page_preserves_initial_microsecond_watermark_and_fixed_expiry(monkeypatch):
    now = [1000]
    monkeypatch.setattr('airport_assistant.modules.cases.queue.time.time', lambda: now[0])
    f = QueueFixture()
    first = f.page(limit=1)
    initial = f.clock
    now[0] = 1500
    f.clock += timedelta(minutes=1)
    second = f.page(limit=1, cursor=first['next_cursor'])
    assert second['watermark'] == initial
    assert second['observed_at'] == f.clock
    assert second['items'][0]['id'] == UUID(int=2)
    assert f.calls[-1]['position'] == (initial - timedelta(microseconds=1), UUID(int=1))
    now[0] = 1900
    before = len(f.calls)
    with pytest.raises(DraftError) as error:
        f.page(limit=1, cursor=second['next_cursor'])
    assert error.value.status == 400
    assert f.calls[before:] == ['scope']


def test_full_authorized_queue_set_change_invalidates_specific_queue_cursor_without_version_change():
    f = QueueFixture()
    first = f.page(queue_id=f.queue, limit=1)
    f.workspace['queues'].append(dict(id=str(uuid4()), name='New queue'))
    before = len(f.calls)
    with pytest.raises(DraftError) as error:
        f.page(queue_id=f.queue, limit=1, cursor=first['next_cursor'])
    assert error.value.status == 400
    assert f.workspace['membership_version'] == 1 and f.calls[before:] == ['scope']


def test_revoked_scope_is_concealed_before_bad_cursor_and_no_query_runs():
    f = QueueFixture()
    f.workspace = None
    with pytest.raises(AuthError) as error:
        f.page(cursor='malformed')
    assert error.value.status == 404 and f.calls == ['scope']


def test_large_scope_keeps_cursor_bounded_and_echoes_full_metadata_with_exact_query_scope():
    f = QueueFixture()
    f.workspace['queues'].extend(dict(id=str(uuid4()), name='Extra') for _ in range(500))
    first = f.page(queue_id=f.queue, view='mine', status='new', limit=1)
    assert len(first['next_cursor']) <= 2048
    assert first['queues'] == f.workspace['queues'] and first['queue_id'] == f.queue
    assert f.calls[-1]['queues'] == [f.queue]
    assert f.calls[-1]['membership'] == f.member
    assert f.calls[-1]['view'] == 'mine' and f.calls[-1]['status'] == 'new'
    assert f.calls[-1]['limit'] == 2
    assert 'total' not in first and 'count' not in first


def test_live_refresh_is_required_for_behind_cursor_rows_and_newer_timestamps_are_excluded():
    f = QueueFixture()
    first = f.page(limit=1)
    watermark = first['watermark']
    behind = dict(id=UUID(int=0), created_at=f.rows[0]['created_at'], queue_id=f.queue)
    newer = dict(id=UUID(int=99), created_at=watermark + timedelta(microseconds=1), queue_id=f.queue)
    f.rows = [behind, *f.rows, newer]
    following = f.page(limit=1, cursor=first['next_cursor'])
    assert following['items'][0]['id'] == UUID(int=2)
    assert f.page(limit=100)['items'][0] == behind
    assert newer not in f.page(limit=100)['items']
