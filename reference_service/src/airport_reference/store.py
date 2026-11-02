"""Separate SQLite dataset. Each request reads one immutable transaction snapshot."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import re
from uuid import UUID
from airport_reference.security import SourceError

APPLICATION_ID=1095324240
SCHEMA_VERSION=1


class Store:
    def __init__(self,path:Path):self.path=path.resolve()

    @contextmanager
    def read(self):
        connection=None
        try:
            connection=sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True,timeout=1)
            connection.row_factory=sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if connection.execute("PRAGMA application_id").fetchone()[0]!=APPLICATION_ID or connection.execute("PRAGMA user_version").fetchone()[0]!=SCHEMA_VERSION:
                raise SourceError(503,"SOURCE_UNAVAILABLE")
            metadata=dict(connection.execute("SELECT key,value FROM metadata"))
            if set(metadata)!={"generation","dataset_hash"} or not re.fullmatch(r"[a-f0-9]{64}",metadata["dataset_hash"]):
                raise SourceError(503,"SOURCE_UNAVAILABLE")
            UUID(metadata["generation"])
            yield connection
        except (sqlite3.Error,ValueError,TypeError):
            raise SourceError(503,"SOURCE_UNAVAILABLE") from None
        finally:
            if connection is not None:connection.close()


def active(c,caller):
    return c.execute("SELECT 1 FROM subjects s JOIN tenants t ON t.id=s.tenant_id WHERE s.tenant_id=? AND s.reference=? AND s.active=1 AND t.active=1",(caller.tenant_id,caller.subject)).fetchone() is not None


def grants(c,caller,kind,reference):
    row=c.execute("SELECT scopes FROM relations WHERE tenant_id=? AND subject_reference=? AND resource_type=? AND resource_reference=?",(caller.tenant_id,caller.subject,kind,reference)).fetchone()
    return frozenset(json.loads(row[0])) if row else frozenset()


def one(c,caller,table,reference):
    if table not in {"bookings","transactions","policies"}:raise ValueError("Unsupported table.")
    return c.execute(f"SELECT * FROM {table} WHERE tenant_id=? AND reference=?",(caller.tenant_id,reference)).fetchone()
