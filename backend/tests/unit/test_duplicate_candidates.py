"""Lookup input, service boundaries and minimal parameterized SQL; no network."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from airport_assistant.modules.cases.duplicate import DuplicateSearch
from airport_assistant.modules.cases.capabilities import StaffCapabilities, AssigneeCursor
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.infrastructure.database.case_lifecycle import LifecycleSQL


class Fixture:
    def __init__(self):
        self.id, self.mid, self.target = uuid4(), uuid4(), uuid4()
        self.identity = SimpleNamespace(database_context=None, session=SimpleNamespace(kind='staff'))
        self.case = dict(id=self.id, version=3, status='new', organization_id=uuid4(), owner_principal_id=uuid4(), owner_anonymous_session_id=None)
        self.workspace = dict(membership_id=self.mid, membership_version=2)
        self.active = True
        self.events = []
        self.candidate = dict(case_id=self.target, public_reference='CASE-' + self.target.hex)
        self.service = StaffCapabilities(self, AssigneeCursor(b'x' * 32))

    def command(self, **extra):
        return DuplicateSearch.model_validate(dict(membership_id=self.mid, expected_version=3, public_reference='CASE-' + self.target.hex, **extra))

    @asynccontextmanager
    async def transaction(self, context):
        yield self

    async def session_active(self): return self.active

    async def find_case(self, id, *, lock=False):
        assert id == self.id and lock
        self.events.append('case')
        return self.case

    async def active_chat(self, id, *, lock=False):
        assert id == self.id and lock
        self.events.append('chat')
        return None

    async def staff_workspace(self, id, mid):
        assert id == self.id and mid == self.mid
        self.events.append('scope')
        return self.workspace

    async def duplicate_candidate(self, reference, case, mid):
        assert reference == self.candidate['public_reference'] and case is self.case and mid == self.mid
        self.events.append('lookup')
        return self.candidate if self.found else None

    found = True


@pytest.mark.parametrize('reference', ['', 'CASE-', 'case-'+'a'*32, 'CASE-'+'A'*32, ' CASE-'+'a'*32, 'CASE-'+'a'*32+'\n', 'CASE-'+'a'*31, 'CASE-'+'a'*33, 'CASE-'+'%'*32, 'CASE-'+'_'*32, 123, None])
def test_exact_canonical_reference_only(reference):
    with pytest.raises(ValidationError): DuplicateSearch(membership_id=uuid4(), expected_version=1, public_reference=reference)


@pytest.mark.parametrize('change', [dict(expected_version=True), dict(expected_version='1'), dict(expected_version=0), dict(membership_id='bad'), dict(tenant_id=str(uuid4()))])
def test_strict_version_uuid_and_no_extra_identity_fields(change):
    value=dict(membership_id=str(uuid4()), expected_version=1, public_reference='CASE-'+'a'*32); value.update(change)
    with pytest.raises(ValidationError): DuplicateSearch.model_validate(value)


def test_selected_scope_precedes_version_disclosure_and_lookup():
    f=Fixture(); f.workspace=None; f.case['version']=4
    with pytest.raises(AuthError) as e: asyncio.run(f.service.duplicate_candidates(f.identity,f.id,f.command()))
    assert e.value.status==404 and f.events==['case','chat','scope']


@pytest.mark.parametrize('closed', [False,True])
def test_stale_or_closed_source_does_not_search(closed):
    f=Fixture(); f.case.update(status='closed' if closed else 'new',version=3 if closed else 4)
    with pytest.raises(DraftError) as e: asyncio.run(f.service.duplicate_candidates(f.identity,f.id,f.command()))
    assert e.value.status==409 and 'lookup' not in f.events


@pytest.mark.parametrize('found', [False,True])
def test_minimal_bound_response_and_no_business_write(found):
    f=Fixture(); f.found=found
    value=asyncio.run(f.service.duplicate_candidates(f.identity,f.id,f.command()))
    assert value==dict(case_id=f.id,case_version=3,membership_id=f.mid,membership_version=2,items=[f.candidate] if found else [])
    assert f.events==['case','chat','scope','lookup'] and f.case['version']==3


def test_expired_session_after_lookup_rejects_the_result():
    f=Fixture()
    original=f.duplicate_candidate
    async def revoked(*args):
        value=await original(*args); f.active=False; return value
    f.duplicate_candidate=revoked
    with pytest.raises(AuthError) as e: asyncio.run(f.service.duplicate_candidates(f.identity,f.id,f.command()))
    assert e.value.status==401


def test_sql_projects_only_candidate_metadata_and_checks_exact_owner_pair_and_workspace():
    f=Fixture(); context=SimpleNamespace(tenant_id=uuid4()); captured=[]
    class Connection:
        async def execute(self,query,args): captured.append((query,args)); return self
        async def fetchone(self): return (f.target,f.candidate['public_reference'])
    sql=LifecycleSQL(Connection(),'app',context)
    value=asyncio.run(sql.duplicate_candidate(f.candidate['public_reference'],f.case,f.mid))
    query,args=captured[0]
    query=query.as_string()
    assert 'SELECT id,public_reference' in query and 'customer_report' not in query and 'FOR UPDATE' not in query
    assert 'public_reference=%s' in query and 'id<>%s' in query and 'organization_id=%s' in query
    assert 'owner_principal_id IS NOT DISTINCT FROM %s' in query and 'owner_anonymous_session_id IS NOT DISTINCT FROM %s' in query
    assert 'case_staff_workspace(id,%s) IS NOT NULL' in query and 'LIMIT 1' in query
    assert args==(context.tenant_id,f.candidate['public_reference'],f.id,f.case['organization_id'],f.case['owner_principal_id'],None,f.mid)
    assert value==f.candidate
