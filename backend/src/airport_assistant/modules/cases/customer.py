"""Owned customer pages; cursors are positions, never authorization tickets."""
import hashlib
import asyncio
import hmac
import json
import time

from airport_assistant.modules.cases.lifecycle import lifecycle_transaction, NEXT_ACTOR
from airport_assistant.modules.cases.queue import QueueCursor
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.labels import SecurityLabels, ResourceSecurityLabel
from airport_assistant.modules.source.models import SourceFailure


class CustomerCaseCursor(QueueCursor):
    def __init__(self, key):
        if len(key) < 32:
            raise ValueError('Cursor signing key required.')
        self._key = hmac.digest(key, b'customer-case-page-v1', hashlib.sha256)


class CustomerCases:
    def __init__(self, repository, cursors, private_access=None):
        self._repository, self._cursors = repository, cursors
        self._private_access = private_access
        self.clock = time.monotonic

    async def page(self, identity, *, status=None, limit=25, cursor=None):
        if identity.session.kind != 'customer':
            raise AuthError(401, 'AUTH_REQUIRED')
        if status is not None and status not in NEXT_ACTOR or type(limit) is not int or not 1 <= limit <= 100:
            raise DraftError(422, 'VALIDATION_ERROR')
        s = identity.session
        scope = [str(s.tenant_id), str(s.id), str(s.principal_id), status, limit, 'created_asc']
        watermark = position = expires = None
        if cursor is not None:
            watermark, position, expires = self._cursors.read(cursor, scope)

        async def load():
            nonlocal watermark, expires
            async with lifecycle_transaction(self._repository, identity, customer=True) as tx:
                if watermark is None:
                    watermark, expires = await tx.queue_clock(), int(time.time()) + 900
                rows = await tx.customer_case_page(status, watermark, position, limit + 1)
                # Include the lookahead row's labels: has_more/cursor must not
                # disclose a private candidate whose source access was denied.
                chunks = page_label_chunks(s.tenant_id, rows)
                selected, more = rows[:limit], len(rows) > limit
                fields = ('id', 'public_reference', 'status', 'summary', 'next_actor', 'next_action', 'version', 'created_at', 'updated_at')
                items = [{k: row[k] for k in fields} for row in selected]
                token = self._cursors.issue(scope, watermark, (selected[-1]['created_at'], selected[-1]['id']), expires) if more else None
                result = dict(items=items, status=status, limit=limit, sort='created_asc', has_more=more,
                              next_cursor=token, watermark=watermark, consistency='live_keyset')
                basis = hashlib.sha256(json.dumps(dict(scope=scope, rows=rows, watermark=watermark, position=position),
                    sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()).hexdigest()
                return result, basis, chunks

        candidate, basis, chunks = await load()
        if not chunks:
            return candidate
        # A page may legitimately exceed a single aggregate's 64 labels.
        # Authorize deterministic chunks serially, retaining ONE deadline from
        # the first check, then reread the entire ordered page/current ancestry.
        # No widening TTL or truncating requirements to make a page pass.
        checked_at = self.clock()
        def fresh():
            elapsed = self.clock() - checked_at
            if not 0 <= elapsed < 2:
                raise SourceFailure(503)
            return 2 - elapsed
        for labels in chunks:
            remaining = fresh()
            try:
                # Cancellation bounds the whole sequential provider loop inside
                # allows(), not just the time between completed chunks.
                async with asyncio.timeout(remaining):
                    allowed = self._private_access is not None and await self._private_access.allows(
                        tenant_id=s.tenant_id, principal_id=s.principal_id, labels=labels)
            except TimeoutError:
                raise SourceFailure(503) from None
            if allowed is not True:
                raise AuthError(404, 'RESOURCE_NOT_FOUND')
            fresh()
        current, current_basis, _ = await load()
        if current_basis != basis:
            raise DraftError(409, 'SOURCE_CONTEXT_CHANGED')
        fresh()
        return current


def page_label_chunks(tenant_id, rows):
    """Validate each stored aggregate, union scopes, chunk without dropping refs."""
    merged = {}
    for row in rows:
        records = row['resource_security_labels']
        if type(records) is not list:
            raise AuthError(404, 'RESOURCE_NOT_FOUND')
        if not records:
            continue
        try:
            bundle = SecurityLabels.from_records(tenant_id, records)
        except (ValueError, TypeError):
            raise AuthError(404, 'RESOURCE_NOT_FOUND') from None
        for label in bundle.labels:
            merged.setdefault((label.resource_type, label.reference), set()).update(label.required_source_scopes)
    labels = [ResourceSecurityLabel(source='reference', resource_type=kind, reference=ref,
              required_source_scopes=tuple(sorted(scopes))) for (kind, ref), scopes in sorted(merged.items())]
    return tuple(SecurityLabels(tenant_id, tuple(labels[i:i+64])) for i in range(0, len(labels), 64))
