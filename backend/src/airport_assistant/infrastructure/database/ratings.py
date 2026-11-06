"""Rating persistence; parent locks precede the single target feedback row."""
from psycopg.types.json import Jsonb
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL

FIELDS=('id','score','comment','version','created_at','updated_at','resource_security_labels')


class PostgresRatings(PostgresCaseLifecycle):
    def unit(self,connection,context):return RatingSQL(connection,self._schema,context)


class RatingSQL(LifecycleSQL):
    async def ratings_ready(self):
        present=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',(self._schema+'.ratings_ready()',))).fetchone())[0]
        return bool(present and(await(await self._c.execute(self.query('SELECT {s}.ratings_ready()'))).fetchone())[0])

    async def rating_target(self,kind,id,*,lock=False):
        return(await(await self._c.execute(self.query('SELECT {s}.rating_target(%s,%s,%s)'),(kind,id,lock))).fetchone())[0]

    async def rating(self,kind,id,*,lock=False):
        column='case_id' if kind=='case' else 'conversation_id'
        row=await(await self._c.execute(self.query('SELECT '+','.join(FIELDS)+' FROM {s}.ratings WHERE tenant_id=%s AND '+column+'=%s'+(' FOR UPDATE' if lock else '')),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(FIELDS,row)) if row else None

    async def save_rating(self,kind,target,id,version,command,labels):
        ctx=self._context
        # The guard computes labels from the locked current parent. Runtime has
        # no INSERT/UPDATE privilege on classification; after_loader verifies it.
        if version==0:
            column='case_id' if kind=='case' else 'conversation_id'
            await self._c.execute(self.query('INSERT INTO {s}.ratings(tenant_id,id,'+column+''',score,comment,version,created_session_id,updated_session_id)
              SELECT %s,%s,%s,%s,%s,1,%s,%s WHERE {s}.rating_target(%s,%s,true)->'resource_security_labels'=%s::jsonb'''),
              (ctx.tenant_id,id,target,command.score,command.comment,ctx.session_id,ctx.session_id,kind,target,Jsonb(labels)))
        else:
            await self._c.execute(self.query('''UPDATE {s}.ratings SET score=%s,comment=%s,version=version+1,updated_session_id=%s
              WHERE tenant_id=%s AND id=%s AND version=%s
                AND {s}.rating_target(%s,%s,true)->'resource_security_labels'=%s::jsonb'''),
              (command.score,command.comment,ctx.session_id,ctx.tenant_id,id,version,kind,target,Jsonb(labels)))
