"""Local setup: generate auth key internally and prepare the narrow DB owner."""

import argparse
import asyncio
import os
from pathlib import Path
import re
import secrets

from dotenv import dotenv_values,set_key
from sqlalchemy import text

from airport_assistant.auth_config import AuthSettings
from airport_assistant.composition import database_loop_factory
from airport_assistant.database_config import RuntimeDatabaseSettings,MigrationDatabaseSettings,load_database_settings,validate_same_target
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.infrastructure.database.migrations import create_migration_engine,migrate
from airport_assistant.infrastructure.database.provisioning import prepare_auth_owner


def main():
    parser=argparse.ArgumentParser(description="Prepare customer auth without printing secret values.")
    parser.add_argument("--env-file",type=Path,default=Path(".env"))
    parser.add_argument("--demo-host",help="Optionally register a fictitious demo tenant for this canonical local host.")
    args=parser.parse_args();phase="configuration"
    try:
        if any(os.environ.get(name) is not None for name in ("SESSION_SECRET","APP_AUTH_ENABLED","APP_DATABASE_ENABLED")):
            raise ValueError("Local-file setup requires no process overrides for auth activation.")
        runtime=load_database_settings(RuntimeDatabaseSettings,args.env_file)
        direct=load_database_settings(MigrationDatabaseSettings,args.env_file)
        validate_same_target(runtime,direct)
        before=dotenv_values(args.env_file)
        key=before.get("SESSION_SECRET") or secrets.token_urlsafe(48)
        AuthSettings(SESSION_SECRET=key,_env_file=args.env_file)
        async def validate_runtime():
            db=RuntimeDatabase(runtime.url.get_secret_value())
            try:await db.open()
            finally:await db.close()
        phase="runtime_role"
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(validate_runtime())
        phase="authentication_owner"
        engine=create_migration_engine(direct.url.get_secret_value())
        try:
            with engine.begin() as c:prepare_auth_owner(c)
            phase="migration"
            revision=migrate(direct.url.get_secret_value())
            if args.demo_host:
                phase="demo_host"
                host=args.demo_host
                if len(host)>253 or not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*",host):
                    raise ValueError("Use a canonical hostname without port.")
                with engine.begin() as c:
                    existing=c.execute(text("SELECT tenant_id FROM app.tenant_hosts WHERE hostname=:host"),dict(host=host)).scalar_one_or_none()
                    if existing is None:
                        c.execute(text("INSERT INTO app.tenants(slug,display_name,timezone,supported_languages) VALUES('airport-demo','Airport Demo','Asia/Jakarta',ARRAY['id','en']) ON CONFLICT(slug) DO NOTHING"))
                        tenant=c.execute(text("SELECT id FROM app.tenants WHERE slug='airport-demo'")).scalar_one()
                        c.execute(text("INSERT INTO app.tenant_hosts(tenant_id,hostname) VALUES(:tenant,:host)"),dict(tenant=tenant,host=host))
        finally:engine.dispose()
        phase="local_configuration"
        if dotenv_values(args.env_file).get("SESSION_SECRET")!=before.get("SESSION_SECRET"):
            raise ValueError("Auth secret changed during setup.")
        for name,value in (("SESSION_SECRET",key),("APP_DATABASE_ENABLED","true"),("APP_AUTH_ENABLED","true")):
            if not set_key(str(args.env_file),name,value,quote_mode="always")[0]:raise ValueError("Configuration update failed.")
    except Exception:
        print(f"Authentication setup failed at {phase}; details redacted. Review the stage and retry safely.")
        raise SystemExit(1) from None
    print(f"PASS: auth owner and revision={revision}; auth key configured internally, API auth enabled. No email provider configured.")


if __name__=="__main__":main()
