"""Read-only local host verification; credentials are loaded internally, never printed."""
import argparse
import asyncio
from pathlib import Path

from airport_assistant.composition import database_loop_factory
from airport_assistant.database_config import RuntimeDatabaseSettings, load_database_settings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.modules.identity.context import DatabaseContext


async def verify(env_file: Path, expected_slug: str) -> None:
    settings = load_database_settings(RuntimeDatabaseSettings, env_file)
    database = RuntimeDatabase(settings.url.get_secret_value())
    try:
        await database.open()
        async with database.transaction() as connection:
            row = await (await connection.execute("SELECT app.lookup_tenant_host('localhost')")).fetchone()
        if not row or row[0] is None:
            raise ValueError('Host not registered.')
        async with database.transaction(DatabaseContext(row[0])) as connection:
            tenant = await (await connection.execute('SELECT slug FROM app.tenants WHERE id=%s', (row[0],))).fetchone()
        if not tenant or tenant[0] != expected_slug:
            raise ValueError('Unexpected tenant.')
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--expected-tenant-slug', required=True)
    args = parser.parse_args()
    try:
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:
            runner.run(verify(args.env_file, args.expected_tenant_slug))
    except Exception:
        print('FAIL: localhost registration or runtime connection could not be verified; details redacted. No registry changes made.')
        raise SystemExit(1) from None
    print('PASS: localhost is active and maps to the expected tenant; restricted runtime connection verified. No registry changes made.')


if __name__ == '__main__':
    main()
