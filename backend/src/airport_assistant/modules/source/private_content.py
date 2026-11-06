"""Fresh authorization of internal projections, never a reusable permission ticket.

Loaders must enforce current object/session ACL under RLS on EVERY invocation.
External grants are evaluated between transactions. No candidate can be emitted
until the second load confirms the same authorization/content basis.
"""
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
import time
from uuid import UUID

from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from .labels import SecurityLabels, merge_labels
from .models import SourceFailure


def _json(value):
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError('Unsupported internal projection basis.')


def inherited_records(tenant_id, *groups):
    """Monotone server-side union; a missing parent is not a public parent."""
    if not groups or any(type(records) is not list for records in groups):
        raise AuthError(404, 'RESOURCE_NOT_FOUND')
    try:
        bundles = [SecurityLabels.from_records(tenant_id, records) for records in groups if records]
        return merge_labels(*bundles).to_records() if bundles else []
    except (ValueError, TypeError):
        raise AuthError(404, 'RESOURCE_NOT_FOUND') from None


@dataclass(frozen=True)
class PrivateProjection:
    """Server-only candidate; repr never contains the content or source labels."""
    tenant_id: UUID
    resource_id: UUID
    basis_hash: str
    labels: SecurityLabels | None = field(repr=False)
    value: object = field(repr=False, compare=False)

    @classmethod
    def capture(cls, tenant_id, resource_id, *, value, basis, records):
        if not isinstance(tenant_id, UUID) or not isinstance(resource_id, UUID):
            raise ValueError('Trusted object identity required.')
        # Missing/malformed labels must never become an unlabeled projection.
        if type(records) is not list:
            raise AuthError(404, 'RESOURCE_NOT_FOUND')
        try:
            labels = SecurityLabels.from_records(tenant_id, records) if records else None
            encoded = json.dumps(dict(basis=basis, labels=labels.to_records() if labels else []),
                                 sort_keys=True, separators=(',', ':'), default=_json,
                                 ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, UnicodeError):
            raise AuthError(404, 'RESOURCE_NOT_FOUND') from None
        return cls(tenant_id, resource_id, hashlib.sha256(encoded).hexdigest(), labels, value)


class PrivateContentGate:
    def __init__(self, source_access=None, *, clock=time.monotonic, freshness_seconds=2):
        if not 0 < freshness_seconds <= 2:
            raise ValueError('Private source checks have a short operation boundary.')
        self.source_access = source_access
        self.clock = clock
        self.freshness_seconds = freshness_seconds

    @staticmethod
    def _candidate(identity, candidate, purpose):
        if (type(candidate) is not PrivateProjection
                or candidate.tenant_id != identity.session.tenant_id):
            raise AuthError(404, 'RESOURCE_NOT_FOUND')
        if purpose == 'staff':
            if identity.session.kind != 'staff' or not identity.memberships:
                raise AuthError(403, 'ACTION_FORBIDDEN')
        elif purpose == 'customer':
            if identity.session.kind not in {'anonymous', 'customer'}:
                raise AuthError(403, 'ACTION_FORBIDDEN')
            if candidate.labels is not None and identity.session.kind != 'customer':
                raise AuthError(404, 'RESOURCE_NOT_FOUND')
        else:
            raise ValueError('Explicit access purpose required.')

    async def read(self, identity, loader, *, purpose='customer', materialize=None):
        """Optional materializer reads bytes/cursors only AFTER source permission.

        Like loader, it must enforce fresh object/session ACL and return a bound
        PrivateProjection. It receives an operation-local checker to invoke on
        current metadata BEFORE parsing a cursor or loading bytes. The checker
        is called again after transaction exit; it is not a reusable permission.
        """
        candidate = await loader()
        self._candidate(identity, candidate, purpose)
        private = candidate.labels is not None
        if not private and materialize is None:
            # No external I/O occurred: the loader's current object ACL suffices.
            return candidate.value
        if private and purpose == 'customer':
            allowed = False if self.source_access is None else await self.source_access.allows(
                tenant_id=identity.session.tenant_id,
                principal_id=identity.session.principal_id, labels=candidate.labels)
            if allowed is not True:
                raise AuthError(404, 'RESOURCE_NOT_FOUND')
        checked_at = self.clock()
        def check(current):
            self._candidate(identity, current, purpose)
            if (current.resource_id != candidate.resource_id
                    or (private and current.basis_hash != candidate.basis_hash)
                    or (not private and current.labels is not None)):
                raise DraftError(409, 'SOURCE_CONTEXT_CHANGED')
            elapsed = self.clock() - checked_at
            if private and not 0 <= elapsed < self.freshness_seconds:
                raise SourceFailure(503)
        current = await (materialize(check) if materialize is not None else loader())
        check(current)
        return current.value

    @asynccontextmanager
    async def mutation(self, identity, transaction, loader, *, purpose='customer',
                       additional_labels=None, after_loader=None,before_commit=None):
        """One authorized write unit, with source I/O only BETWEEN DB units.

        The factory/loader enforce live session/object ACL. The gate owns both
        before/after checks inside the write transaction, so callers cannot omit
        them. A custom after_loader can include a newly created child aggregate;
        its resource identity stays bound to the authorized existing parent.
        """
        if additional_labels is not None:
            if type(additional_labels) is not SecurityLabels or additional_labels.tenant_id!=identity.session.tenant_id:
                raise ValueError('Trusted same-tenant additional labels required.')
        required=None;checked_at=None
        def fresh():
            if checked_at is not None and required is not None and not 0<=self.clock()-checked_at<self.freshness_seconds:
                raise SourceFailure(503)
            if before_commit is not None:
                from airport_assistant.modules.identity.customer_transaction import run_before_commit
                run_before_commit(before_commit)
        # Factories MUST explicitly accept/forward this keyword. No fallback to
        # an unguarded factory if an implementation has not adopted the port.
        async with transaction(before_commit=fresh) as tx:
            candidate = await loader(tx)
            self._candidate(identity,candidate,purpose)
            if candidate.labels is None and additional_labels is None:
                # Keep ordinary report-only commands in their original one DB
                # unit. A classification added during the write rolls it back.
                yield tx
                after=await (after_loader(tx) if after_loader is not None else loader(tx))
                self._candidate(identity,after,purpose)
                if after.resource_id!=candidate.resource_id or after.labels is not None:
                    raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return
        bundles=[labels for labels in (candidate.labels,additional_labels) if labels is not None]
        required=merge_labels(*bundles) if bundles else None
        if required is not None and purpose=='customer':
            if identity.session.kind!='customer':raise AuthError(404,'RESOURCE_NOT_FOUND')
            allowed=False if self.source_access is None else await self.source_access.allows(
                tenant_id=identity.session.tenant_id,principal_id=identity.session.principal_id,labels=required)
            if allowed is not True:raise AuthError(404,'RESOURCE_NOT_FOUND')
        checked_at=self.clock()
        async with transaction(before_commit=fresh) as tx:
            before=await loader(tx)
            self._candidate(identity,before,purpose)
            if before.resource_id!=candidate.resource_id or before.basis_hash!=candidate.basis_hash:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            fresh()
            yield tx
            after=await (after_loader(tx) if after_loader is not None else loader(tx))
            self._candidate(identity,after,purpose)
            expected=required.to_records() if required is not None else []
            actual=after.labels.to_records() if after.labels is not None else []
            if after.resource_id!=candidate.resource_id or actual!=expected:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            fresh()
        # A slow/ambiguous commit must not disclose a stale private result. This
        # can be a committed-but-unavailable outcome; clients recover via the
        # existing stable command/status protocol, never by replaying new work.
        fresh()
