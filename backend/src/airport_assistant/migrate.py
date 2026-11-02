"""Credential-safe migration CLI; use direct connection, never runtime pooling."""

import argparse
from pathlib import Path
import sys

from airport_assistant.config import ConfigurationError
from airport_assistant.database_config import MigrationDatabaseSettings, load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate, new_revision, MigrationError


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply or inspect application migrations using the direct credential.")
    parser.add_argument("action", choices=("upgrade", "current", "new"))
    parser.add_argument("--message", help="Required for new; no database credentials are loaded.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--env-file", type=Path, default=Path(".env"))
    source.add_argument("--no-env-file", action="store_true")
    args = parser.parse_args()
    if args.action == "new" and not args.message:
        parser.error("new requires --message")
    try:
        if args.action == "new":
            path = new_revision(args.message)
            print(f"PASS: created revision {path.name}; implement it before upgrade.")
            return
        settings = load_database_settings(MigrationDatabaseSettings, None if args.no_env_file else args.env_file)
        revision = migrate(settings.url.get_secret_value(), connect_timeout=settings.connect_timeout, action=args.action)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except MigrationError as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Migration failed; details redacted. Check direct database access and migration history.", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: migration {args.action}; revision={revision or 'none'}.")


if __name__ == "__main__":
    main()
