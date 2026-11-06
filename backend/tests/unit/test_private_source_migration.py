"""Capture forward SQL without connecting, and enforce staged rollout boundaries."""
from types import SimpleNamespace

from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import dialect

from airport_assistant.infrastructure.database.migrations import migration_config


def statements(monkeypatch):
    revision = ScriptDirectory.from_config(migration_config()).get_revision('0029_private_source_schema').module
    output = []
    attrs = dict(schema='private_schema_test', runtime_role='test_runtime', policy_role='test_policy',
                 auth_role='test_auth', worker_role='test_worker', job_owner_role='test_jobs')
    monkeypatch.setattr(revision, 'context', SimpleNamespace(config=SimpleNamespace(attributes=attrs)))
    monkeypatch.setattr(revision, 'op', SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=dialect()), execute=output.append))
    revision.upgrade()
    return revision, output


def test_forward_revision_preserves_old_constraints_policies_and_no_activation(monkeypatch):
    revision, sql = statements(monkeypatch)
    assert revision.down_revision == '0028_public_answers'
    all_sql = '\n'.join(sql)
    assert 'DROP CONSTRAINT' not in all_sql and 'DROP POLICY' not in all_sql
    assert 'DISABLE ROW LEVEL SECURITY' not in all_sql and 'NO FORCE' not in all_sql
    assert 'CREATE ROLE' not in all_sql
    assert "CHECK(resource_security_labels='[]'::jsonb)" in all_sql
    assert "CHECK(source_observations='[]'::jsonb)" in all_sql
    assert 'CREATE FUNCTION "private_schema_test".mail_private_' not in all_sql
    assert 'source_evidence_valid(source_observations,resource_security_labels)' in all_sql


def test_each_source_table_has_canonical_and_monotone_contract(monkeypatch):
    revision, sql = statements(monkeypatch)
    for table in (*revision.OLD_LABEL_TABLES, *revision.NEW_LABEL_TABLES):
        assert any(f'ALTER TABLE "private_schema_test".{table} ADD CONSTRAINT private_labels_canonical' in item for item in sql)
        assert any(f'ON "private_schema_test".{table}\n' in item and 'guard_source_labels_monotone' in item for item in sql)


def test_pure_helper_privileges_include_existing_mail_reply_owners(monkeypatch):
    _, sql = statements(monkeypatch)
    grants = [item for item in sql if 'GRANT EXECUTE' in item and 'source_labels_valid' in item]
    assert len(grants) == 1
    assert 'mail_owner_' in grants[0] and 'reply_owner_' in grants[0]
    for item in sql:
        if item.startswith('CREATE FUNCTION'):
            assert 'SET search_path=pg_catalog' in item
            assert 'SECURITY DEFINER' not in item


def test_status_reports_missing_actual_capabilities_instead_of_adding_stubs(monkeypatch):
    _, sql = statements(monkeypatch)
    status = next(item for item in sql if item.startswith('CREATE FUNCTION') and 'private_source_schema_status' in item)
    assert "'graph_contract',to_regprocedure" in status
    assert "'inbound_contract',to_regprocedure" in status
    assert "'outbound_contract',to_regprocedure" in status
    assert "'history_contract',to_regprocedure" in status
