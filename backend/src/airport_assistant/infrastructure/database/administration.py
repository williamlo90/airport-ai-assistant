"""Bounded metadata RPCs; runtime cannot directly rewrite memberships."""
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL


class PostgresAdministration(PostgresCaseLifecycle):
    def unit(self,connection,context):return AdministrationSQL(connection,self._schema,context)


class AdministrationSQL(LifecycleSQL):
    async def administration_ready(self):
        present=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',(self._schema+'.administration_ready()',))).fetchone())[0]
        return bool(present and(await(await self._c.execute(self.query('SELECT {s}.administration_ready()'))).fetchone())[0])

    async def admin_workspace(self,actor):
        return(await(await self._c.execute(self.query('SELECT {s}.admin_workspace(%s)'),(actor,))).fetchone())[0]

    async def admin_directory(self,actor,after,limit):
        return(await(await self._c.execute(self.query('SELECT {s}.admin_directory(%s,%s,%s)'),(actor,after,limit))).fetchone())[0]

    async def admin_member(self,actor,principal):
        return(await(await self._c.execute(self.query('SELECT {s}.admin_member(%s,%s)'),(actor,principal))).fetchone())[0]

    async def admin_change(self,principal,command):
        return(await(await self._c.execute(self.query('SELECT {s}.admin_change_membership(%s,%s,%s,%s,%s,%s,%s)'),
            (command.actor_membership_id,command.actor_version,principal,command.expected_version,command.active,
             sorted(command.roles),sorted(command.queue_ids,key=str)))).fetchone())[0]
