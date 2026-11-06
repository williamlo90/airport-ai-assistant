"""Public knowledge metadata must match actual JSONB round-trip under private capability."""
import asyncio,json
from unittest.mock import AsyncMock
import pytest
from test_ai_publication_private import setup
from test_knowledge_evidence import Fixture
from airport_assistant.modules.agent.answers import PublicAnswers,Grounding
from airport_assistant.modules.drafts.models import DraftError

@pytest.mark.parametrize('tamper',[False,True])
def test_grounded_public_answer_json_roundtrip_preserves_exact_metadata_or_rolls_back(tamper):
    async def run():
        identity,ticket,store,access,service,_,_=setup();k=Fixture();k.identity=identity;k.now=store.time
        store.conv['resource_security_labels']=[];original_context=store.ai_publication_context
        async def public(*args):
            result=await original_context(*args);result['run_labels']=[];return result
        store.ai_publication_context=public
        store.lock_knowledge_answer=AsyncMock();store.current_answer_chunks=AsyncMock(side_effect=k.evidence_chunks)
        service.answers=PublicAnswers(k.evidence)
        result=await k.evidence.collect(identity,k.selection);grounding=Grounding(k.selection,result.snapshot,('source_1',))
        body=service.answers.compiled('en',result,grounding.source_ids)
        original_write=store.ai_write_publication
        async def jsonb(*args,**kwargs):
            saved=await original_write(*args,**kwargs)
            stored=store.graph['message']['knowledge_evidence']
            store.graph['message']['knowledge_evidence']=json.loads(json.dumps(stored))
            if tamper:store.graph['message']['knowledge_evidence']['citations'][0]['quote']='Altered quote'
            return saved
        store.ai_write_publication=jsonb
        await service.capture_bound(identity,store.conv['id'],ticket,2,3)
        if tamper:
            with pytest.raises(DraftError):await service.publish(identity,ticket,body,grounding=grounding)
            assert store.graph is None and store.g['state']=='pending'
        else:
            saved=await service.publish(identity,ticket,body,grounding=grounding)
            assert saved['state']=='published' and store.graph['message']['knowledge_evidence']['citations'][0]['quote']==k.rows[0]['body']
        access.allows.assert_not_awaited()
    asyncio.run(run())
