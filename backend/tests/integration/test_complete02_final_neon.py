"""One disposable schema for the final targeted roles/ratings qualification."""
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import pytest
from test_administration_ratings_neon import target,rating_target,information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target
from test_administration_ratings_neon import (
    test_admin_directory_scope_no_self_no_case_access_and_atomic_versioned_changes,
    test_admin_fault_rolls_back_audit_roles_and_queue_bindings_and_raw_dml_is_denied,
    test_reciprocal_admin_deactivation_serializes_by_organization_without_deadlock,
    test_dropped_partial_unique_index_fails_admission_and_rollback_restores_it,
)
from test_customer_ratings_neon import (
    test_actual_rating_upsert_owner_source_isolation_and_no_case_or_outbox_changes as public_rating,
    test_audit_failure_rolls_back_and_changed_readiness_cannot_write,
    test_two_independent_connections_cannot_overwrite_a_new_rating_with_the_same_old_version,
)
from test_rating_recovery_neon import (
    test_actual_otp_adoption_keeps_single_rating_original_session_and_current_owner_scope,
    test_private_feedback_actual_labels_denial_foreign_sessions_and_get_recovery as private_rating,
)
from test_private_source_graph_neon import app,run,count
from airport_assistant.infrastructure.database.case_repository import CaseSQL,PostgresCases
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.drafts.models import ReportPayload

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_COMPLETE02_TESTS')!='1',reason='Opt-in final isolated roles/ratings qualification.')


def test_current_public_rating(target):public_rating(target,False)


class TimingDB:
    """Metadata only: no statement text, parameters, actor values or credentials."""
    def __init__(self,inner):self.inner=inner;self.phase='other';self.events=[]
    def __getattr__(self,name):return getattr(self.inner,name)
    @asynccontextmanager
    async def transaction(self,*args,**kwargs):
        started=time.monotonic();phase=self.phase
        try:
            async with self.inner.transaction(*args,**kwargs) as connection:
                self.events.append(dict(phase=phase,event='transaction_acquired',ms=round((time.monotonic()-started)*1000,2)))
                yield TimingConnection(connection,self)
        finally:self.events.append(dict(phase=phase,event='transaction_finished',ms=round((time.monotonic()-started)*1000,2)))


class TimingConnection:
    def __init__(self,connection,trace):self.connection,self.trace=connection,trace
    def __getattr__(self,name):return getattr(self.connection,name)
    async def execute(self,*args,**kwargs):
        started=time.monotonic()
        try:return await self.connection.execute(*args,**kwargs)
        finally:self.trace.events.append(dict(phase=self.trace.phase,event='execute',ms=round((time.monotonic()-started)*1000,2)))
    @asynccontextmanager
    async def pipeline(self):
        started=time.monotonic()
        try:
            async with self.connection.pipeline() as pipeline:yield pipeline
        finally:self.trace.events.append(dict(phase=self.trace.phase,event='pipeline_synchronized',ms=round((time.monotonic()-started)*1000,2)))


def test_private_rating_with_metadata_timings(target,monkeypatch):
    # Reuse the exact authorization/denial assertions; add phase timings only.
    import test_rating_recovery_neon as recovery
    original_app=recovery.app
    traces=[]
    @asynccontextmanager
    async def timed_app(t):
        async with original_app(t) as h:
            trace=TimingDB(h.db);traces.append(trace);h.db=trace
            h.drafts=DraftService(PostgresCases(trace,t.schema),h.access)
            h.cases=CaseService(PostgresCases(trace,t.schema),h.access)
            for obj,method,label in ((h.drafts,'create','private_draft'),(h.cases,'confirm','private_confirm')):
                original=getattr(obj,method)
                async def timed(*args,_call=original,_label=label,**kwargs):
                    trace.phase=_label;started=time.monotonic()
                    try:return await _call(*args,**kwargs)
                    finally:
                        trace.events.append(dict(phase=_label,event='operation_finished',ms=round((time.monotonic()-started)*1000,2)))
                        trace.phase='other'
                setattr(obj,method,timed)
            yield h
    monkeypatch.setattr(recovery,'app',timed_app)
    try:private_rating(target)
    finally:
        report=dict(schema=target.schema,deadline_seconds=2,events=[event for trace in traces for event in trace.events])
        dest=Path(__file__).resolve().parents[3]/'local/team/complete02-private-timings.json'
        dest.write_text(json.dumps(report,indent=2),encoding='utf-8')


def test_pipeline_server_error_rolls_back_graph_before_return_and_same_intent_can_be_explicitly_confirmed(target):
    class FaultConnection:
        def __init__(self,inner):self.inner=inner;self.writes=0
        def __getattr__(self,name):return getattr(self.inner,name)
        async def execute(self,*args,**kwargs):
            self.writes+=1
            if self.writes==4:
                # Actual server failure between graph INSERTs in the open pipeline.
                await self.inner.execute('SELECT 1/0')
            return await self.inner.execute(*args,**kwargs)
    class FaultSQL(CaseSQL):
        async def _insert_case_graph(self,*args):
            original=self._c;self._c=FaultConnection(original)
            try:return await super()._insert_case_graph(*args)
            finally:self._c=original
    class FaultRepository(PostgresCases):
        def unit(self,c,context):return FaultSQL(c,self._schema,context)
    async def scenario():
        async with app(target) as h:
            conversation=await h.conversations.create(target.identity,'en')
            await h.conversations.append(target.identity,conversation.id,uuid4(),'Please explain the airport facilities.')
            payload=ReportPayload(category='information',service_type='general',summary='Airport facilities',customer_report='Please explain the procedure.',contact_email='customer@example.com')
            draft=await h.drafts.create(target.identity,conversation.id,'create_case',payload)
            tables=('cases','action_consents','case_participants','case_history','audit_events','outbox_jobs','idempotency_records')
            before={table:count(target,table) for table in tables};key=uuid4().hex;request=uuid4()
            with pytest.raises(DatabaseError) as error:
                await CaseService(FaultRepository(h.db,target.schema),h.access).confirm(target.identity,draft.id,draft.version,draft.payload_hash,key,request)
            assert error.value.sqlstate=='22012'
            assert {table:count(target,table) for table in tables}==before
            assert (await h.drafts.get(target.identity,draft.id)).state=='pending'
            saved=await h.cases.confirm(target.identity,draft.id,draft.version,draft.payload_hash,key,request)
            assert (await h.cases.status(target.identity,draft.id))['case_id']==saved.case_id
            assert count(target,'cases')==before['cases']+1
    run(scenario())
