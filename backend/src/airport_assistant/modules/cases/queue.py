"""Server-scoped live keyset pages; no inferred counts or snapshot claims."""
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from uuid import UUID
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction, NEXT_ACTOR
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def parse_stamp(value):
    if type(value) is not str or len(value) > 40 or not value.endswith('+00:00'):
        raise ValueError()
    result = datetime.fromisoformat(value)
    if stamp(result) != value:
        raise ValueError()
    return result


class QueueCursor:
    def __init__(self, key):
        if len(key) < 32:
            raise ValueError('Cursor signing key required.')
        self._key = hmac.digest(key, b'staff-queue-page-v1', 'sha256')

    def issue(self, scope, watermark, position, expires):
        raw = json.dumps([scope, stamp(watermark), [stamp(position[0]), str(position[1])], expires], separators=(',', ':')).encode()
        return base64.urlsafe_b64encode(raw + hmac.digest(self._key, raw, 'sha256')).decode().rstrip('=')

    def read(self, token, scope):
        try:
            if type(token) is not str or not 1 <= len(token) <= 2048:
                raise ValueError()
            data = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
            raw, signature = data[:-32], data[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, raw, 'sha256')):
                raise ValueError()
            bound, watermark, position, expires = json.loads(raw)
            if bound != scope or type(expires) is not int or expires <= time.time() or not isinstance(position, list) or len(position) != 2 or type(position[1]) is not str:
                raise ValueError()
            water, created = parse_stamp(watermark), parse_stamp(position[0])
            if created > water:
                raise ValueError()
            return water, (created, UUID(position[1])), expires
        except (ValueError, TypeError, UnicodeError):
            raise DraftError(400, 'BAD_REQUEST') from None


class StaffQueue:
    def __init__(self, repository, cursors):
        self._repository, self._cursors = repository, cursors

    async def page(self, identity, membership, *, queue_id=None, view='all', status=None, sort='created_asc', limit=25, cursor=None):
        if view not in ('all', 'mine', 'unassigned') or status is not None and status not in NEXT_ACTOR or sort != 'created_asc' or type(limit) is not int or not 1 <= limit <= 100:
            raise DraftError(422, 'VALIDATION_ERROR')
        async with lifecycle_transaction(self._repository, identity, staff=True) as tx:
            workspace = await tx.queue_workspace(membership, queue_id)
            if workspace is None:
                raise AuthError(404, 'RESOURCE_NOT_FOUND')
            full_ids = sorted(q['id'] for q in workspace['queues'])
            fingerprint = hashlib.sha256(json.dumps(full_ids, separators=(',', ':')).encode()).hexdigest()
            s = identity.session
            scope = [str(s.tenant_id), str(s.id), str(s.principal_id), str(membership), workspace['membership_version'],
                workspace['organization']['id'], fingerprint, str(queue_id) if queue_id else None, view, status, sort, limit]
            if cursor is None:
                watermark, position, expires = await tx.queue_clock(), None, int(time.time()) + 900
            else:
                watermark, position, expires = self._cursors.read(cursor, scope)
            chosen = [queue_id] if queue_id else [UUID(v) for v in full_ids]
            rows = await tx.queue_page(UUID(workspace['organization']['id']), chosen, membership, view, status, watermark, position, limit + 1)
            selected, more = rows[:limit], len(rows) > limit
            token = self._cursors.issue(scope, watermark, (selected[-1]['created_at'], selected[-1]['id']), expires) if more else None
            return dict(membership_id=membership, membership_version=workspace['membership_version'], organization=workspace['organization'],
                queues=workspace['queues'], queue_id=queue_id, view=view, status=status, sort=sort, limit=limit, items=selected,
                has_more=more, next_cursor=token, consistency='live_keyset', watermark=watermark, observed_at=await tx.queue_clock())
