"""Conservative selected-membership capabilities and signed live directory pages."""
import base64
import hashlib
import hmac
import json
import time
from uuid import UUID
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction, TRANSITIONS
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


class AssigneeCursor:
    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError('Cursor signing key required.')
        self._key = hmac.digest(key, b'staff-assignee-directory-v1', 'sha256')

    def issue(self, scope, position):
        raw = json.dumps([scope, str(position), int(time.time()) + 900], separators=(',', ':')).encode()
        return base64.urlsafe_b64encode(raw + hmac.digest(self._key, raw, 'sha256')).decode().rstrip('=')

    def read(self, value, scope):
        try:
            if type(value) is not str or not 1 <= len(value) <= 2048:
                raise ValueError()
            data = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
            raw, signature = data[:-32], data[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, raw, 'sha256')):
                raise ValueError()
            bound, position, expires = json.loads(raw)
            if bound != scope or type(expires) is not int or expires <= time.time() or type(position) is not str:
                raise ValueError()
            return UUID(position)
        except (ValueError, TypeError, UnicodeError):
            raise DraftError(400, 'BAD_REQUEST') from None


def allowed(case, workspace, chat, current_assignee):
    actions, statuses, reasons = [], [], []
    claim = None
    if case['status'] != 'closed':
        if workspace['supervisor']:
            actions.append('assign')
            if case['status'] == 'new' and case['assigned_membership_id'] is not None and (chat is None or chat['mode'] != 'human'):
                actions.append('unassign')
        elif case['assigned_membership_id'] is None and chat is None:
            actions.append('claim')
            claim = workspace['membership_id']
        if current_assignee is not None:
            statuses = sorted(TRANSITIONS[case['status']] - {'closed'})
            if statuses:
                actions.append('update_status')
        actions.append('close')
        reasons = ['resolved', 'customer_cancelled', 'duplicate', 'no_response']
    return dict(allowed_actions=actions, allowed_statuses=statuses, allowed_close_reasons=reasons, self_claim_membership_id=claim)


class StaffCapabilities:
    def __init__(self, repository, cursors):
        self._repository, self._cursors = repository, cursors

    @staticmethod
    async def basis(tx, id, membership):
        case = await tx.find_case(id, lock=True)
        if case is None:
            raise AuthError(404, 'RESOURCE_NOT_FOUND')
        chat = await tx.active_chat(id, lock=True)
        workspace = await tx.staff_workspace(id, membership)
        if workspace is None:
            raise AuthError(404, 'RESOURCE_NOT_FOUND')
        return case, chat, workspace

    async def get(self, identity, id, membership):
        async with lifecycle_transaction(self._repository, identity, staff=True) as tx:
            case, chat, workspace = await self.basis(tx, id, membership)
            current = await tx.current_assignee(id)
            return dict(case_id=id, case_version=case['version'], actor_principal_id=identity.session.principal_id,
                membership_id=workspace['membership_id'], membership_version=workspace['membership_version'],
                organization=workspace['organization'], queue=workspace['queue'], assignee=workspace['assignee'],
                active_conversation=dict(id=chat['id'], version=chat['version'], mode=chat['mode']) if chat else None,
                **allowed(case, workspace, chat, current))

    async def assignees(self, identity, id, membership, cursor=None, limit=25):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DraftError(422, 'VALIDATION_ERROR')
        async with lifecycle_transaction(self._repository, identity, staff=True) as tx:
            case, _, workspace = await self.basis(tx, id, membership)
            if not workspace['supervisor']:
                raise AuthError(403, 'ACTION_FORBIDDEN')
            s = identity.session
            scope = [str(s.tenant_id), str(s.id), str(s.principal_id), str(id), case['version'],
                str(membership), workspace['membership_version'], workspace['organization']['id'], workspace['queue']['id'], limit]
            position = self._cursors.read(cursor, scope) if cursor is not None else None
            rows = [] if case['status'] == 'closed' else await tx.staff_assignees(id, membership, position, limit + 1)
            if rows is None:
                raise AuthError(404, 'RESOURCE_NOT_FOUND')
            items = rows[:limit]
            more = len(rows) > limit
            return dict(case_id=id, case_version=case['version'], membership_id=membership,
                membership_version=workspace['membership_version'], items=items, has_more=more,
                next_cursor=self._cursors.issue(scope, items[-1]['membership_id']) if more else None)

    async def duplicate_candidates(self, identity, id, command):
        # Validate internal callers too; a read must never become a wildcard search.
        from airport_assistant.modules.cases.duplicate import DuplicateSearch
        command = DuplicateSearch.model_validate(command)
        async with lifecycle_transaction(self._repository, identity, staff=True) as tx:
            case, _, workspace = await self.basis(tx, id, command.membership_id)
            if case['version'] != command.expected_version:
                raise DraftError(409, 'VERSION_CONFLICT')
            if case['status'] == 'closed':
                raise DraftError(409, 'CASE_CLOSED')
            candidate = await tx.duplicate_candidate(command.public_reference, case, command.membership_id)
            return dict(case_id=id, case_version=case['version'], membership_id=workspace['membership_id'],
                membership_version=workspace['membership_version'], items=[candidate] if candidate else [])
