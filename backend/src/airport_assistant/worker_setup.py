"""Provision a scheduler-only login without exposing local credentials."""
import argparse
import asyncio
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit,unquote
from dotenv import dotenv_values,set_key
from airport_assistant.database_config import RuntimeDatabaseSettings,MigrationDatabaseSettings,load_database_settings,validate_same_target
from airport_assistant.worker_config import WorkerDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate,create_migration_engine
from airport_assistant.infrastructure.database.provisioning import enable_login,runtime_url
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.composition import database_loop_factory


def main():
    parser=argparse.ArgumentParser(description='Provision WORKER_DATABASE_URL locally without printing credentials.')
    parser.add_argument('--env-file',type=Path,default=Path('.env'));args=parser.parse_args();phase='configuration'
    try:
        if any(os.environ.get(key) is not None for key in ('WORKER_DATABASE_URL','DATABASE_URL','DATABASE_DIRECT_URL')):raise ValueError('Process override not allowed.')
        original=dotenv_values(args.env_file)
        api=load_database_settings(RuntimeDatabaseSettings,args.env_file);direct=load_database_settings(MigrationDatabaseSettings,args.env_file)
        validate_same_target(api,direct)
        before=original.get('WORKER_DATABASE_URL');role='airport_assistant_worker'
        if before:
            old=WorkerDatabaseSettings(WORKER_DATABASE_URL=before);validate_same_target(old,direct)
            if unquote(urlsplit(before).username)!=role:raise ValueError('Wrong worker login.')
            password=unquote(urlsplit(before).password)
        else:password=secrets.token_urlsafe(36)
        phase='migration';revision=migrate(direct.url.get_secret_value())
        phase='login_setup';engine=create_migration_engine(direct.url.get_secret_value())
        try:
            with engine.begin() as c:enable_login(c,role,password)
        finally:engine.dispose()
        new=runtime_url(api.url.get_secret_value(),role,password);phase='privilege_verification'
        async def verify():
            database=WorkerDatabase(new)
            try:await database.open()
            finally:await database.close()
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(verify())
        phase='local_configuration'
        current=dotenv_values(args.env_file)
        if any(current.get(key)!=original.get(key) for key in ('WORKER_DATABASE_URL','DATABASE_URL','DATABASE_DIRECT_URL')):raise ValueError('Database configuration changed.')
        if not set_key(str(args.env_file),'WORKER_DATABASE_URL',new,quote_mode='always')[0]:raise ValueError('Local write failed.')
    except Exception:
        print('Worker setup failed at '+phase+'; details redacted.');raise SystemExit(1) from None
    print('PASS: restricted worker login verified; WORKER_DATABASE_URL saved locally; revision='+revision+'.')


if __name__=='__main__':main()
