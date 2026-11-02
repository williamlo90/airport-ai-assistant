"""Provision dedicated runtime access and update only its local URL internally."""

import argparse
import asyncio
from pathlib import Path
import secrets
import os
from urllib.parse import unquote, urlsplit

from dotenv import set_key, dotenv_values

from airport_assistant.composition import database_loop_factory
from airport_assistant.database_config import MigrationDatabaseSettings, RuntimeDatabaseSettings, load_database_settings, validate_same_target
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.infrastructure.database.migrations import create_migration_engine, migrate
from airport_assistant.infrastructure.database.provisioning import RUNTIME_ROLE, POLICY_ROLE, prepare_roles, enable_login, runtime_url


def main():
    parser=argparse.ArgumentParser(description="Provision restricted runtime login and update DATABASE_URL locally.")
    parser.add_argument("--env-file",type=Path,default=Path(".env"))
    args=parser.parse_args()
    phase="configuration"
    try:
        if os.environ.get("DATABASE_URL"):
            raise ValueError("Local-file provisioning requires no process URL override.")
        runtime=load_database_settings(RuntimeDatabaseSettings,args.env_file)
        migration=load_database_settings(MigrationDatabaseSettings,args.env_file)
        validate_same_target(runtime,migration)
        old=runtime.url.get_secret_value()
        parsed=urlsplit(old)
        password=unquote(parsed.password) if unquote(parsed.username)==RUNTIME_ROLE else secrets.token_urlsafe(36)
        direct=migration.url.get_secret_value()
        engine=create_migration_engine(direct,connect_timeout=migration.connect_timeout)
        try:
            phase="role_setup"
            with engine.begin() as connection:
                prepare_roles(connection)
            phase="migration"
            revision=migrate(direct,runtime_role=RUNTIME_ROLE,policy_role=POLICY_ROLE)
            phase="login_setup"
            with engine.begin() as connection:
                enable_login(connection,RUNTIME_ROLE,password)
        finally:
            engine.dispose()
        new=runtime_url(old,RUNTIME_ROLE,password)
        phase="runtime_verification"
        async def verify():
            db=RuntimeDatabase(new)
            try:
                await db.open()
            finally:
                await db.close()
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:
            runner.run(verify())
        phase="local_configuration_update"
        if dotenv_values(args.env_file).get("DATABASE_URL") != old:
            raise ValueError("Runtime configuration changed during provisioning.")
        if not set_key(str(args.env_file),"DATABASE_URL",new,quote_mode="always")[0]:
            raise ValueError("Local configuration update failed.")
    except Exception:
        print(f"Provisioning failed at {phase}; details redacted. DATABASE_URL was not updated.")
        raise SystemExit(1) from None
    print(f"PASS: restricted runtime login verified; local DATABASE_URL updated internally; revision={revision}.")


if __name__=="__main__":
    main()
