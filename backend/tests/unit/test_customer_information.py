"""Original intent, once-only confirmation and current-authority recovery."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.cases.information import CustomerInformation
from airport_assistant.modules.cases.information_preview import InformationInput,InformationPreviewCodec
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure


class Fixture:
    def __init__(self,private=False):
        self.identity=SimpleNamespace(database_context=None,session=SimpleNamespace(
            kind='customer',tenant_id=uuid4(),principal_id=uuid4(),id=uuid4()))
        self.cid=uuid4();self.clock=[1000];self.rows={};self.writes=0;self.active=0;self.expire_on_exit=False
        self.case=dict(id=self.cid,owner_principal_id=self.identity.session.principal_id,version=3,status='awaiting_customer',
            next_actor='customer',next_action='Please provide details',resource_security_labels=[dict(source='reference',resource_type='booking',reference='B1',required_source_scopes=['booking:read'])] if private else [])
        self.access=SimpleNamespace(allows=AsyncMock(return_value=True))
        self.codec=InformationPreviewCodec(b'p'*32,clock=lambda:self.clock[0])
        self.service=CustomerInformation(self,self.codec,self.access)
        self.command=InformationInput(expected_version=3,message='My original information.',language='en')

    @asynccontextmanager
    async def transaction(self,context):
        snapshot=deepcopy((self.case,self.rows,self.writes));self.active+=1
        try:
            yield self
        except BaseException:
            self.case,self.rows,self.writes=snapshot
            raise
        finally:
            self.active-=1
        if self.expire_on_exit and self.writes:self.clock[0]=1900

    async def session_active(self):return True
    async def information_ready(self):return True
    async def languages(self):return ['en','id']
    async def find_case(self,id,**kwargs):return deepcopy(self.case) if id==self.cid else None
    async def customer_ancestry(self,id):return deepcopy(self.case['resource_security_labels'])
    async def information_key_lock(self,key):pass
    async def information_receipt(self,*,key=None,preview=None):
        return next((deepcopy(r) for r in self.rows.values() if (r['request_key']==key if key is not None else r['id']==preview)),None)
    async def publish_information(self,record,key,digest,message_id,labels):
        self.writes+=1;self.case['version']+=1
        self.rows[key]=dict(id=record.preview_id,case_id=self.cid,request_key=key,request_hash=digest,
            message_id=message_id,message=record.message,stored_body=record.message,language=record.language,
            stored_language=record.language,expected_version=record.expected_version,sequence=2,
            resource_security_labels=labels,message_labels=labels)
    def run(self,awaitable):return asyncio.run(awaitable)
    def preview(self):return self.run(self.service.preview(self.identity,self.cid,self.command))
    def confirm(self,preview,key):return self.run(self.service.confirm(self.identity,self.cid,preview['confirmation_token'],key))


def test_once_original_message_revision_status_preserved_and_current_receipt():
    f=Fixture();preview=f.preview();key=uuid4().hex;before=deepcopy(f.case)
    saved=f.confirm(preview,key)
    assert saved['case_version']==4 and f.writes==1
    assert {k:v for k,v in f.case.items() if k!='version'}=={k:v for k,v in before.items() if k!='version'}
    assert f.confirm(preview,key)==saved and f.writes==1
    with pytest.raises(DraftError) as e:f.confirm(preview,uuid4().hex)
    assert e.value.code=='IDEMPOTENCY_CONFLICT' and f.writes==1
    f.clock[0]=3000;f.case.update(status='closed',version=9)
    assert f.run(f.service.receipt(f.identity,f.cid,key))==saved
    assert f.confirm(preview,key)==saved and f.writes==1


def test_preview_is_not_durable_write_and_closed_or_stale_preview_rejected():
    f=Fixture();f.preview();assert not f.rows and f.writes==0
    f.case['status']='closed'
    with pytest.raises(DraftError) as e:f.preview()
    assert e.value.code=='CASE_CLOSED'
    f.case.update(status='new',version=4)
    with pytest.raises(DraftError) as e:f.preview()
    assert e.value.code=='VERSION_CONFLICT'


def test_expired_new_intent_never_publishes_but_missing_receipt_remains_unknown():
    f=Fixture();preview=f.preview();f.clock[0]=1900
    with pytest.raises(DraftError) as e:f.confirm(preview,uuid4().hex)
    assert e.value.code=='PREVIEW_EXPIRED' and f.writes==0
    with pytest.raises(AuthError) as e:f.run(f.service.receipt(f.identity,f.cid,uuid4().hex))
    assert e.value.status==404


@pytest.mark.parametrize('change',['version','closed','owner'])
def test_changed_parent_between_preview_and_confirm_denies_before_write(change):
    f=Fixture();preview=f.preview()
    if change=='version':f.case['version']+=1
    elif change=='closed':f.case['status']='closed'
    else:f.case['owner_principal_id']=uuid4()
    with pytest.raises((DraftError,AuthError)):f.confirm(preview,uuid4().hex)
    assert f.writes==0


def test_private_grants_between_transactions_denied_and_changed_context_fail_closed():
    f=Fixture(private=True)
    async def allowed(**kwargs):assert f.active==0;return True
    f.access.allows.side_effect=allowed
    preview=f.preview()
    async def change(**kwargs):assert f.active==0;f.case['version']+=1;return True
    f.access.allows.side_effect=change
    with pytest.raises((DraftError,AuthError)):f.confirm(preview,uuid4().hex)
    assert f.writes==0
    f=Fixture(private=True);f.access.allows.return_value=False
    with pytest.raises(AuthError):f.preview()
    assert f.writes==0


def test_different_preview_cannot_adopt_key_and_case_mismatch_is_concealed():
    f=Fixture();preview=f.preview();other=f.preview();key=uuid4().hex
    f.confirm(preview,key)
    with pytest.raises(DraftError) as e:f.confirm(other,key)
    assert e.value.code=='IDEMPOTENCY_CONFLICT' and f.writes==1
    with pytest.raises(AuthError):f.run(f.service.receipt(f.identity,uuid4(),key))


def test_post_commit_expiry_is_unknown503_and_get_still_recovers():
    f=Fixture();preview=f.preview();key=uuid4().hex;f.expire_on_exit=True
    with pytest.raises(SourceFailure):f.confirm(preview,key)
    assert f.writes==1
    assert f.run(f.service.receipt(f.identity,f.cid,key))['state']=='saved'


def test_current_source_denial_withholds_saved_receipt_after_close():
    f=Fixture(private=True);preview=f.preview();key=uuid4().hex;f.confirm(preview,key)
    f.case.update(status='closed',version=5);f.access.allows.return_value=False
    with pytest.raises(AuthError):f.run(f.service.receipt(f.identity,f.cid,key))
    assert f.writes==1


def test_slow_source_cannot_reanchor_freshness_after_await():
    f=Fixture(private=True);clock=[0];f.service.clock=lambda:clock[0]
    async def slow(**kwargs):clock[0]=2.01;return True
    f.access.allows.side_effect=slow
    with pytest.raises(SourceFailure):f.preview()
    assert f.writes==0


def test_source_loop_cancelled_before_later_references_after_budget():
    from airport_assistant.modules.source.labels import PrivateSourceAccess
    f=Fixture(private=True);calls=[]
    async def has_grants(**kwargs):
        calls.append(kwargs['resource_reference']);await asyncio.sleep(.05);return True
    f.case['resource_security_labels'].append({**f.case['resource_security_labels'][0],'reference':'B2'})
    f.service._source=PrivateSourceAccess(SimpleNamespace(has_grants=has_grants))
    clock=iter([0,1.99]);f.service.clock=lambda:next(clock)
    with pytest.raises(SourceFailure):f.preview()
    assert calls==['B1']
