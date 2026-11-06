"""Public extractive answers: the model selects evidence, the server copies facts."""
from dataclasses import dataclass,field
from datetime import timedelta
import hmac
from typing import Literal
from pydantic import Field,model_validator
from airport_assistant.modules.knowledge.evidence import Snapshot
from airport_assistant.modules.knowledge.retrieval import Search
from .models import Model,AgentError


class AnswerSelection(Model):
    outcome:Literal['answer','insufficient','conflict']
    source_ids:tuple[str,...]=Field(max_length=3,strict=False)
    @model_validator(mode='after')
    def shape(self):
        if (self.outcome=='answer')!=bool(self.source_ids) or len(set(self.source_ids))!=len(self.source_ids):
            raise ValueError('Choose unique sources only for an answer.')
        return self


class KnowledgeAnswerUnavailable(Exception):
    """Abort the factual write, then expose a safe handoff outcome."""


def staff_offer(language):
    return ('Saya belum menemukan informasi terkini yang cukup pasti. Anda dapat meminta bantuan petugas.' if language=='id'
        else 'I could not confirm reliable current information. You can request staff assistance.')


@dataclass(frozen=True)
class Grounding:
    selection:Search=field(repr=False)
    snapshot:Snapshot=field(repr=False)
    source_ids:tuple[str,...]


def render(language,citations):
    header='Informasi bandara yang dipublikasikan:' if language=='id' else 'Published airport information:'
    return header+'\n\n'+'\n\n'.join(c['quote']+'\n['+str(i)+'] '+c['source_uri'] for i,c in enumerate(citations,1))


class PublicAnswers:
    def __init__(self,evidence):self.evidence=evidence

    @staticmethod
    def public(selection,language):
        if (selection.language!=language or any(getattr(selection,key) is not None for key in ('policy_reference','terminal','booking_created_at'))):
            raise AgentError(422,'AGENT_INVALID_INPUT')

    @staticmethod
    def candidates(result):
        return tuple({'source_id':'source_'+str(i),'text':chunk.body,'authority':'untrusted_source_text'} for i,chunk in enumerate(result.chunks,1))

    @staticmethod
    def usable(result):
        if result.status!='sources_available' or result.snapshot is None:return result.status
        if len({c.citation.document_id for c in result.chunks})!=1:return 'conflicting_sources'
        return None

    @staticmethod
    def citations(result,ids):
        by_id={'source_'+str(i):chunk for i,chunk in enumerate(result.chunks,1)}
        if not ids or len(ids)>3 or len(set(ids))!=len(ids) or any(id not in by_id for id in ids):
            raise AgentError(502,'AGENT_INVALID_RESPONSE')
        return tuple(dict(by_id[id].citation.model_dump(mode='json',exclude={'score'}),quote=by_id[id].body,source_uri=by_id[id].source_uri) for id in ids)

    def compiled(self,language,result,ids):
        citations=self.citations(result,ids)
        body=render(language,citations)
        if len(body)>4000:raise AgentError(413,'AGENT_CONTEXT_LIMIT')
        return body

    async def validate_transaction(self,identity,grounding,tx,language,body):
        # No nested database unit/network/provider under the publication lock.
        ev=self.evidence;c=grounding.selection;s=grounding.snapshot;a=identity.session
        self.public(c,language)
        age=(ev.clock()-s.issued_at).total_seconds()
        if (not hmac.compare_digest(s.signature,ev.signature(s)) or not 0<=age<ev.policy.ttl_seconds
            or (s.tenant_id,s.session_id,s.principal_id,s.actor_kind)!=(a.tenant_id,a.id,a.principal_id,a.kind)
            or s.actor_kind not in {'anonymous','customer'} or s.context_key!=ev.context(c) or s.policy_id!=ev.policy.policy_id):
            raise KnowledgeAnswerUnavailable()
        await tx.lock_knowledge_answer()
        now=await tx.now()
        if not 0<=(now-s.issued_at).total_seconds()<ev.policy.ttl_seconds:raise KnowledgeAnswerUnavailable()
        rows=await tx.current_answer_chunks(c.model_copy(update={'as_of':now}),s.citations)
        by_ref={(r['version_id'],r['generation'],r['ordinal']):r for r in rows}
        if len(rows)!=len(s.citations) or any(r['ambiguous'] or r['scope_token']!=s.scope_token for r in rows):
            raise KnowledgeAnswerUnavailable()
        from airport_assistant.modules.knowledge.evidence import EvidenceResult,SourceChunk
        chunks=[]
        for citation in s.citations:
            row=by_ref.get((citation.version_id,citation.generation,citation.ordinal))
            if row is None or (row['basis_hash'],row['document_revision'],row['document_id'])!=(citation.basis_hash,citation.document_revision,citation.document_id):
                raise KnowledgeAnswerUnavailable()
            chunks.append(SourceChunk(citation,row['body'],row['source_uri']))
        current=EvidenceResult('sources_available','current',tuple(chunks),s)
        if self.usable(current) or self.compiled(language,current,grounding.source_ids)!=body:raise KnowledgeAnswerUnavailable()
        return dict(service_type=c.service_type,document_key=c.document_key,scope_token=s.scope_token,
            issued_at=s.issued_at.isoformat(),expires_at=(s.issued_at+timedelta(seconds=ev.policy.ttl_seconds)).isoformat(),
            # JSON arrays must remain lists across PostgreSQL JSONB round-trips.
            citations=list(self.citations(current,grounding.source_ids)))
