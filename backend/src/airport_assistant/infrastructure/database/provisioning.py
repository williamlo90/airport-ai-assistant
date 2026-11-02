"""Controlled role provisioning; secrets stay within the invoking script."""

import re
from urllib.parse import quote, urlsplit

from psycopg import sql
from sqlalchemy import text

from airport_assistant.infrastructure.database.migrations import MigrationError

RUNTIME_ROLE = "airport_assistant_runtime"
POLICY_ROLE = "airport_assistant_policy_owner"
AUTH_ROLE = "airport_assistant_auth_owner"


def prepare_auth_owner(connection,auth_role=AUTH_ROLE):
    role_name(auth_role)
    row=connection.execute(text("SELECT oid,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,rolreplication,rolinherit,rolcanlogin FROM pg_roles WHERE rolname=:name"),dict(name=auth_role)).first()
    if row:
        if any(row[1:]) or connection.execute(text("SELECT count(*) FROM pg_auth_members WHERE member=:oid"),dict(oid=row[0])).scalar_one():
            raise MigrationError("Authentication owner must be an isolated NOLOGIN role.")
    else:
        connection.exec_driver_sql(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION").format(sql.Identifier(auth_role)).as_string())
    current=connection.execute(text("SELECT current_user")).scalar_one()
    if current==auth_role:raise MigrationError("Separate migration credential is required.")
    connection.exec_driver_sql(sql.SQL("GRANT {} TO {}").format(sql.Identifier(auth_role),sql.Identifier(current)).as_string())


def role_name(value: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise MigrationError("Invalid dedicated database role.")


def prepare_roles(connection, runtime_role=RUNTIME_ROLE, policy_role=POLICY_ROLE):
    for name in (runtime_role, policy_role):
        role_name(name)
        row = connection.execute(text("""SELECT oid,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,
            rolreplication,rolinherit,rolcanlogin FROM pg_roles WHERE rolname=:name"""), {"name": name}).first()
        if row:
            if any(row[1:7]) or (name == policy_role and row[7]):
                raise MigrationError("An existing dedicated role has unsafe privileges.")
            if connection.execute(text("SELECT count(*) FROM pg_auth_members WHERE member=:id"), {"id": row[0]}).scalar_one():
                raise MigrationError("Dedicated roles must not inherit or set other roles.")
        else:
            connection.exec_driver_sql(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION").format(sql.Identifier(name)).as_string())
    current = connection.execute(text("SELECT current_user,current_database()")).one()
    if current[0] in {runtime_role, policy_role}:
        raise MigrationError("Provisioning requires a separate migration role.")
    connection.exec_driver_sql(sql.SQL("GRANT {} TO {}").format(sql.Identifier(policy_role),sql.Identifier(current[0])).as_string())
    connection.exec_driver_sql(sql.SQL("REVOKE CREATE,TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(current[1])).as_string())
    connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def enable_login(connection, role: str, password: str):
    role_name(role)
    # Keep the password out of SQLAlchemy's statement logger, even at DEBUG.
    connection.connection.driver_connection.execute(
        sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(password)))


def runtime_url(original_url: str, role: str, password: str) -> str:
    parsed = urlsplit(original_url)
    host = parsed.netloc.rpartition("@")[2]
    return parsed._replace(netloc=quote(role,safe="")+":"+quote(password,safe="")+"@"+host).geturl()
