"""Trusted context construction and transaction-local parameter boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.infrastructure.database.provisioning import runtime_url, role_name, enable_login


@pytest.mark.parametrize("values", [
    dict(tenant_id="not-a-uuid"),
    dict(tenant_id=uuid4(),actor_kind="admin"),
    dict(tenant_id=uuid4(),actor_kind="customer"),
    dict(tenant_id=uuid4(),principal_id=uuid4()),
    dict(tenant_id=uuid4(),principal_id="not-a-uuid",actor_kind="staff"),
])
def test_invalid_context_rejected(values):
    with pytest.raises(ValueError):
        DatabaseContext(**values)


def test_role_uri_replacement_encodes_password_and_preserves_target():
    result=runtime_url("postgresql://old:old@ep-pooler.region.aws.neon.tech/db?sslmode=require","runtime","p@ss:/word")
    assert result=="postgresql://runtime:p%40ss%3A%2Fword@ep-pooler.region.aws.neon.tech/db?sslmode=require"


def test_login_password_bypasses_sqlalchemy_statement_logger():
    driver=Mock()
    connection=SimpleNamespace(connection=SimpleNamespace(driver_connection=driver),exec_driver_sql=Mock())
    enable_login(connection,"test_runtime","secret-sentinel")
    driver.execute.assert_called_once()
    connection.exec_driver_sql.assert_not_called()


@pytest.mark.parametrize("value",["x;drop role y",'x"y',"postgres",""])
def test_role_identifiers_checked(value):
    if value=="postgres":
        # Valid syntax does not grant permission; provisioning uses fixed names.
        role_name(value)
    else:
        with pytest.raises(RuntimeError):role_name(value)
