"""Explicit source grants and field projections inside one read transaction."""

from datetime import datetime, timezone
import json
from airport_reference.security import SourceError, cursor_encode, cursor_decode
from airport_reference.store import active,grants,one


def hidden():raise SourceError(404,"RESOURCE_NOT_FOUND")


def stamp():return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")


def normal_status(value,known):return value if value in known else "unknown"


class SourceService:
    def __init__(self,store,cursor_secret):self.store=store;self.cursor_secret=cursor_secret

    def _check(self,c,caller):
        if not active(c,caller):hidden()

    def booking(self,caller,reference):
        with self.store.read() as c:
            self._check(c,caller)
            scope=grants(c,caller,"booking",reference)
            row=one(c,caller,"bookings",reference)
            if row is None or "booking:read" not in scope:hidden()
            fields=("reference","service_type","start_at","end_at","service_timezone","location","policy_reference","source_updated_at")
            value={key:row[key] for key in fields}
            value.update(status=normal_status(row["status"],{"confirmed","cancelled","pending"}),observed_at=stamp(),allowed_scopes=sorted(scope))
            return value

    def _transaction(self,c,caller,row,scope):
        fields=("reference","source_updated_at")
        value={key:row[key] for key in fields}
        value.update(status=normal_status(row["status"],{"pending","paid","failed","refunded"}),observed_at=stamp(),allowed_scopes=sorted(scope))
        if value["status"]=="unknown":value["source_status_label"]=row["status"]
        if "booking:read" in grants(c,caller,"booking",row["booking_reference"]):value["booking_reference"]=row["booking_reference"]
        if "payment_details:read" in scope:
            value.update(currency=row["currency"],amount_minor=row["amount_minor"],currency_exponent=row["currency_exponent"],refund_status=normal_status(row["refund_status"],{"none","pending","completed"}))
        return value

    def transaction(self,caller,reference):
        with self.store.read() as c:
            self._check(c,caller)
            row=one(c,caller,"transactions",reference);scope=grants(c,caller,"transaction",reference)
            if row is None or "payment_status:read" not in scope:hidden()
            return self._transaction(c,caller,row,scope)

    def policy(self,caller,reference):
        with self.store.read() as c:
            self._check(c,caller)
            row=one(c,caller,"policies",reference)
            permitted=c.execute("SELECT b.reference FROM bookings b JOIN relations r ON(r.tenant_id,r.resource_type,r.resource_reference)=(b.tenant_id,'booking',b.reference) WHERE b.tenant_id=? AND b.policy_reference=? AND r.subject_reference=?",(caller.tenant_id,reference,caller.subject)).fetchall()
            if row is None or not any("booking:read" in grants(c,caller,"booking",r[0]) for r in permitted):hidden()
            return {"reference":row["reference"],"service_type":row["service_type"],"version":row["version"],"effective_from":row["effective_from"],"instructions":json.loads(row["instructions"]),"source_updated_at":row["source_updated_at"],"observed_at":stamp()}

    def _page(self,c,caller,endpoint,filter_value,limit,cursor,items):
        generation=c.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]
        binding=dict(tenant=caller.tenant_id,subject=caller.subject,endpoint=endpoint,filter=filter_value,limit=limit,order="reference-v1",generation=generation)
        last=cursor_decode(self.cursor_secret,binding,cursor) if cursor else ""
        available=[(key,value) for key,value in items if key>last]
        selected=available[:limit]
        next_cursor=cursor_encode(self.cursor_secret,binding,selected[-1][0]) if len(available)>limit else None
        return {"items":[value for _,value in selected],"next_cursor":next_cursor}

    def booking_transactions(self,caller,reference,limit,cursor):
        with self.store.read() as c:
            self._check(c,caller)
            if one(c,caller,"bookings",reference) is None or "booking:read" not in grants(c,caller,"booking",reference):hidden()
            rows=c.execute("SELECT * FROM transactions WHERE tenant_id=? AND booking_reference=? ORDER BY reference",(caller.tenant_id,reference)).fetchall()
            items=[]
            for row in rows:
                scope=grants(c,caller,"transaction",row["reference"])
                if "payment_status:read" in scope:items.append((row["reference"],self._transaction(c,caller,row,scope)))
            if not items:hidden()
            return self._page(c,caller,"booking-transactions",reference,limit,cursor,items)

    def relations(self,caller,limit,cursor):
        with self.store.read() as c:
            self._check(c,caller)
            rows=c.execute("SELECT resource_type,resource_reference,relation,scopes FROM relations WHERE tenant_id=? AND subject_reference=? ORDER BY resource_type,resource_reference",(caller.tenant_id,caller.subject)).fetchall()
            items=[]
            for row in rows:
                scope=frozenset(json.loads(row["scopes"]))
                required="booking:read" if row["resource_type"]=="booking" else "payment_status:read"
                if required in scope:
                    value={key:row[key] for key in ("resource_type","resource_reference","relation")}
                    value["allowed_scopes"]=sorted(scope)
                    items.append((row["resource_type"]+":"+row["resource_reference"],value))
            return self._page(c,caller,"relations","",limit,cursor,items)
