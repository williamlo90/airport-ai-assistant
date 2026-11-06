"""Read-only staff delivery projection; case RLS remains the access boundary."""
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle, LifecycleSQL


class PostgresDeliveryStatus(PostgresCaseLifecycle):
    def unit(self,connection,context):return DeliveryStatusSQL(connection,self._schema,context)


class DeliveryStatusSQL(LifecycleSQL):
    async def deliveries(self,case_id):
        rows=await(await self._c.execute(self.query("""SELECT j.id,COALESCE(e.app_message_id,j.source_message_id),e.id,
          COALESCE(e.delivery_status,CASE WHEN j.state IN('blocked','dead') THEN 'failed' WHEN j.state='unknown' THEN 'delivery_unknown' ELSE 'queued' END),
          e.delivery_version,COALESCE(e.needs_review,false) OR (e.id IS NULL AND j.state IN('blocked','dead','unknown')) OR
            (COALESCE(e.delivery_status,'') NOT IN('delivered','bounced','failed') AND COALESCE(r.state IN('blocked','dead','unknown'),false)),
          COALESCE(e.review_reason,CASE WHEN COALESCE(e.delivery_status,'') NOT IN('delivered','bounced','failed') AND r.state IN('blocked','dead','unknown') THEN 'RECONCILE_STOPPED' WHEN e.id IS NULL AND j.state IN('blocked','dead','unknown') THEN 'SEND_NOT_CONFIRMED' END),
          j.created_at,e.provider_status_at,e.last_checked_at
          FROM {s}.outbox_jobs j LEFT JOIN {s}.email_messages e ON e.tenant_id=j.tenant_id AND e.job_id=j.id
          LEFT JOIN {s}.outbox_jobs r ON r.tenant_id=e.tenant_id AND r.email_message_id=e.id AND r.kind='email_reconcile'
          WHERE j.tenant_id=%s AND j.case_id=%s AND j.kind IN('case_created','email_send') ORDER BY j.created_at DESC,j.id DESC LIMIT 101"""),
          (self._context.tenant_id,case_id))).fetchall()
        fields=('job_id','app_message_id','email_message_id','delivery_status','delivery_version','needs_review','review_reason','queued_at','provider_status_at','last_checked_at')
        return [dict(zip(fields,row)) for row in rows]
