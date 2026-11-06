"""Published history boundaries, keyset positions and fail-closed ancestry."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest

from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.conversations.correspondence import StaffCorrespondence
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


class Store:
    def __init__(self):
        self.actor=SimpleNamespace(session=SimpleNamespace(kind='staff',id=uuid4(),tenant_id=uuid4(),principal_id=uuid4()),
            memberships=(object(),),database_context=None)
        self.case_id=uuid4();self.member=uuid4();self.active=True;self.access=True;self.ancestry=[]
        now=datetime.now(timezone.utc)
        self.rows=[dict(id=uuid4(),case_id=self.case_id,conversation_id=None,author_type='staff',body='Saved reply',
            language='en',sequence=3-n,channel='email',created_at=now-timedelta(seconds=n),resource_security_labels=[],conversation_labels=[]) for n in range(3)]
        self.reads=0
    @asynccontextmanager
    async def transaction(self,context):yield self
    async def session_active(self):return self.active
    async def find_case(self,id):return dict(id=id,version=1,resource_security_labels=[])
    async def staff_workspace(self,*args):return {} if self.access else None
    async def case_correspondence_ancestry(self,*args):return self.ancestry
    async def staff_correspondence(self,case,before_time,before_id,limit):
        self.reads+=1
        assert limit==3
        return self.rows[:limit]


def test_newest_page_is_chronological_with_explicit_older_position():
    async def run():
        db=Store();page=await StaffCorrespondence(StaffMessages(db)).read(db.actor,db.case_id,db.member,limit=2)
        assert [r['id'] for r in page['items']]==[r['id'] for r in reversed(db.rows[:2])]
        assert page['has_more'] and page['before_id']==db.rows[1]['id']
        assert page['before_time']==db.rows[1]['created_at']
        assert all('resource_security_labels' not in r for r in page['items'])
    asyncio.run(run())


@pytest.mark.parametrize('bad',['missing_id','naive_time','limit','missing_ancestry','queue','parent'])
def test_unavailable_scope_or_malformed_position_never_emits_history(bad):
    async def run():
        db=Store();kwargs=dict(limit=2)
        if bad=='missing_id':kwargs['before_time']=datetime.now(timezone.utc)
        if bad=='naive_time':kwargs.update(before_time=datetime.now(),before_id=uuid4())
        if bad=='limit':kwargs['limit']=51
        if bad=='missing_ancestry':db.ancestry=None
        if bad=='queue':db.access=False
        if bad=='parent':db.rows[0]['case_id']=uuid4()
        with pytest.raises((AuthError,DraftError)):
            await StaffCorrespondence(StaffMessages(db)).read(db.actor,db.case_id,db.member,**kwargs)
    asyncio.run(run())
