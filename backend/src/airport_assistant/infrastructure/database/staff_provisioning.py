"""Credential changes require a separate migration administrator, never runtime."""

from uuid import UUID
from sqlalchemy import text
from airport_assistant.infrastructure.database.migrations import validate_schema


def set_staff_credential(connection, tenant: UUID, principal: UUID, contact: UUID, encoded: str, *, schema="app"):
    validate_schema(schema)
    q = connection.dialect.identifier_preparer.quote_identifier(schema)
    eligible = connection.execute(text(f"""SELECT ct.id FROM {q}.contacts ct
        JOIN {q}.principals p ON(p.tenant_id,p.id)=(ct.tenant_id,ct.principal_id)
        JOIN {q}.tenants t ON t.id=p.tenant_id
        WHERE ct.tenant_id=:tenant AND ct.id=:contact AND p.id=:principal
          AND ct.verified_at IS NOT NULL AND p.active AND t.active
          AND EXISTS(SELECT 1 FROM {q}.staff_memberships m
            JOIN {q}.organizations o ON(o.tenant_id,o.id)=(m.tenant_id,m.organization_id)
            JOIN {q}.membership_roles r ON(r.tenant_id,r.membership_id)=(m.tenant_id,m.id)
            WHERE m.tenant_id=p.tenant_id AND m.principal_id=p.id AND m.active AND o.active)
        FOR UPDATE OF ct,p"""),dict(tenant=tenant,principal=principal,contact=contact)).scalar_one_or_none()
    if eligible is None:
        raise ValueError("Existing verified staff identity and active membership required.")
    result = connection.execute(text(f"""INSERT INTO {q}.staff_credentials(tenant_id,principal_id,contact_id,password_hash)
        VALUES(:tenant,:principal,:contact,:encoded) ON CONFLICT(tenant_id,principal_id)
        DO UPDATE SET password_hash=EXCLUDED.password_hash,active=true
        WHERE {q}.staff_credentials.contact_id=EXCLUDED.contact_id"""),
        dict(tenant=tenant,principal=principal,contact=contact,encoded=encoded))
    if result.rowcount != 1:
        raise ValueError("Credential contact cannot be retargeted during rotation.")


def disable_staff_credential(connection, tenant: UUID, principal: UUID, *, schema="app"):
    validate_schema(schema)
    q = connection.dialect.identifier_preparer.quote_identifier(schema)
    result = connection.execute(text(f"UPDATE {q}.staff_credentials SET active=false WHERE tenant_id=:tenant AND principal_id=:principal"),
                                dict(tenant=tenant,principal=principal))
    if result.rowcount != 1:
        raise ValueError("Existing staff credential required.")
