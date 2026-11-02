"""Safe CLI for config validation and explicitly requested live DB checks."""

import argparse
import asyncio
from pathlib import Path
import sys

from airport_assistant.composition import build_resources, database_loop_factory
from airport_assistant.config import ConfigurationError
from airport_assistant.database_config import (
    MigrationDatabaseSettings, RuntimeDatabaseSettings, load_database_settings, validate_same_target,
)
from airport_assistant.infrastructure.database.verification import verify_connections


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate DB settings; --live uses a dedicated test database.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--env-file", type=Path, default=Path(".env"))
    source.add_argument("--no-env-file", action="store_true")
    parser.add_argument("--live", action="store_true", help="Create/drop a probe table and test commit/rollback.")
    args = parser.parse_args()
    try:
        env_file = None if args.no_env_file else args.env_file
        runtime = load_database_settings(RuntimeDatabaseSettings, env_file)
        migration = load_database_settings(MigrationDatabaseSettings, env_file)
        validate_same_target(runtime, migration)
        if not args.live:
            print("Database configuration valid. No network checks performed.")
            return
        resources = build_resources(runtime)
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:
            runner.run(verify_connections(resources.database, migration.url.get_secret_value(),
                                          connect_timeout=migration.connect_timeout))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("Database verification failed. Check access/network and probe cleanup in the test database.", file=sys.stderr)
        raise SystemExit(1) from None
    print("PASS: pooled runtime, direct connection, TLS, commit, rollback and pool reuse; probe table removed.")


if __name__ == "__main__":
    main()
