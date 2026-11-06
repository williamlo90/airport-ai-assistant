"""Local setup generates keys internally; it never prints credential values."""

import argparse
import os
from pathlib import Path
import secrets
from airport_reference.config import load_settings
from airport_reference.seed import seed_database
from airport_reference.store import Store


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',type=Path,default=Path('reference_service/.env'))
    parser.add_argument('--reset-fixtures',action='store_true',help='Stop API first. Replace only a recognized reference database.')
    args=parser.parse_args()
    try:
        if any(k.startswith('REF_') for k in os.environ):raise ValueError('Local setup requires no REF_* process overrides.')
        if not args.env_file.exists():
            args.env_file.parent.mkdir(parents=True,exist_ok=True)
            values=dict(REF_ENV='development',REF_HOST='127.0.0.1',REF_PORT='8010',REF_DATABASE_PATH=Path('reference_service/local/reference.sqlite3').resolve().as_posix(),REF_SERVICE_TOKEN=secrets.token_urlsafe(48),REF_CONTEXT_SECRET=secrets.token_urlsafe(48))
            content='# Local reference source settings. Do not commit.\n'
            for name,value in values.items():
                escaped=str(value).replace("'", "\\'")
                content+=f"{name}='{escaped}'\n"
            # Prepare the full content before exclusive creation; preserve existing keys.
            descriptor=os.open(args.env_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(descriptor,'w',encoding='utf-8') as out:out.write(content)
        settings=load_settings(args.env_file)
        if args.reset_fixtures or not settings.database_path.exists():seed_database(settings.database_path,replace=args.reset_fixtures)
        with Store(settings.database_path).read() as c:
            if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Invalid reference dataset.')
    except Exception:
        print('Reference setup failed; configuration and database details redacted.')
        raise SystemExit(1) from None
    print('PASS: fictional reference dataset ready; independent local keys configured internally. No app database changed.')


if __name__=='__main__':main()
