"""Transactional fixture creation, published only after validation and close."""

from contextlib import closing,contextmanager
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4
from airport_reference.models import Dataset
from airport_reference.store import APPLICATION_ID, SCHEMA_VERSION

SCHEMA="""
CREATE TABLE tenants(id TEXT PRIMARY KEY,slug TEXT NOT NULL UNIQUE,active INTEGER NOT NULL CHECK(active IN(0,1)));
CREATE TABLE subjects(tenant_id TEXT NOT NULL REFERENCES tenants(id),reference TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN(0,1)),PRIMARY KEY(tenant_id,reference));
CREATE TABLE policies(tenant_id TEXT NOT NULL REFERENCES tenants(id),reference TEXT NOT NULL,service_type TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),effective_from TEXT NOT NULL,source_updated_at TEXT NOT NULL,instructions TEXT NOT NULL,PRIMARY KEY(tenant_id,reference),UNIQUE(tenant_id,reference,service_type));
CREATE TABLE resources(tenant_id TEXT NOT NULL REFERENCES tenants(id),resource_type TEXT NOT NULL CHECK(resource_type IN('booking','transaction')),reference TEXT NOT NULL,PRIMARY KEY(tenant_id,resource_type,reference));
CREATE TABLE bookings(tenant_id TEXT NOT NULL,reference TEXT NOT NULL,resource_type TEXT NOT NULL DEFAULT 'booking' CHECK(resource_type='booking'),service_type TEXT NOT NULL,status TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT NOT NULL,service_timezone TEXT NOT NULL,location TEXT NOT NULL,policy_reference TEXT NOT NULL,source_updated_at TEXT NOT NULL,PRIMARY KEY(tenant_id,reference),FOREIGN KEY(tenant_id,resource_type,reference) REFERENCES resources(tenant_id,resource_type,reference),FOREIGN KEY(tenant_id,policy_reference,service_type) REFERENCES policies(tenant_id,reference,service_type));
CREATE TABLE transactions(tenant_id TEXT NOT NULL,reference TEXT NOT NULL,resource_type TEXT NOT NULL DEFAULT 'transaction' CHECK(resource_type='transaction'),booking_reference TEXT NOT NULL,currency TEXT NOT NULL,amount_minor INTEGER NOT NULL CHECK(amount_minor>=0),currency_exponent INTEGER NOT NULL CHECK(currency_exponent BETWEEN 0 AND 3),status TEXT NOT NULL,refund_status TEXT NOT NULL,source_updated_at TEXT NOT NULL,PRIMARY KEY(tenant_id,reference),FOREIGN KEY(tenant_id,resource_type,reference) REFERENCES resources(tenant_id,resource_type,reference),FOREIGN KEY(tenant_id,booking_reference) REFERENCES bookings(tenant_id,reference));
CREATE TABLE relations(tenant_id TEXT NOT NULL,subject_reference TEXT NOT NULL,resource_type TEXT NOT NULL,resource_reference TEXT NOT NULL,relation TEXT NOT NULL,scopes TEXT NOT NULL,PRIMARY KEY(tenant_id,subject_reference,resource_type,resource_reference),FOREIGN KEY(tenant_id,subject_reference) REFERENCES subjects(tenant_id,reference),FOREIGN KEY(tenant_id,resource_type,resource_reference) REFERENCES resources(tenant_id,resource_type,reference));
CREATE INDEX transaction_booking ON transactions(tenant_id,booking_reference,reference);
CREATE INDEX booking_policy ON bookings(tenant_id,policy_reference);
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def fixture_path():
    checkout=Path(__file__).resolve().parents[2]/"fixtures/dataset.json"
    return checkout if checkout.is_file() else files("airport_reference").joinpath("fixtures/dataset.json")


@contextmanager
def publication_lock(path):
    lock=path.with_name(path.name+".seed.lock")
    descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    try:yield
    finally:lock.unlink()


def publish_new(temporary,path):
    # Windows rename fails if the target exists; POSIX rename would overwrite.
    if os.name=="nt":os.rename(temporary,path)
    else:os.link(temporary,path)


def seed_database(path:Path,*,replace=False,fixture=None):
    path=path.resolve()
    if path.suffix!=".sqlite3":raise ValueError("Dedicated .sqlite3 path required.")
    path.parent.mkdir(parents=True,exist_ok=True)
    with publication_lock(path):
        _seed_locked(path,replace=replace,fixture=fixture)


def _seed_locked(path,*,replace,fixture):
    previous=None
    if path.exists():
        if not replace:raise ValueError("Existing database; stop API before explicit reset.")
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as old:
            if old.execute("PRAGMA application_id").fetchone()[0]!=APPLICATION_ID:raise ValueError("Refuse replacing an unrelated file.")
        stat=path.stat();previous=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)
    data=Dataset.model_validate_json((fixture or fixture_path()).read_bytes())
    path.parent.mkdir(parents=True,exist_ok=True)
    handle=tempfile.NamedTemporaryFile(prefix="reference-seed-",suffix=".sqlite3",dir=path.parent,delete=False)
    temporary=Path(handle.name);handle.close()
    connection=None
    try:
        connection=sqlite3.connect(temporary)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        with connection:
            for table in ("tenants","subjects","policies","bookings","transactions","relations"):
                for record in getattr(data,table):
                    row=record.model_dump(mode="json")
                    if table in {"bookings","transactions"}:
                        connection.execute("INSERT INTO resources VALUES(?,?,?)",(row["tenant_id"],"booking" if table=="bookings" else "transaction",row["reference"]))
                    for name in ("instructions","scopes"):
                        if name in row:row[name]=json.dumps(row[name],sort_keys=True,separators=(",",":"))
                    fields=",".join(row)
                    connection.execute(f"INSERT INTO {table}({fields}) VALUES({','.join('?' for _ in row)})",tuple(row.values()))
            connection.executemany("INSERT INTO metadata VALUES(?,?)",[("generation",str(uuid4())),("dataset_hash",hashlib.sha256(data.model_dump_json().encode()).hexdigest())])
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if connection.execute("PRAGMA foreign_key_check").fetchall() or connection.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Invalid fixture relations.")
        connection.close();connection=None
        if previous is None:
            publish_new(temporary,path)
        else:
            # Cooperating seeders share the lock. Refuse a target changed by another writer.
            stat=path.stat()
            if previous!=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns):raise ValueError("Reference target changed during reset.")
            os.replace(temporary,path)
    finally:
        if connection is not None:connection.close()
        if temporary.exists():temporary.unlink()
