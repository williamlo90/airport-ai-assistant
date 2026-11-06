"""Ordered wire batch preserves fresh SQL statements and withholds partial rows."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
import pytest
from airport_assistant.infrastructure.database.agent_runs import AgentSQL,FIELDS

class Connection:
 def __init__(self,context,*,fail=False):
  self.context=context;self.fail=fail;self.inside=False;self.events=[];self.cid=uuid4();self.run_id=uuid4()
 async def execute(self,query,parameters=None):
  text=query.as_string();self.events.append((text,parameters))
  if 'SELECT conversation_id' in text:return Cursor(self,(self.cid,),False)
  if '."conversations"' in text:raise AssertionError('Unexpected identifier representation')
  if 'conversations WHERE' in text:
   row=(self.cid,self.context.tenant_id,self.context.principal_id,None,'en',2,1,1,None,[],'ai')
  elif 'conversation_session_valid()' in text:row=(True,)
  else:
   data=dict.fromkeys(FIELDS);data.update(id=self.run_id,conversation_id=self.cid,tenant_id=self.context.tenant_id,resource_security_labels=[])
   row=tuple(data[k] for k in FIELDS)
  return Cursor(self,row,True)
 @asynccontextmanager
 async def pipeline(self):
  self.inside=True
  try:yield
  finally:self.inside=False
  if self.fail:raise RuntimeError('Synthetic pipeline synchronization failure')

class Cursor:
 def __init__(self,connection,row,queued):self.connection,self.row,self.queued=connection,row,queued
 async def fetchone(self):
  assert not self.connection.inside,'No candidate is materialized before wire sync'
  self.connection.events.append(('fetch',self.queued));return self.row


def test_ordered_packet_uses_tenant_bound_statements_and_fetches_only_after_sync():
 async def scenario():
  ctx=SimpleNamespace(tenant_id=uuid4(),principal_id=uuid4());c=Connection(ctx)
  conv,run,active=await AgentSQL(c,'app',ctx).checkpoint_candidate(c.run_id)
  statements=[e for e in c.events if e[0]!='fetch']
  assert len(statements)==4
  assert 'conversations WHERE' in statements[1][0] and 'FOR UPDATE' in statements[1][0]
  assert 'agent_runs WHERE' in statements[2][0] and 'FOR UPDATE' in statements[2][0]
  assert 'conversation_session_valid()' in statements[3][0]
  assert statements[1][1]==(ctx.tenant_id,c.cid) and statements[2][1]==(ctx.tenant_id,c.run_id)
  assert conv.id==c.cid and run['id']==c.run_id and active is True
  assert len([e for e in c.events if e[0]=='fetch' and e[1]])==3
 asyncio.run(scenario())


def test_sync_failure_never_returns_or_fetches_partial_packet():
 async def scenario():
  ctx=SimpleNamespace(tenant_id=uuid4(),principal_id=uuid4());c=Connection(ctx,fail=True)
  with pytest.raises(RuntimeError):await AgentSQL(c,'app',ctx).checkpoint_candidate(c.run_id)
  assert not any(e[0]=='fetch' and e[1] for e in c.events)
 asyncio.run(scenario())
