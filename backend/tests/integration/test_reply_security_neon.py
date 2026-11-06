"""Independent MAIL-04 proof, lifecycle, capability and recovery probes."""
import asyncio
from dataclasses import replace
import os
from uuid import UUID, uuid4
import pytest
from psycopg import sql
from sqlalchemy import text
from test_email_replies import (
    target, inbox_target, delivery_target, mail_target, worker_target, publication_target,
    handoff_target, events_target, files_target, lifecycle_target, commit_target,
    adoption_target, draft_target, conversation_target, auth_target, isolate_jobs,
    scheduler, inbox_app, Fetcher, SECRET, setup_case, inbound,
)
from test_email_inbox import event, post, headers
from test_inbox_security_neon import ingress_for_two, KEY_B
from test_staff_auth import run, row, change, seed
from test_chat_handoff import operator, login, accept, meta, close
from test_job_worker import expire
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.replies import PostgresReplies
from airport_assistant.infrastructure.database.inbox import PostgresInbox
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.modules.mail.replies import ReplyWorker, ChallengeWorker, ReplyTransport, code
from airport_assistant.modules.mail.inbox import ReceivedContent, InboxWorker
from airport_assistant.modules.mail.service import SendResult
from airport_assistant.modules.jobs.service import JobWorker, Outcome

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in independent MAIL-04 Neon probes.')


def state(t, email):
    return row(t, 'SELECT state,reason,app_message_id FROM {s}.email_reply_state WHERE email_id=:e', e=email)


def published(t, email):
    return row(t, 'SELECT count(*) FROM {s}.messages WHERE email_source_id=:e', e=email)[0]


def worker(t, schedule, repo=None):
    return JobWorker(schedule, {'email_reply': ReplyWorker(repo or PostgresReplies(schedule._database, t['schema']), SECRET)})


async def stage(h, t, c, schedule, envelope, body, *, html=None, refs=None, delivered=None, auth=True, alias='airport-a'):
    provider = 'qa-reply-' + uuid4().hex; raw = event(id=provider)
    assert (await post(c, raw, headers(raw, key=KEY_B), alias)).status_code == 202 if alias != 'airport-a' else (await post(c, raw)).status_code == 202
    transport = Fetcher()
    async def receive(snapshot):
        return ReceivedContent(envelope.recipient, snapshot['inbound_address'], 'Re: request', body, html,
            '<' + uuid4().hex + '@customer.example>', refs, None, None, 0,
            {'dkim': 'pass', 'dmarc': 'pass'} if auth else None, delivered or envelope.reply_to)
    transport.receive = receive
    adapter = InboxWorker(PostgresInbox(schedule._database, t['schema']), PostgresMail(schedule._database, t['schema']), ReplyTransport(transport, SECRET), accounts=schedule.mail_accounts)
    assert await JobWorker(schedule, {'email_fetch': adapter}).run_once() == 'succeeded'
    return row(t, 'SELECT id FROM {s}.email_messages WHERE provider_message_id=:p', p=provider)[0]


async def challenge(h, t, c, schedule, envelope, transport, *, send=True):
    original = await inbound(h, t, c, schedule, envelope, 'Please check this original request.', auth=False)
    assert await worker(t, schedule).run_once() == 'succeeded'
    assert state(t, original)[0] == 'challenged'
    cid = row(t, 'SELECT id FROM {s}.email_reply_challenges WHERE email_id=:e', e=original)[0]
    notification = None
    if send:
        assert await JobWorker(schedule, {'email_verify_send': ChallengeWorker(PostgresReplies(schedule._database, t['schema']), transport, SECRET)}).run_once() == 'succeeded'
        notification = transport.sent[-1]
    return original, cid, notification


def test_thread_conflicts_never_fallback_and_genuine_account_rfc_can_correlate(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as a, h.client() as b:
                case_a, envelope_a, _ = await setup_case(h, sender, target, a, schedule)
                case_b, _, _ = await setup_case(h, sender, target, b, schedule)
                rfc_a, rfc_b = '<qa-a@provider.example>', '<qa-b@provider.example>'
                for case_id, value in ((case_a, rfc_a), (case_b, rfc_b)):
                    change(target, 'UPDATE {s}.email_messages SET rfc_message_id=:r WHERE case_id=:c', r=value, c=UUID(case_id))
                conflict = await stage(h, target, a, schedule, envelope_a, 'Conflicting thread', refs=rfc_b)
                assert await worker(target, schedule).run_once() == 'succeeded'
                assert published(target, conflict) == 0 and state(target, conflict)[0] in ('review', 'rejected')
                forged = envelope_a.reply_to.split('+r-')[0] + '+r-' + 'x'*32 + '@' + envelope_a.reply_to.rsplit('@', 1)[1]
                invalid = await stage(h, target, a, schedule, envelope_a, 'Do not fall back', delivered=forged, refs=rfc_a)
                assert await worker(target, schedule).run_once() == 'succeeded'
                assert published(target, invalid) == 0
                legitimate = await stage(h, target, a, schedule, envelope_a, 'Actual RFC reply', delivered='reply@airport.example', refs=rfc_a)
                assert await worker(target, schedule).run_once() == 'succeeded'
                assert published(target, legitimate) == 1
                assert row(target, 'SELECT case_id FROM {s}.messages WHERE email_source_id=:e', e=legitimate)[0] == UUID(case_a)
                unknown = await stage(h, target, a, schedule, envelope_a, 'Subject cannot select CASE', delivered='reply@airport.example')
                assert await worker(target, schedule).run_once() == 'succeeded'
                assert published(target, unknown) == 0
    run(scenario())


@pytest.mark.parametrize('revocation', ['participant', 'contact', 'principal', 'account'])
def test_current_rights_revoked_after_service_snapshot_prevent_publication(target, revocation):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                case_id, envelope, _ = await setup_case(h, sender, target, c, schedule)
                email = await inbound(h, target, c, schedule, envelope, 'Must remain private after revocation.')
                principal, contact_id = row(target, 'SELECT owner_principal_id,followup_contact_id FROM {s}.cases WHERE id=:c', c=UUID(case_id))
                before = row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0]
                verified_at = row(target, 'SELECT verified_at FROM {s}.contacts WHERE id=:id', id=contact_id)[0]
                commands = {
                    'participant': ("UPDATE {s}.case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id", "UPDATE {s}.case_participants SET revoked_at=NULL WHERE case_id=:id", UUID(case_id)),
                    'contact': ("UPDATE {s}.contacts SET verified_at=NULL WHERE id=:id", "UPDATE {s}.contacts SET verified_at=:verified WHERE id=:id", contact_id),
                    'principal': ("UPDATE {s}.principals SET active=false WHERE id=:id", "UPDATE {s}.principals SET active=true WHERE id=:id", principal),
                    'account': ("UPDATE {s}.email_accounts SET active=false WHERE id=:id", "UPDATE {s}.email_accounts SET active=true WHERE id=:id", target['mail_account']),
                }
                revoke, restore, id = commands[revocation]
                repo = PostgresReplies(schedule._database, target['schema']); original = repo.snapshot
                async def snapshot(lease):
                    value = await original(lease); assert value['eligible'] is True
                    change(target, revoke, id=id)
                    return value
                repo.snapshot = snapshot
                try:
                    assert await worker(target, schedule, repo).run_once() in ('succeeded', 'retry', 'blocked')
                    assert published(target, email) == 0
                    assert row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0] == before
                finally:
                    change(target, restore, id=id, **({'verified': verified_at} if revocation == 'contact' else {}))
    run(scenario())


@pytest.mark.parametrize('action', ['review', 'publish'])
def test_expiry_while_waiting_case_lock_rolls_back_every_reply_mutation(target, action):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                case_id, envelope, _ = await setup_case(h, sender, target, c, schedule)
                body = 'This reply must not commit from an expired worker.'
                email = await inbound(h, target, c, schedule, envelope, body)
                repo = PostgresReplies(schedule._database, target['schema']); old = await schedule.claim(uuid4(), ('email_reply',), 30)
                assert (await repo.snapshot(old))['eligible']
                for forged in (replace(old, tenant_id=target['b']), replace(old, worker_id=uuid4()), replace(old, token=uuid4()), replace(old, generation=old.generation+1)):
                    assert await repo.snapshot(forged) is None
                before = row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0]
                change(target, "UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '28 seconds',lease_until=clock_timestamp()+interval '2 seconds' WHERE id=:id", id=old.id)
                with target['engine'].begin() as holder:
                    holder.execute(text('SELECT id FROM "' + target['schema'] + '".cases WHERE id=:c FOR UPDATE'), {'c': UUID(case_id)})
                    task = asyncio.create_task(repo.apply(old, action, body if action == 'publish' else '', None, None))
                    await asyncio.sleep(2.2)
                with pytest.raises(DatabaseError) as error:await asyncio.wait_for(task, 5)
                assert error.value.sqlstate == 'Z0018'
                assert state(target, email)[0] == 'pending' and published(target, email) == 0
                assert row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0] == before
                fresh = await schedule.claim(uuid4(), ('email_reply',), 30)
                assert fresh.id == old.id and fresh.generation == old.generation+1
                assert await repo.apply(old, action, body, None, None) is False
                assert await repo.apply(fresh, action, body, None, None)
                assert await schedule.finish(fresh, Outcome('succeeded', 'OK'))
                assert published(target, email) == (1 if action == 'publish' else 0)
    run(scenario())


def test_wrong_challenge_codes_exhaust_atomically_and_correct_late_code_cannot_publish(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                _, envelope, transport = await setup_case(h, sender, target, c, schedule)
                source, cid, notification = await challenge(h, target, c, schedule, envelope, transport)
                wrong = '000000' if code(SECRET, cid) != '000000' else '111111'
                for attempt in range(1, 6):
                    reply = await inbound(h, target, c, schedule, notification, 'VERIFY ' + str(cid) + ' ' + wrong, auth=False)
                    assert await worker(target, schedule).run_once() == 'succeeded'
                    assert row(target, 'SELECT attempts FROM {s}.email_reply_challenges WHERE id=:id', id=cid)[0] == attempt
                    assert state(target, reply)[0] == 'review' and published(target, reply) == 0
                assert row(target, 'SELECT state FROM {s}.email_reply_challenges WHERE id=:id', id=cid)[0] == 'failed'
                assert state(target, source)[:2] == ('review', 'CHALLENGE_ATTEMPTS_EXHAUSTED')
                await inbound(h, target, c, schedule, notification, 'VERIFY ' + str(cid) + ' ' + code(SECRET, cid), auth=False)
                assert await worker(target, schedule).run_once() == 'succeeded'
                assert published(target, source) == 0
    run(scenario())


def test_challenge_expiry_during_consumption_rolls_back_proof_case_and_message(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                case_id, envelope, transport = await setup_case(h, sender, target, c, schedule)
                source, cid, notification = await challenge(h, target, c, schedule, envelope, transport)
                response = await inbound(h, target, c, schedule, notification, 'VERIFY ' + str(cid) + ' ' + code(SECRET, cid), auth=False)
                lease = await schedule.claim(uuid4(), ('email_reply',), 30); repo = PostgresReplies(schedule._database, target['schema'])
                before = row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0]
                q = '"' + target['schema'] + '"'
                with target['engine'].begin() as db:
                    db.exec_driver_sql('CREATE FUNCTION ' + q + '.qa_slow_consume() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.state=\'consumed\' THEN PERFORM pg_sleep(1.3); END IF; RETURN NEW; END $$')
                    db.exec_driver_sql('CREATE TRIGGER qa_slow_consume BEFORE UPDATE ON ' + q + '.email_reply_challenges FOR EACH ROW EXECUTE FUNCTION ' + q + '.qa_slow_consume()')
                try:
                    change(target, "UPDATE {s}.email_reply_challenges SET expires_at=clock_timestamp()+interval '1 second' WHERE id=:id", id=cid)
                    with pytest.raises(DatabaseError) as error:await repo.apply(lease, 'verify', '', None, None)
                    assert error.value.sqlstate == 'Z0018'
                    assert published(target, source) == published(target, response) == 0
                    assert row(target, 'SELECT state,attempts FROM {s}.email_reply_challenges WHERE id=:id', id=cid) == ('pending', 0)
                    assert row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0] == before
                finally:
                    with target['engine'].begin() as db:
                        db.exec_driver_sql('DROP TRIGGER qa_slow_consume ON ' + q + '.email_reply_challenges')
                        db.exec_driver_sql('DROP FUNCTION ' + q + '.qa_slow_consume()')
    run(scenario())


def test_correct_challenge_is_one_time_and_never_creates_sessions_or_grants(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                _, envelope, transport = await setup_case(h, sender, target, c, schedule)
                source, cid, notification = await challenge(h, target, c, schedule, envelope, transport)
                counts = 'SELECT (SELECT count(*) FROM {s}.sessions),(SELECT count(*) FROM {s}.principals),(SELECT count(*) FROM {s}.case_participants)'
                before = row(target, counts)
                for _ in range(2):
                    reply = await inbound(h, target, c, schedule, notification, 'VERIFY ' + str(cid) + ' ' + code(SECRET, cid), auth=False)
                    assert await worker(target, schedule).run_once() == 'succeeded'
                    assert published(target, reply) == 0
                    assert code(SECRET, cid) not in row(target, 'SELECT body FROM {s}.email_messages WHERE id=:e', e=reply)[0]
                assert published(target, source) == 1 and row(target, counts) == before
                assert row(target, 'SELECT state,attempts FROM {s}.email_reply_challenges WHERE id=:id', id=cid) == ('consumed', 1)
    run(scenario())


@pytest.mark.parametrize('failure', ['reserved_crash', 'delivery_unknown', 'lost_ack', 'failed'])
def test_challenge_uncertain_outcomes_are_held_and_never_blindly_resent(target, failure):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                _, envelope, transport = await setup_case(h, sender, target, c, schedule)
                source, cid, _ = await challenge(h, target, c, schedule, envelope, transport, send=False)
                repo = PostgresReplies(schedule._database, target['schema']); before = len(transport.sent)
                if failure == 'reserved_crash':
                    lease = await schedule.claim(uuid4(), ('email_verify_send',), 30)
                    assert await repo.reserve(lease)
                    expire(target, lease.id)
                    assert await schedule.claim(uuid4(), ('email_verify_send',), 30) is None
                else:
                    if failure in ('delivery_unknown', 'failed'):
                        async def uncertain(value):transport.sent.append(value);return SendResult(failure)
                        transport.send = uncertain
                    else:
                        sent = repo.sent
                        async def lost_ack(*args):
                            assert await sent(*args)
                            raise TimeoutError('Synthetic challenge result acknowledgment lost')
                        repo.sent = lost_ack
                    result = await JobWorker(schedule, {'email_verify_send': ChallengeWorker(repo, transport, SECRET)}).run_once()
                    assert result == ('dead' if failure == 'failed' else 'unknown')
                assert await JobWorker(schedule, {'email_verify_send': ChallengeWorker(repo, transport, SECRET)}).run_once() == 'idle'
                assert len(transport.sent) == before + (0 if failure == 'reserved_crash' else 1)
                assert row(target, 'SELECT send_state FROM {s}.email_reply_challenges WHERE id=:id', id=cid)[0] == {'reserved_crash': 'reserved', 'delivery_unknown': 'delivery_unknown', 'lost_ack': 'accepted', 'failed': 'failed'}[failure]
                assert published(target, source) == 0
                assert state(target, source)[:2] == ('review', 'WORKER_STOPPED')
    run(scenario())


def test_recipient_edit_between_challenge_snapshot_and_reservation_prevents_send(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                case_id, envelope, transport = await setup_case(h, sender, target, c, schedule)
                source, _, _ = await challenge(h, target, c, schedule, envelope, transport, send=False)
                contact_id = row(target, 'SELECT followup_contact_id FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0]
                repo = PostgresReplies(schedule._database, target['schema']); original = repo.challenge
                async def snapshot(lease):
                    value = await original(lease)
                    if value:change(target, 'UPDATE {s}.contacts SET email_address=:email WHERE id=:id', email='changed-' + uuid4().hex + '@example.com', id=contact_id)
                    return value
                repo.challenge = snapshot
                before = len(transport.sent)
                try:
                    assert await JobWorker(schedule, {'email_verify_send': ChallengeWorker(repo, transport, SECRET)}).run_once() == 'blocked'
                    assert len(transport.sent) == before and published(target, source) == 0
                finally:
                    change(target, 'UPDATE {s}.contacts SET email_address=:email WHERE id=:id', email=envelope.recipient, id=contact_id)
    run(scenario())


def test_closed_html_quote_and_staff_projection_preserve_privacy_and_current_acl(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c, h.client() as staff, h.client() as admin, h.client() as outside:
                case_id, envelope, transport = await setup_case(h, sender, target, c, schedule, closed=True, staff=staff)
                for body, html in (('Thank you', None), ('Something may need checking.', None), (None, '<blockquote>CONTINUE SAME ISSUE</blockquote><p>Thank you</p>')):
                    email = await stage(h, target, c, schedule, envelope, body, html=html)
                    assert await worker(target, schedule).run_once() == 'succeeded'
                    assert published(target, email) == 1
                original, _, _ = await challenge(h, target, c, schedule, envelope, transport, send=False)
                assert row(target, 'SELECT status FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0] == 'closed'
                path = '/api/v1/staff/cases/' + case_id + '/email-replies'
                response = await staff.get(path); assert response.status_code == 200, response.text
                rows = {r['email_id']: r for r in response.json()}
                assert rows[str(original)]['body'] is None and rows[str(original)]['state'] == 'challenged'
                assert any(r['reason'] == 'CLOSED_REPLY_AMBIGUOUS' and r['body'] for r in rows.values())
                assert response.headers['cache-control'] == 'no-store'
                assert 'code_hash' not in response.text and 'verification_response_hash' not in response.text
                assert (await c.get(path)).status_code == 401
                await login(h, admin, operator(target, case_id, ('admin',)))
                await login(h, outside, seed(target))
                assert (await admin.get(path)).status_code == 404 and (await outside.get(path)).status_code == 404
                actor = row(target, 'SELECT assigned_membership_id FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0]
                change(target, 'UPDATE {s}.staff_memberships SET active=false WHERE id=:id', id=actor)
                assert (await staff.get(path)).status_code == 404
    run(scenario())


def test_open_human_chat_reply_invalidates_stale_close_without_changing_handler(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c, h.client() as staff:
                case_id, envelope, _ = await setup_case(h, sender, target, c, schedule)
                conversation = row(target, 'SELECT id FROM {s}.conversations WHERE case_id=:c', c=UUID(case_id))[0]
                await login(h, staff, operator(target, case_id))
                assert (await accept(h, staff, conversation, await meta(staff, conversation))).status_code == 200
                old = await meta(staff, conversation)
                email = await inbound(h, target, c, schedule, envelope, 'Additional information before closure.')
                assert await worker(target, schedule).run_once() == 'succeeded' and published(target, email) == 1
                current = await meta(staff, conversation)
                assert current['version'] == old['version'] and current['generation_epoch'] == old['generation_epoch']
                assert current['handler_membership_id'] == old['handler_membership_id'] and current['mode'] == 'human'
                assert current['case_version'] == old['case_version']+1
                assert (await close(h, staff, case_id, old)).status_code == 409
                response = await close(h, staff, case_id, current); assert response.status_code == 200, response.text
    run(scenario())


def test_fault_after_publication_writes_rolls_back_then_same_lease_commits_once(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                case_id, envelope, _ = await setup_case(h, sender, target, c, schedule)
                body = 'Original customer reply for atomic publication.'
                email = await inbound(h, target, c, schedule, envelope, body)
                repo = PostgresReplies(schedule._database, target['schema']); lease = await schedule.claim(uuid4(), ('email_reply',), 30)
                before = row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0]
                q = '"' + target['schema'] + '"'
                with target['engine'].begin() as db:
                    db.exec_driver_sql('CREATE FUNCTION ' + q + '.qa_reply_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.state=\'published\' THEN RAISE EXCEPTION \'Synthetic post-write publication fault\'; END IF; RETURN NEW; END $$')
                    db.exec_driver_sql('CREATE TRIGGER qa_reply_fault AFTER UPDATE ON ' + q + '.email_reply_state FOR EACH ROW EXECUTE FUNCTION ' + q + '.qa_reply_fault()')
                try:
                    with pytest.raises(DatabaseError):await repo.apply(lease, 'publish', body, None, None)
                    assert published(target, email) == 0 and state(target, email)[0] == 'pending'
                    assert row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0] == before
                finally:
                    with target['engine'].begin() as db:
                        db.exec_driver_sql('DROP TRIGGER qa_reply_fault ON ' + q + '.email_reply_state')
                        db.exec_driver_sql('DROP FUNCTION ' + q + '.qa_reply_fault()')
                assert await repo.apply(lease, 'publish', body, None, None)
                assert await repo.apply(lease, 'publish', body, None, None)
                assert published(target, email) == 1
                assert row(target, 'SELECT version FROM {s}.cases WHERE id=:c', c=UUID(case_id))[0] == before+1
                assert await schedule.finish(lease, Outcome('succeeded', 'OK'))
    run(scenario())


def test_foreign_account_cannot_use_another_accounts_reply_token(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as schedule:
            ingress_for_two(h, target)
            async with h.client() as c:
                _, envelope, _ = await setup_case(h, sender, target, c, schedule)
                change(target, 'UPDATE {s}.email_messages SET rfc_message_id=:r WHERE id=:id', r=envelope.rfc_message_id, id=envelope.id)
                schedule.mail_accounts = ('qa-account-b',)
                token = envelope.reply_to.split('+r-', 1)[1].split('@', 1)[0]
                email = await stage(h, target, c, schedule, envelope, 'Foreign account token', delivered='reply+r-' + token + '@other.example', refs=envelope.rfc_message_id, alias='qa-airport-b')
                assert await worker(target, schedule).run_once() == 'succeeded'
                assert published(target, email) == 0 and state(target, email)[0] in ('rejected', 'review')
    run(scenario())


def test_api_and_worker_cannot_write_proofs_or_read_codes_and_partial_profile_fails(target):
    async def scenario():
        async with inbox_app(target) as (h, _), scheduler(target) as schedule:
            for database, statements in ((h.db, ('SELECT code_hash FROM {s}.email_reply_challenges', "UPDATE {s}.email_reply_state SET state='published'", 'SELECT {s}.email_reply_snapshot(NULL,NULL,NULL,NULL,NULL)')),
                                         (schedule._database, ('SELECT * FROM {s}.email_reply_challenges', "UPDATE {s}.email_reply_state SET proof='challenge'"))):
                for statement in statements:
                    with pytest.raises(DatabaseError) as error:
                        async with database.transaction() as c:await c.execute(sql.SQL(statement).format(s=sql.Identifier(target['schema'])))
                    assert error.value.sqlstate == '42501'
        function = '"' + target['schema'] + '".email_challenge_reserve(uuid,uuid,uuid,uuid,bigint)'; role = '"' + target['worker_role'] + '"'
        with target['engine'].begin() as db:db.exec_driver_sql('REVOKE EXECUTE ON FUNCTION ' + function + ' FROM ' + role)
        try:
            db = WorkerDatabase(target['worker_url'], schema=target['schema']); db.mail_reply_required = True
            try:
                with pytest.raises(DatabaseError):await db.open()
            finally:await db.close()
        finally:
            with target['engine'].begin() as db:db.exec_driver_sql('GRANT EXECUTE ON FUNCTION ' + function + ' TO ' + role)
    run(scenario())


def test_reply_account_fk_routes_claim_and_reaper_and_challenge_to_correct_worker(target):
    async def scenario():
        async with inbox_app(target) as (h, sender), scheduler(target) as a, scheduler(target) as b:
            ingress_for_two(h, target); b.mail_accounts = ('qa-account-b',)
            async with h.client() as c:
                _, envelope, transport = await setup_case(h, sender, target, c, a)
                email_a = await inbound(h, target, c, a, envelope, 'Correct-account reply.')
                token = envelope.reply_to.split('+r-', 1)[1].split('@', 1)[0]
                email_b = await stage(h, target, c, b, envelope, 'Foreign-account reply.', delivered='reply+r-' + token + '@other.example', alias='qa-airport-b')
                assert row(target, 'SELECT reply_account_id FROM {s}.outbox_jobs WHERE reply_email_id=:e AND kind=\'email_reply\'', e=email_a)[0] == target['mail_account']
                assert row(target, 'SELECT reply_account_id FROM {s}.outbox_jobs WHERE reply_email_id=:e AND kind=\'email_reply\'', e=email_b)[0] == target['qa_inbox_account_b']
                old_b = await b.claim(uuid4(), ('email_reply',), 30); assert old_b.tenant_id == target['b']
                expire(target, old_b.id)
                lease_a = await a.claim(uuid4(), ('email_reply',), 30); assert lease_a.tenant_id == target['a']
                assert row(target, 'SELECT state,attempts,lease_generation FROM {s}.outbox_jobs WHERE id=:id', id=old_b.id) == ('leased', 1, old_b.generation)
                assert (await ReplyWorker(PostgresReplies(a._database, target['schema']), SECRET)(lease_a)).state == 'succeeded'
                assert await a.finish(lease_a, Outcome('succeeded', 'OK'))
                fresh_b = await b.claim(uuid4(), ('email_reply',), 30)
                assert fresh_b.id == old_b.id and fresh_b.generation == old_b.generation+1
                repo_b = PostgresReplies(b._database, target['schema'])
                assert await repo_b.snapshot(old_b) is None
                assert (await ReplyWorker(repo_b, SECRET)(fresh_b)).state == 'succeeded'
                assert await b.finish(fresh_b, Outcome('succeeded', 'OK'))
                assert published(target, email_a) == 1 and published(target, email_b) == 0
                _, cid, _ = await challenge(h, target, c, a, envelope, transport, send=False)
                assert await b.claim(uuid4(), ('email_verify_send',), 30) is None
                send_a = await a.claim(uuid4(), ('email_verify_send',), 30)
                assert send_a is not None
                assert row(target, 'SELECT reply_account_id,reply_challenge_id FROM {s}.outbox_jobs WHERE id=:id', id=send_a.id) == (target['mail_account'], cid)
                assert await a.finish(send_a, Outcome('dead', 'PERMANENT'))
    run(scenario())
