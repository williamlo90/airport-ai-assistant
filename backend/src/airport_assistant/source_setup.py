"""Local reference connector setup; keys are copied only inside this script."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID
from dotenv import dotenv_values,set_key
from airport_assistant.config import load_settings
from airport_assistant.source_config import SourceSettings
from airport_assistant.database_config import RuntimeDatabaseSettings,load_database_settings
from airport_assistant.composition import database_loop_factory
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--reference-env',type=Path,default=Path('reference_service/.env'))
    parser.add_argument('--demo-host',default='localhost')
    args=parser.parse_args()
    try:
        if any(k.startswith('SOURCE_') or k=='APP_SOURCE_ENABLED' for k in os.environ):raise ValueError('Local setup requires no source process overrides.')
        app=load_settings(args.env_file)
        if app.env=='production' or not app.auth_enabled or not app.database_enabled:raise ValueError('Local authenticated development app required.')
        original=dotenv_values(args.env_file);ref=dotenv_values(args.reference_env)
        if ref.get('REF_ENV','development') not in {'development','test'}:raise ValueError('Local development reference required.')
        if ref.get('REF_HOST','127.0.0.1')!='127.0.0.1':raise ValueError('Local reference host required.')
        port=int(ref.get('REF_PORT','8010'))
        if not 1<=port<=65535:raise ValueError('Invalid local source port.')
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value:raise ValueError('Duplicate tenant mapping.')
                value[key]=item
            return value
        mapping=json.loads(original.get('SOURCE_TENANT_MAP') or '{}',object_pairs_hook=unique)
        runtime=load_database_settings(RuntimeDatabaseSettings,args.env_file)
        async def lookup():
            db=RuntimeDatabase(runtime.url.get_secret_value())
            try:
                await db.open()
                return await PostgresAuthRepository(db).tenant_for_host(args.demo_host)
            finally:await db.close()
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:tenant=runner.run(lookup())
        if tenant is None:raise ValueError('Registered demo host required.')
        target='11111111-1111-4111-8111-111111111111'
        if str(tenant) in mapping and mapping[str(tenant)]!=target:raise ValueError('Existing mapping cannot be rebound by local setup.')
        mapping[str(tenant)]=target
        values=dict(SOURCE_API_BASE_URL=f'http://127.0.0.1:{port}',SOURCE_API_TOKEN=ref.get('REF_SERVICE_TOKEN'),SOURCE_CONTEXT_SECRET=ref.get('REF_CONTEXT_SECRET'),SOURCE_TENANT_MAP=json.dumps(mapping,separators=(',',':')),SOURCE_ALLOW_LOOPBACK_HTTP='true')
        SourceSettings(SOURCE_API_BASE_URL=values['SOURCE_API_BASE_URL'],SOURCE_API_TOKEN=values['SOURCE_API_TOKEN'],
                       context_secret=values['SOURCE_CONTEXT_SECRET'],tenant_map=values['SOURCE_TENANT_MAP'],allow_loopback_http=True,_env_file=None)
        for name in ('SOURCE_API_BASE_URL','SOURCE_API_TOKEN','SOURCE_CONTEXT_SECRET'):
            if original.get(name) and original[name]!=values[name]:raise ValueError('Existing source origin or credential differs; use a reviewed rotation.')
        if dotenv_values(args.env_file)!=original:raise ValueError('Local configuration changed during setup.')
        for name,value in {**values,'APP_SOURCE_ENABLED':'true'}.items():
            if not set_key(str(args.env_file),name,value)[0]:raise ValueError('Source config update failed.')
    except Exception:
        print('Source setup failed; configuration and credential details redacted.')
        raise SystemExit(1) from None
    print('PASS: local source connector configured internally; registered tenant mapped, no customer identity or source relationship created.')


if __name__=='__main__':main()
