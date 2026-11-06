"""Parameterized tenant-scoped knowledge storage; policy decisions stay in service."""
from contextlib import asynccontextmanager
from psycopg import sql
from psycopg.types.json import Jsonb
from airport_assistant.infrastructure.database.migrations import validate_schema
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.knowledge.models import Applicability

DOCUMENT=('id','organization_id','document_key','title','revision','created_by','created_at','updated_at')
VERSION=('id','document_id','version','revision','body','source_uri','service_type','language','visibility','organization_id','allowed_roles','valid_from','valid_to','applicability','policy_reference','supersedes_version_id','content_hash','basis_hash','status','published_at','published_by','withdrawn_at','withdrawn_by','withdrawal_reason','index_generation','created_by','created_at','updated_at')
CONTENT=('body','source_uri','service_type','language','visibility','organization_id','allowed_roles','valid_from','valid_to','applicability','policy_reference','supersedes_version_id')
AUDIT=('id','document_id','version_id','document_revision','version_revision','actor_principal_id','actor_membership_id','event_type','basis_hash','index_generation','reason','created_at')

def row(fields,value):
    if value is None:return None
    result=dict(zip(fields,value))
    if 'applicability' in result:result['applicability']=Applicability.model_validate(result['applicability']).model_dump()
    return result

class PostgresKnowledge:
    def __init__(self,database,schema='app'):validate_schema(schema);self.database,self.schema=database,schema
    @asynccontextmanager
    async def transaction(self,context):
        try:
            async with self.database.transaction(context) as c:yield KnowledgeSQL(c,self.schema,context)
        except DatabaseError as e:
            if e.sqlstate=='42501':
                async with self.database.transaction(context) as c:
                    if not await KnowledgeSQL(c,self.schema,context).session_active():raise AuthError(401,'AUTH_REQUIRED') from None
            if e.sqlstate=='Z0024':raise DraftError(503,'KNOWLEDGE_INDEX_UNAVAILABLE') from None
            if e.sqlstate=='23505':raise DraftError(409,'KNOWLEDGE_CONFLICT') from None
            if e.sqlstate is None or e.sqlstate.startswith('08') or e.sqlstate in {'57014','55P03','40001','40P01'}:
                raise DraftError(503,'KNOWLEDGE_UNAVAILABLE') from None
            raise
class KnowledgeSQL:
    def __init__(self,c,schema,context):self.c,self.schema,self.context=c,schema,context
    def query(self,statement):return sql.SQL(statement).format(s=sql.Identifier(self.schema))
    async def one(self,statement,params=()):return await(await self.c.execute(self.query(statement),params)).fetchone()
    async def session_active(self):return (await self.one('SELECT {s}.knowledge_session_valid()'))[0]
    async def admin(self,mid,org):
        scope=(await self.one('SELECT {s}.knowledge_scope(%s,%s)',(mid,org)))[0]
        if scope is not None:await self.c.execute("SELECT set_config('app.knowledge_membership_id',%s,true)",(str(mid),))
        return scope
    async def document(self,id,lock=False):return row(DOCUMENT,await self.one('SELECT '+','.join(DOCUMENT)+' FROM {s}.knowledge_documents WHERE tenant_id=%s AND id=%s'+(' FOR UPDATE' if lock else ''),(self.context.tenant_id,id)))
    async def documents(self,organization,after,limit,key=None):
        statement='SELECT '+','.join(DOCUMENT)+' FROM {s}.knowledge_documents WHERE tenant_id=%s AND organization_id=%s'
        params=[self.context.tenant_id,organization]
        if after is not None:statement+=' AND id>%s';params.append(after)
        if key is not None:statement+=' AND document_key=%s';params.append(key)
        statement+=' ORDER BY id LIMIT %s';params.append(limit)
        values=await(await self.c.execute(self.query(statement),tuple(params))).fetchall()
        return [row(DOCUMENT,v) for v in values]

    async def parent(self,id):
        value=await self.one('SELECT document_id FROM {s}.knowledge_versions WHERE tenant_id=%s AND id=%s',(self.context.tenant_id,id))
        return value[0] if value else None
    async def predecessor(self,doc,id):
        value=await self.one('SELECT version FROM {s}.knowledge_versions WHERE tenant_id=%s AND document_id=%s AND id=%s',(self.context.tenant_id,doc,id))
        return value[0] if value else None
    async def version(self,id,lock=False):return row(VERSION,await self.one('SELECT '+','.join(VERSION)+' FROM {s}.knowledge_versions WHERE tenant_id=%s AND id=%s'+(' FOR UPDATE' if lock else ''),(self.context.tenant_id,id)))
    async def create_document(self,c,org):
        return (await self.one('INSERT INTO {s}.knowledge_documents(tenant_id,organization_id,document_key,title,created_by) VALUES(%s,%s,%s,%s,%s) RETURNING id',(self.context.tenant_id,org,c.document_key,c.title,self.context.principal_id)))[0]
    async def values(self,content,doc):
        d=await self.document(doc)
        data=content.model_dump();data['organization_id']=d['organization_id'] if content.visibility=='staff' else None
        data['allowed_roles']=list(content.allowed_roles);data['applicability']=Jsonb(content.applicability.model_dump(mode='json'))
        return tuple(data[k] for k in CONTENT)
    async def create_version(self,doc,content):
        values=await self.values(content,doc)
        n=(await self.one('SELECT COALESCE(max(version),0)+1 FROM {s}.knowledge_versions WHERE tenant_id=%s AND document_id=%s',(self.context.tenant_id,doc)))[0]
        columns=','.join(CONTENT);placeholders=','.join(['%s']*len(CONTENT))
        return (await self.one('INSERT INTO {s}.knowledge_versions(tenant_id,document_id,version,created_by,'+columns+') VALUES(%s,%s,%s,%s,'+placeholders+') RETURNING id',(self.context.tenant_id,doc,n,self.context.principal_id,*values)))[0]
    async def edit(self,id,content):
        parent=await self.parent(id);values=await self.values(content,parent)
        await self.one('UPDATE {s}.knowledge_versions SET '+','.join(k+'=%s' for k in CONTENT)+',revision=revision+1 WHERE tenant_id=%s AND id=%s RETURNING id',(*values,self.context.tenant_id,id))
        return await self.version(id)
    async def published(self,doc):
        values=await(await self.c.execute(self.query('SELECT '+','.join(VERSION)+" FROM {s}.knowledge_versions WHERE tenant_id=%s AND document_id=%s AND status='published'"),(self.context.tenant_id,doc))).fetchall()
        return [row(VERSION,v) for v in values]
    async def publish(self,id,proof):
        await self.one("UPDATE {s}.knowledge_versions SET status='published',revision=revision+1,index_generation=%s,index_basis_hash=%s,index_valid_until=%s WHERE tenant_id=%s AND id=%s RETURNING id",(proof.generation,proof.basis_hash,proof.valid_until,self.context.tenant_id,id))
        return await self.version(id)
    async def withdraw(self,id,reason):
        await self.one("UPDATE {s}.knowledge_versions SET status='withdrawn',revision=revision+1,withdrawal_reason=%s WHERE tenant_id=%s AND id=%s RETURNING id",(reason,self.context.tenant_id,id))
        return await self.version(id)
    async def list(self,doc,after,limit,audit=False):
        fields=AUDIT if audit else VERSION;table='knowledge_audit' if audit else 'knowledge_versions';key='document_revision' if audit else 'version'
        values=await(await self.c.execute(self.query('SELECT '+','.join(fields)+' FROM {s}.'+table+' WHERE tenant_id=%s AND document_id=%s AND '+key+'>%s ORDER BY '+key+' LIMIT %s'),(self.context.tenant_id,doc,after,limit))).fetchall()
        return [row(fields,v) for v in values]
    async def select(self,c):
        # SQL authorization and status/applicability filtering precede returning body.
        where="""d.tenant_id=%s AND d.document_key=%s AND v.status='published' AND v.service_type=%s AND v.language=%s
          AND v.policy_reference IS NOT DISTINCT FROM %s AND v.valid_from<=%s AND(v.valid_to IS NULL OR %s<v.valid_to)
          AND {s}.knowledge_can_read(v.visibility,v.organization_id,v.allowed_roles)
          AND(v.applicability->>'terminal' IS NULL OR v.applicability->>'terminal'=%s)
          AND(v.applicability->>'booking_created_from' IS NULL OR(v.applicability->>'booking_created_from')::timestamptz<=%s)
          AND(v.applicability->>'booking_created_to' IS NULL OR %s<(v.applicability->>'booking_created_to')::timestamptz)"""
        fields=','.join('v.'+k for k in VERSION)
        result=await(await self.c.execute(self.query('SELECT '+fields+' FROM {s}.knowledge_versions v JOIN {s}.knowledge_documents d ON(d.tenant_id,d.id)=(v.tenant_id,v.document_id) WHERE '+where+' ORDER BY v.id LIMIT 2'),(self.context.tenant_id,c.document_key,c.service_type,c.language,c.policy_reference,c.as_of,c.as_of,c.terminal,c.booking_created_at,c.booking_created_at))).fetchall()
        return [row(VERSION,v) for v in result]

    async def index_proof(self,version,basis):
        from airport_assistant.modules.knowledge.retrieval import proof
        value=await self.one('''SELECT c.generation FROM {s}.knowledge_chunks c
            WHERE c.tenant_id=%s AND c.version_id=%s AND c.basis_hash=%s
            AND {s}.knowledge_index_complete(c.version_id,c.basis_hash,c.generation)
            ORDER BY c.generation LIMIT 1''',(self.context.tenant_id,version,basis))
        return proof(self.context.tenant_id,version,basis,value[0]) if value else None

    async def store_chunks(self,version,basis,generation,chunks,vectors):
        for chunk,vector in zip(chunks,vectors,strict=True):
            await self.c.execute(self.query('''INSERT INTO {s}.knowledge_chunks
                (tenant_id,version_id,generation,basis_hash,ordinal,start_offset,body,embedding)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s::public.vector)'''),
                (self.context.tenant_id,version,generation,basis,chunk.ordinal,chunk.start,chunk.body,vector_literal(vector)))

    async def search_chunks(self,c,vector):
        statement=EVIDENCE_SCOPE+" SELECT "+EVIDENCE_FIELDS+""",
            1-(embedding OPERATOR(public.<=>) %s::public.vector) AS score
            FROM eligible e CROSS JOIN scope_token st
            ORDER BY score DESC,version_id,ordinal LIMIT %s"""
        values=selection_params(self.context,c)+(vector_literal(vector),c.limit)
        rows=await(await self.c.execute(self.query(statement),values)).fetchall()
        return [dict(zip(EVIDENCE_KEYS+('score',),v)) for v in rows]

    async def evidence_chunks(self,c,citations):
        # Cached citations never supply body/URI or override selection/actor context.
        requested=Jsonb([{'version_id':str(r.version_id),'generation':str(r.generation),'ordinal':r.ordinal,
                         'basis_hash':r.basis_hash,'document_revision':r.document_revision} for r in citations])
        statement=EVIDENCE_SCOPE+" SELECT "+EVIDENCE_FIELDS+"""
            FROM eligible e CROSS JOIN scope_token st WHERE EXISTS(
              SELECT 1 FROM jsonb_to_recordset(%s::jsonb) AS ref(version_id uuid,generation uuid,
                ordinal integer,basis_hash text,document_revision integer)
              WHERE (e.version_id,e.generation,e.ordinal,e.basis_hash,e.document_revision)=
                    (ref.version_id,ref.generation,ref.ordinal,ref.basis_hash,ref.document_revision))
            ORDER BY e.version_id,e.ordinal"""
        rows=await(await self.c.execute(self.query(statement),selection_params(self.context,c)+(requested,))).fetchall()
        return [dict(zip(EVIDENCE_KEYS,v)) for v in rows]


# These CTEs are shared by ranking and cache consumption: both must apply the
# exact same current ACL/applicability predicates before exposing source text.
EVIDENCE_SCOPE="""WITH versions AS MATERIALIZED(
    SELECT v.id AS version_id,v.index_generation AS generation,v.basis_hash,v.document_id,
      v.version,v.source_uri,d.revision AS document_revision
    FROM {s}.knowledge_versions v JOIN {s}.knowledge_documents d
      ON(d.tenant_id,d.id)=(v.tenant_id,v.document_id)
    WHERE v.tenant_id=%s AND (%s::text IS NULL OR d.document_key=%s) AND v.status='published'
      AND v.service_type=%s AND v.language=%s AND v.policy_reference IS NOT DISTINCT FROM %s
      AND v.valid_from<=%s AND(v.valid_to IS NULL OR %s<v.valid_to)
      AND {s}.knowledge_can_read(v.visibility,v.organization_id,v.allowed_roles)
      AND(v.applicability->>'terminal' IS NULL OR v.applicability->>'terminal'=%s)
      AND(v.applicability->>'booking_created_from' IS NULL OR(v.applicability->>'booking_created_from')::timestamptz<=%s)
      AND(v.applicability->>'booking_created_to' IS NULL OR %s<(v.applicability->>'booking_created_to')::timestamptz)),
    conflicts AS MATERIALIZED(SELECT document_id FROM versions GROUP BY document_id HAVING count(*)>1),
    scope_token AS MATERIALIZED(SELECT encode(sha256(convert_to(COALESCE(string_agg(
      version_id::text||':'||generation::text||':'||basis_hash||':'||document_revision::text,
      ',' ORDER BY version_id),''),'UTF8')),'hex') AS scope_token FROM versions),
    eligible AS MATERIALIZED(SELECT ch.ordinal,ch.body,ch.embedding,v.* FROM versions v
      JOIN {s}.knowledge_chunks ch ON ch.tenant_id={s}.current_tenant_id()
      AND(ch.version_id,ch.generation,ch.basis_hash)=(v.version_id,v.generation,v.basis_hash)) """
EVIDENCE_FIELDS="""e.version_id,e.generation,e.ordinal,e.body,e.source_uri,e.basis_hash,
    e.document_id,e.version,e.document_revision,st.scope_token,
    EXISTS(SELECT 1 FROM conflicts c WHERE c.document_id=e.document_id) AS ambiguous"""
EVIDENCE_KEYS=('version_id','generation','ordinal','body','source_uri','basis_hash','document_id',
               'version','document_revision','scope_token','ambiguous')


def selection_params(context,c):
    return (context.tenant_id,c.document_key,c.document_key,c.service_type,c.language,c.policy_reference,
            c.as_of,c.as_of,c.terminal,c.booking_created_at,c.booking_created_at)


def vector_literal(vector):
    from airport_assistant.modules.knowledge.retrieval import normalized
    return '['+','.join(str(v) for v in normalized(vector))+']'
