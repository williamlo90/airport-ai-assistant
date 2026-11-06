"""Staff mail projections: live operational ACL, no personal source grants.

Private mode requires a dedicated whole-graph SQL projection. Legacy mode is
empty-only and never falls back from a missing private capability.
"""
from uuid import UUID

from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.customer_transaction import CustomerSessionLost
from airport_assistant.modules.source.private_content import PrivateContentGate, PrivateProjection, inherited_records
from .private_outbound import fingerprint


GROUPS = ('case_labels', 'conversation_labels', 'draft_labels', 'message_labels', 'job_labels', 'email_labels',
          'reply_labels', 'challenge_labels', 'reconcile_labels', 'history_labels')
REPLY_FIELDS = ('email_id', 'state', 'reason', 'app_message_id', 'created_at', 'body',
                'verification_state', 'send_state', 'attempts')
DELIVERY_FIELDS = ('job_id', 'app_message_id', 'email_message_id', 'delivery_status',
                   'delivery_version', 'needs_review', 'review_reason', 'queued_at',
                   'provider_status_at', 'last_checked_at')


def not_found():
    raise AuthError(404, 'RESOURCE_NOT_FOUND')


def exposed_rows(items, view):
    if type(items) is not list or len(items) > (101 if view == 'deliveries' else 100):
        not_found()
    fields = DELIVERY_FIELDS if view == 'deliveries' else REPLY_FIELDS
    result = []
    for item in items:
        if type(item) is not dict:
            not_found()
        row = {key: item.get(key) for key in fields}
        if view == 'replies':
            # Raw quarantine, challenge codes and HTML are never projected.
            if row['state'] != 'published' or row['app_message_id'] is None:
                row['body'] = None
            else:
                try:
                    if not isinstance(row['app_message_id'], UUID) and str(UUID(row['app_message_id'])) != row['app_message_id']:
                        not_found()
                except (TypeError, ValueError, AttributeError):
                    not_found()
                if row['body'] is not None and (type(row['body']) is not str or not 1 <= len(row['body']) <= 4000 or '\0' in row['body']):
                    not_found()
        result.append(row)
    return dict(items=result[:100], has_more=len(result) > 100) if view == 'deliveries' else result


class StaffMailHistory:
    def __init__(self, repository, *, private_history=False):
        if type(private_history) is not bool:
            raise ValueError('Explicit private history switch required.')
        self.repository, self.private_history = repository, private_history
        self.gate = PrivateContentGate()  # staff operational ACL, never staff source grants

    async def read(self, identity, case_id, view):
        if view not in {'replies', 'deliveries'}:
            raise ValueError('Explicit history view required.')
        if identity.session.kind != 'staff':
            raise AuthError(401, 'AUTH_REQUIRED')
        if not isinstance(case_id, UUID):
            not_found()

        async def load():
            try:
                async with self.repository.transaction(identity.database_context) as tx:
                    if await tx.session_active() is not True:
                        raise AuthError(401, 'AUTH_REQUIRED')
                    before = await tx.find_case(case_id)
                    if before is None:
                        not_found()
                    if self.private_history:
                        first = await tx.mail_history_context(case_id, view)
                        initial, _, _ = self.context(identity, case_id, view, before, first)
                    else:
                        rows = await (tx.email_replies(case_id) if view == 'replies' else tx.deliveries(case_id))
                    # The final context query is AFTER other awaited reads and
                    # session check, and must enforce live case/membership RLS.
                    if await tx.session_active() is not True:
                        raise AuthError(401, 'AUTH_REQUIRED')
                    current = await tx.find_case(case_id)
                    if current is None:
                        not_found()
                    if self.private_history:
                        final = await tx.mail_history_context(case_id, view)
                        basis, records, value = self.context(identity, case_id, view, current, final)
                        if basis != initial:
                            not_found()
                    else:
                        records = inherited_records(identity.session.tenant_id,
                            before.get('resource_security_labels'), current.get('resource_security_labels'))
                        # Supplied child labels are checked even if a corrupt
                        # parent appears public. Legacy SQL remains empty-only.
                        for row in rows:
                            if type(row) is not dict:
                                not_found()
                            groups = [row[key] for key in ('resource_security_labels', 'labels', *GROUPS) if key in row]
                            if groups:
                                records = inherited_records(identity.session.tenant_id, records, *groups)
                        if records:
                            not_found()
                        value = exposed_rows(rows, view)
                        basis = dict(case=current, value=value)
                    return PrivateProjection.capture(identity.session.tenant_id, case_id,
                        value=value, basis=basis, records=records)
            except CustomerSessionLost:
                raise AuthError(401, 'AUTH_REQUIRED') from None

        return await self.gate.read(identity, load, purpose='staff')

    @staticmethod
    def context(identity, case_id, view, case, raw):
        if (type(raw) is not dict or type(raw.get('classification_version')) is not int
                or raw['classification_version'] != 2 or raw.get('tenant_id') != str(identity.session.tenant_id)
                or raw.get('case_id') != str(case_id) or raw.get('view') != view
                or type(raw.get('case_version')) is not int or raw['case_version'] != case.get('version')
                or type(raw.get('scope_version')) is not int or raw['scope_version'] < 1):
            not_found()
        records = inherited_records(identity.session.tenant_id, *(raw.get(group) for group in GROUPS),
                                     case.get('resource_security_labels'))
        if raw.get('labels') != records or raw.get('case_labels') != case.get('resource_security_labels'):
            not_found()
        # Include all metadata and projection rows in the compared basis, not
        # only the case ID. Private changes during materialization fail closed.
        basis = fingerprint(raw)
        return basis, records, exposed_rows(raw.get('items'), view)
