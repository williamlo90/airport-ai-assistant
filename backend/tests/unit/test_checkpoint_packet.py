"""Packet candidates still pass current ownership, session and source gates."""
import asyncio,copy
from uuid import uuid4
import pytest
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from test_agent_checkpoints_private import setup
from test_agent_private_context import labels


def packet_fixture():
 f,store,model,engine,request=setup()
 f.conv.tenant_id=f.identity.session.tenant_id
 f.conv.customer_principal_id=f.identity.session.principal_id
 f.conv.anonymous_session_id=None
 async def packet(id):
  return copy.copy(f.conv),await store.run(id),store.active
 store.checkpoint_candidate=packet
 return f,store,model,engine,request


def test_packet_reloads_labels_after_external_grants_and_blocks_changed_revision():
 async def scenario():
  f,store,model,engine,request=packet_fixture()
  id=await engine.start(f.identity,request)
  store.rows[id]['resource_security_labels']=list(labels())
  async def changed(**kwargs):
   assert not store.in_tx
   store.rows[id]['revision']+=1
   return True
  f.private.allows.side_effect=changed
  with pytest.raises(DraftError):await engine.load(f.identity,id)
  assert model.calls==[]
 asyncio.run(scenario())


@pytest.mark.parametrize('change',['session','principal','tenant','conversation','missing_parent','missing_run','session_inactive','foreign_owner'])
def test_packet_is_not_authority(change):
 async def scenario():
  f,store,model,engine,request=packet_fixture();id=await engine.start(f.identity,request)
  original=store.checkpoint_candidate
  async def packet(run_id):
   conv,row,active=await original(run_id)
   if change in {'session','principal','tenant','conversation'}:
    row[{'session':'session_id','principal':'principal_id','tenant':'tenant_id','conversation':'conversation_id'}[change]]=uuid4()
   elif change=='missing_parent':conv=None
   elif change=='missing_run':row=None
   elif change=='session_inactive':active=False
   else:conv.customer_principal_id=uuid4()
   return conv,row,active
  store.checkpoint_candidate=packet;f.private.allows.reset_mock()
  with pytest.raises(AuthError) as error:await engine.load(f.identity,id)
  assert error.value.status==(401 if change=='session_inactive' else 404)
  f.private.allows.assert_not_awaited();assert model.calls==[]
 asyncio.run(scenario())


def test_packet_does_not_skip_final_session_check():
 async def scenario():
  f,store,model,engine,request=packet_fixture();id=await engine.start(f.identity,request)
  original=store.checkpoint_candidate
  async def packet(run_id):
   result=await original(run_id);store.active=False
   return result
  store.checkpoint_candidate=packet
  with pytest.raises(AuthError) as error:await engine.load(f.identity,id)
  assert error.value.status==401 and model.calls==[]
 asyncio.run(scenario())
