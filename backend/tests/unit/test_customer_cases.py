"""Private pagination cannot disclose denied lookahead or reuse stale authority."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from airport_assistant.modules.cases.customer import CustomerCases, CustomerCaseCursor
from airport_assistant.modules.cases.queue import QueueCursor
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure


def label(ref):
    return dict(source='reference',resource_type='booking',reference=ref,required_source_scopes=['booking:read'])


class Fixture:
    def __init__(self, private=False):
        self.identity=SimpleNamespace(database_context=None,session=SimpleNamespace(
            kind='customer',tenant_id=uuid4(),principal_id=uuid4(),id=uuid4()))
        self.now=datetime.now(timezone.utc)-timedelta(seconds=1)
        self.rows=[dict(id=UUID(int=i),public_reference='CASE-'+UUID(int=i).hex,status='new',summary='Need help',
            next_actor='staff',next_action='Await staff',version=1,created_at=self.now,updated_at=self.now,
            resource_security_labels=[label('B'+str(i))] if private else []) for i in range(1,5)]
        self.access=SimpleNamespace(allows=AsyncMock(return_value=True))
        self.codec=CustomerCaseCursor(b'c'*32)
        self.service=CustomerCases(self,self.codec,self.access)
        self.transactions=0;self.active=0;self.session_calls=0;self.revoke_at=None;self.args=[]

    @asynccontextmanager
    async def transaction(self,context):
        self.transactions+=1;self.active+=1
        try:yield self
        finally:self.active-=1

    async def session_active(self):
        self.session_calls+=1
        return self.revoke_at is None or self.session_calls<self.revoke_at

    async def queue_clock(self):return self.now+timedelta(seconds=1)

    async def customer_case_page(self,status,watermark,position,limit):
        self.args.append((status,watermark,position,limit))
        return deepcopy([r for r in self.rows if (status is None or r['status']==status)
            and r['created_at']<=watermark and (position is None or (r['created_at'],r['id'])>position)][:limit])

    def page(self,**kwargs):return asyncio.run(self.service.page(self.identity,**kwargs))


def test_public_ties_pages_exactly_once_and_fixed_watermark():
    f=Fixture();first=f.page(limit=2)
    second=f.page(limit=2,cursor=first['next_cursor'])
    assert [i['id'].int for i in first['items']+second['items']]==[1,2,3,4]
    assert first['has_more'] and not second['has_more'] and second['next_cursor'] is None
    assert first['watermark']==second['watermark']
    assert f.args[0][3]==3 and f.transactions==2
    assert set(first['items'][0])=={'id','public_reference','status','summary','next_actor','next_action','version','created_at','updated_at'}
    f.access.allows.assert_not_awaited()


def test_cursor_bound_to_session_customer_tenant_filters_limit_and_purpose():
    f=Fixture();token=f.page(limit=2)['next_cursor']
    for name in ['id','principal_id','tenant_id']:
        original=getattr(f.identity.session,name);setattr(f.identity.session,name,uuid4())
        with pytest.raises(DraftError):f.page(limit=2,cursor=token)
        setattr(f.identity.session,name,original)
    for args in [dict(limit=3),dict(limit=2,status='closed')]:
        with pytest.raises(DraftError):f.page(cursor=token,**args)
    with pytest.raises(DraftError):QueueCursor(b'c'*32).read(token,[])


@pytest.mark.parametrize('kind',['anonymous','staff'])
def test_verified_customer_only_before_repository(kind):
    f=Fixture();f.identity.session.kind=kind
    with pytest.raises(AuthError) as e:f.page()
    assert e.value.status==401 and f.transactions==0


def test_private_denied_lookahead_withholds_whole_page_and_metadata():
    f=Fixture(private=True)
    async def grants(**kwargs):
        assert f.active==0
        assert {v.reference for v in kwargs['labels'].labels}=={'B1','B2','B3'}
        return False
    f.access.allows.side_effect=grants
    with pytest.raises(AuthError) as e:f.page(limit=2)
    assert e.value.status==404


def test_more_than_64_distinct_requirements_preserved_in_chunks_and_outside_sql():
    f=Fixture(private=True)
    for i,r in enumerate(f.rows):r['resource_security_labels']=[label('B'+str(i*40+j)) for j in range(40)]
    seen=[]
    async def grants(**kwargs):
        assert f.active==0
        seen.extend(l.reference for l in kwargs['labels'].labels)
        return True
    f.access.allows.side_effect=grants
    assert len(f.page(limit=3)['items'])==3
    assert len(seen)==len(set(seen))==160 and f.access.allows.await_count==3
    assert f.transactions==2 and f.args[0]==f.args[1]


@pytest.mark.parametrize('change',['summary','labels','membership','next_action'])
def test_page_and_ancestry_change_during_grants_cannot_return_stale_data(change):
    f=Fixture(private=True)
    async def grants(**kwargs):
        if change=='membership':f.rows.pop()
        elif change=='labels':f.rows[0]['resource_security_labels']=[label('NEW')]
        else:f.rows[0][change]='Changed'
        return True
    f.access.allows.side_effect=grants
    with pytest.raises(DraftError) as e:f.page(limit=4)
    assert e.value.code=='SOURCE_CONTEXT_CHANGED'


def test_no_deadline_extension_and_source_failure_not_misreported_denied():
    f=Fixture(private=True);clock=iter([0,2.01]);f.service.clock=lambda:next(clock)
    with pytest.raises(SourceFailure):f.page()
    f=Fixture(private=True);f.access.allows.side_effect=SourceFailure(503)
    with pytest.raises(SourceFailure):f.page()


def test_revoked_session_at_final_exit_blocks_page():
    f=Fixture(private=True);f.revoke_at=4
    with pytest.raises(AuthError) as e:f.page()
    assert e.value.status==401


def test_missing_ancestry_fails_closed_before_grants():
    f=Fixture();f.rows[0]['resource_security_labels']=None
    with pytest.raises(AuthError):f.page()
    f.access.allows.assert_not_awaited()


def test_shared_timeout_cancels_inside_sequential_grants_before_later_refs():
    from airport_assistant.modules.source.labels import PrivateSourceAccess
    f=Fixture(private=True);calls=[]
    async def has_grants(**kwargs):
        calls.append(kwargs['resource_reference'])
        await asyncio.sleep(.05)
        return True
    f.service._private_access=PrivateSourceAccess(SimpleNamespace(has_grants=has_grants))
    clock=iter([0,1.99]);f.service.clock=lambda:next(clock)
    with pytest.raises(SourceFailure):f.page()
    assert calls==['B1'] and f.transactions==1
