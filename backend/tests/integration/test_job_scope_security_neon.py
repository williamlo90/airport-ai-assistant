"""Independent scoped scheduler rejection and concurrent isolation on Neon."""
import asyncio
import os
from uuid import uuid4
import pytest
from psycopg import sql
from test_job_scope import (
    target, auth_mail_target, reply_target, inbox_target, delivery_target, mail_target,
    worker_target, publication_target, handoff_target, events_target, files_target,
    lifecycle_target, commit_target, adoption_target, draft_target, conversation_target,
    auth_target, inbox_app, scheduler, isolate_jobs,
)
from test_job_worker import enqueue, expire
from test_staff_auth import run, row
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.jobs.service import Outcome

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated job scope QA.')


def snapshot(t, id):
    return row(t, 'SELECT row_to_json(j) FROM {s}.outbox_jobs j WHERE id=:id', id=id)[0]


def test_invalid_sql_scope_fails_before_foreign_expired_or_queued_jobs_change(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as q:
            async with h.client() as a, h.client() as b:
                old = await enqueue(h, sender, a, target); ready = await enqueue(h, sender, b, target)
                q.job_scope_ids = (old,); lease = await q.claim(uuid4(), ('case_created',), 30); expire(target, old)
                before = [snapshot(target, id) for id in (old, ready)]
                for raw in ('null', '{}', '[null]', '[1]', '["not-a-uuid"]'):
                    with pytest.raises(DatabaseError) as error:
                        async with q._database.transaction() as c:
                            await c.execute("SELECT set_config('app.job_scope_ids',%s,true)", (raw,))
                            await c.execute("SELECT set_config('app.mail_accounts',%s,true)", ('["test-account"]',))
                            await c.execute(sql.SQL('SELECT {}.job_claim(%s,%s,%s)').format(sql.Identifier(target['schema'])), (uuid4(), ['case_created'], 30))
                    assert error.value.sqlstate.startswith('22')
                    assert [snapshot(target, id) for id in (old, ready)] == before
    run(scenario())


def test_concurrent_disjoint_scopes_and_empty_scope_leave_foreign_lease_unchanged(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as a, scheduler(target) as b:
            async with h.client() as ca, h.client() as cb, h.client() as cf:
                first = await enqueue(h, sender, ca, target); second = await enqueue(h, sender, cb, target); foreign = await enqueue(h, sender, cf, target)
                a.job_scope_ids = (foreign,); stale = await a.claim(uuid4(), ('case_created',), 30); expire(target, foreign)
                before = snapshot(target, foreign)
                a.job_scope_ids = (first,); b.job_scope_ids = (second,)
                leases = await asyncio.gather(a.claim(uuid4(), ('case_created',), 30), b.claim(uuid4(), ('case_created',), 30))
                assert [v.id for v in leases] == [first, second]
                assert snapshot(target, foreign) == before
                for q, lease in zip((a, b), leases):assert await q.finish(lease, Outcome('succeeded', 'OK'))
                a.job_scope_ids = (); b.job_scope_ids = ()
                assert await a.claim(uuid4(), ('case_created',), 30) is None
                assert await b.claim(uuid4(), ('case_created',), 30) is None
                assert snapshot(target, foreign) == before
                b.job_scope_ids = None
                assert await b.claim(uuid4(), ('case_created',), 30) is None
                assert snapshot(target, foreign)['state'] == 'unknown'
    run(scenario())


def test_invalid_python_scope_preserves_fail_closed_empty_scope(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as q:
            async with h.client() as c:
                id = await enqueue(h, sender, c, target); before = snapshot(target, id)
                q.job_scope_ids = ()
                for invalid in ([None], [str(id)], [id]*1001):
                    with pytest.raises(ValueError):q.job_scope_ids = invalid
                    assert q.job_scope_ids == ()
                    assert await q.claim(uuid4(), ('case_created',), 30) is None
                    assert snapshot(target, id) == before
    run(scenario())
