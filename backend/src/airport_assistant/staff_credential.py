"""Interactive credential provisioning for existing staff; no password arguments."""

import argparse
from getpass import getpass, GetPassWarning
from pathlib import Path
import sys
import warnings
from uuid import UUID

from airport_assistant.database_config import MigrationDatabaseSettings, load_database_settings
from airport_assistant.infrastructure.database.migrations import create_migration_engine
from airport_assistant.infrastructure.database.staff_provisioning import set_staff_credential, disable_staff_credential
from airport_assistant.infrastructure.security.passwords import hash_password


def main():
    parser = argparse.ArgumentParser(description="Set/rotate or disable credentials for existing provisioned staff.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--principal", type=UUID, required=True)
    parser.add_argument("--contact", type=UUID)
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if not args.disable and args.contact is None:
        parser.error("--contact is required to set a credential.")
    try:
        encoded = None
        if not args.disable:
            if not sys.stdin.isatty():
                raise ValueError("Interactive hidden password input is required.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", GetPassWarning)
                password = getpass("New staff password (15-128 characters): ")
                if password != getpass("Confirm staff password: "):
                    raise ValueError("Password confirmation failed.")
            encoded = hash_password(password)
            del password
        settings = load_database_settings(MigrationDatabaseSettings, args.env_file)
        engine = create_migration_engine(settings.url.get_secret_value())
        try:
            with engine.begin() as connection:
                if args.disable:
                    disable_staff_credential(connection, args.tenant, args.principal)
                else:
                    set_staff_credential(connection, args.tenant, args.principal, args.contact, encoded)
        finally:
            engine.dispose()
    except Exception:
        print("Staff credential operation failed; input and database details redacted.")
        raise SystemExit(1) from None
    print("PASS: staff credential changed; existing credential-version sessions invalidated. No membership granted.")


if __name__ == "__main__":
    main()
