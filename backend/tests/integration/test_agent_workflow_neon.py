"""AI-02 isolated durable checkpoints and atomic draft receipts; no paid providers."""
import asyncio
import os
from pathlib import Path
from datetime import timedelta
from uuid import uuid4
import pytest
from test_knowledge_vectors_neon import target as vector_target, knowledge_target, auth_target
from test_staff_auth import harness, seed, run, row, change
from test_knowledge_neon import identity
from test_agent_foundation_neon import services, counts
from airport_assistant.database_config import load_database_settings, MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.agent_runs import PostgresAgentRuns, AgentSQL
from airport_assistant.modules.agent.workflow import AgentWorkflow, RunRequest
from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import customer_transaction
from airport_assistant.infrastructure.database.connections import DatabaseError

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated AI-02 Neon tests.')


@pytest.fixture(scope='module')
def target(vector_target):
    t = vector_target
    settings = load_database_settings(MigrationDatabaseSettings, Path(os.environ.get('DB_TEST_ENV_FILE', '.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(), schema=t['schema'], runtime_role=t['role'], policy_role=t['policy'], auth_role=t['owner'], target_revision='0026_agent_runs') == '0026_agent_runs'
    return t


def workflow(h, target):
    foundation, conversations, drafts, planner = services(h, target)
    engine = AgentWorkflow(foundation, PostgresAgentRuns(h.db, target['schema']))
    return engine, conversations, drafts, planner


async def begin(h, c, target, *, language='en'):
    await h.boot(c)
    actor = await identity(h, c)
    engine, conversations, drafts, planner = workflow(h, target)
    conv = await conversations.create(actor, language)
    message = await conversations.append(actor, conv.id, uuid4(), 'Parking booking PK-A-001 needs help.')
    request = RunRequest(conversation_id=conv.id, message_id=message.id, request_key=uuid4(), mode='draft', booking_references=('PK-A-001',))
    run_id = await engine.start(actor, request)
    return actor, engine, conversations, planner, request, run_id


def test_atomic_receipt_failure_rolls_back_and_restart_replays_exactly_one_draft(target, monkeypatch):
    seed(target, roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0, max_size=2)
            async with h.client() as c:
                actor, engine, conversations, planner, request, run_id = await begin(h, c, target)
                baseline = counts(target)
                before = row(target, 'SELECT count(*) FROM {s}.action_drafts')[0]
                original = AgentSQL.update_run
                failed = False
                async def interrupt(tx, checkpoint, **changes):
                    nonlocal failed
                    if changes.get('phase') == 'done' and not failed:
                        failed = True
                        raise RuntimeError('Synthetic receipt interruption')
                    return await original(tx, checkpoint, **changes)
                monkeypatch.setattr(AgentSQL, 'update_run', interrupt)
                with pytest.raises(RuntimeError):
                    await AgentGraph(engine).resume(actor, run_id)
                assert row(target, 'SELECT phase FROM {s}.agent_runs WHERE id=:id', id=run_id)[0] == 'draft'
                assert row(target, 'SELECT count(*) FROM {s}.action_drafts')[0] == before
                restarted, _, _, fresh_planner = workflow(h, target)
                result = await AgentGraph(restarted).resume(actor, run_id)
                again = await AgentGraph(restarted).resume(actor, run_id)
                assert result.status == again.status == 'done' and result.draft.id == again.draft.id
                assert row(target, 'SELECT count(*) FROM {s}.action_drafts')[0] == before + 1
                assert len(planner.calls) == 1 and not fresh_planner.calls
                assert counts(target) == baseline
                assert await restarted.start(actor, request) == run_id
    run(scenario())


def test_concurrent_resume_claim_and_unknown_model_outcome_are_not_replayed(target):
    seed(target, roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0, max_size=2)
            async with h.client() as c:
                actor, engine, conversations, planner, request, run_id = await begin(h, c, target)
                entered, release = asyncio.Event(), asyncio.Event()
                async def hold():
                    entered.set()
                    await release.wait()
                planner.hook = hold
                first = asyncio.create_task(AgentGraph(engine).resume(actor, run_id))
                try:
                    await asyncio.wait_for(entered.wait(), 15)
                    with pytest.raises(AgentError) as exc:
                        await AgentGraph(engine).resume(actor, run_id)
                    assert exc.value.code == 'AGENT_RUN_BUSY'
                finally:
                    release.set()
                    result = await first
                assert result.status == 'done' and len(planner.calls) == 1
                # Reserve a model attempt then simulate process loss before recording it.
                fresh_message = await conversations.append(actor, request.conversation_id, uuid4(), 'Please investigate the same parking issue.')
                second_request = request.model_copy(update={'message_id': fresh_message.id, 'request_key': uuid4()})
                second_id = await engine.start(actor, second_request)
                work = await engine.load(actor, second_id)
                claimed = await engine._claim(work, 'planning')
                async with customer_transaction(engine.repository, actor) as tx:
                    await engine._locked(tx, actor, second_id)
                    await tx.update_run(claimed, lease_until=await tx.now() - timedelta(seconds=1))
                outcome = await AgentGraph(engine).resume(actor, second_id)
                assert outcome.status == 'review' and outcome.reason == 'model_outcome_unknown'
                assert len(planner.calls) == 1
    run(scenario())


def test_checkpoint_rls_context_change_and_revoked_session(target):
    seed(target, roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0, max_size=2)
            async with h.client() as c, h.client() as other, h.client('b.example.test') as foreign_client:
                actor, engine, conversations, planner, request, run_id = await begin(h, c, target)
                await h.boot(other); await h.boot(foreign_client)
                outsider = await identity(h, other)
                from airport_assistant.api.security import SESSION_COOKIE
                foreign = await h.app.state.services.access.resolve(target['b'], foreign_client.cookies.get(SESSION_COOKIE))
                for wrong in (outsider, foreign):
                    with pytest.raises(AuthError):
                        await engine.load(wrong, run_id)
                await conversations.append(actor, request.conversation_id, uuid4(), 'Correction: no longer needed.')
                assert (await AgentGraph(engine).resume(actor, run_id)).status == 'superseded'
                assert not planner.calls
                change(target, 'UPDATE {s}.sessions SET revoked_at=clock_timestamp() WHERE id=:id', id=actor.session.id)
                with pytest.raises(AuthError):
                    await engine.load(actor, run_id)
    run(scenario())


def test_restart_preserves_user_memory_and_language_without_tool_bodies(target):
    seed(target, roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0, max_size=2)
            async with h.client() as c:
                actor, engine, conversations, planner, request, old_id = await begin(h, c, target, language='id')
                latest = await conversations.append(actor, request.conversation_id, uuid4(), 'Bukan besok, maksud saya hari Jumat.')
                request = request.model_copy(update={'message_id': latest.id, 'request_key': uuid4()})
                run_id = await engine.start(actor, request)
                restarted, _, _, new_planner = workflow(h, target)
                result = await AgentGraph(restarted).resume(actor, run_id)
                assert result.draft.payload.booking_reference == 'PK-A-001'
                assert latest.body in result.draft.payload.customer_report
                assert new_planner.calls[0][1] == 'id' and new_planner.calls[0][2] == ()
                record = row(target, 'SELECT trace,pending_call,proposal,model_calls,read_calls FROM {s}.agent_runs WHERE id=:id', id=run_id)
                assert record[0] == [] and record[1] is None and record[3:] == (1, 0)
    run(scenario())


def test_database_rejects_phase_jump_counter_jump_and_runtime_delete(target):
    seed(target, roles=('admin',))
    async def scenario():
        async with harness(target) as h:
            await h.db._pool.resize(min_size=0, max_size=2)
            async with h.client() as c:
                actor, engine, conversations, planner, request, run_id = await begin(h, c, target)
                for changes in ({'phase': 'waiting_user'}, {'read_calls': 2}):
                    with pytest.raises(DatabaseError) as failure:
                        async with customer_transaction(engine.repository, actor) as tx:
                            _, checkpoint = await engine._locked(tx, actor, run_id)
                            await tx.update_run(checkpoint, **changes)
                    assert failure.value.sqlstate == '23514'
                with pytest.raises(DatabaseError) as failure:
                    async with customer_transaction(engine.repository, actor) as tx:
                        await tx._c.execute(tx.query('DELETE FROM {s}.agent_runs WHERE id=%s'), (run_id,))
                assert failure.value.sqlstate == '42501'
                checkpoint = (await engine.load(actor, run_id)).run
                assert checkpoint['phase'] == 'ready' and checkpoint['revision'] == 1
    run(scenario())
