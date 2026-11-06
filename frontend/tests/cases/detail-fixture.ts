import { fixture, json, error, OTHER } from '../chat/fixture';
import { CaseDetailStore } from '../../src/features/cases/detail-store';
import type { CaseDetail } from '../../src/features/cases/detail-contracts';
export { json, error, OTHER };
export const CASE = '55555555-5555-4555-8555-555555555555',
  NEXT_CASE = '66666666-6666-4666-8666-666666666666';
export function closedCase(): CaseDetail {
  return {
    id: CASE,
    public_reference: 'CASE-' + CASE.replaceAll('-', ''),
    status: 'closed',
    summary: 'Existing parking booking after a delay',
    assigned_membership_id: null,
    next_actor: 'none',
    next_action: 'No further action is pending.',
    version: 2,
    updated_at: '2026-11-04T08:00:00Z',
    category: 'booking_issue',
    service_type: 'parking',
    customer_report: 'My flight changed. Please explain the parking procedure.',
    booking_reference: 'PARK-DEMO',
    transaction_reference: null,
    language: 'en',
    close_reason: 'resolved',
    resolution: 'Explained how to review the existing parking booking.',
    related_case_ref: null,
    closed_at: '2026-11-04T08:00:00Z',
    history: [
      {
        event_type: 'created',
        previous_version: 0,
        resulting_version: 1,
        from_status: null,
        to_status: null,
        next_actor: null,
        next_action: null,
        close_reason: null,
        resolution: null,
        customer_message: null,
        created_at: '2026-11-04T07:50:00Z',
      },
      {
        event_type: 'status_changed',
        previous_version: 1,
        resulting_version: 2,
        from_status: 'new',
        to_status: 'closed',
        next_actor: 'none',
        next_action: 'No further action is pending.',
        close_reason: 'resolved',
        resolution: 'Explained how to review the existing parking booking.',
        customer_message: null,
        created_at: '2026-11-04T08:00:00Z',
      },
    ],
  };
}
export function detailFixture() {
  const f = fixture();
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  const base = f.fetcher.getMockImplementation()!;
  const server = {
    detail: closedCase(),
    gets: 0,
    posts: [] as {
      path: string;
      body: {
        expected_version: number;
        intent: string;
        message: string;
        client_request_id: string;
      };
      idempotency: string | null;
    }[],
    applied: 0,
    loseBefore: false,
    loseAfter: false,
    receipts: new Map<string, string>(),
    handler: null as
      null | ((path: string, init?: RequestInit) => Promise<Response> | null),
  };
  f.fetcher.mockImplementation(async (input, init) => {
    const path = String(input);
    if (!path.startsWith('/api/v1/cases/')) return base(input, init);
    const custom = server.handler?.(path, init);
    if (custom) return custom;
    if (path.endsWith('/attachments')) return json([]);
    if (path.endsWith('/ratings'))
      return json({
        target_type: 'case',
        target_id: path.includes(NEXT_CASE) ? NEXT_CASE : CASE,
        eligible: server.detail.status === 'closed',
        rating: null,
      });
    const id = path.includes(NEXT_CASE) ? NEXT_CASE : CASE;
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      server.posts.push({
        path,
        body,
        idempotency: new Headers(init.headers).get('Idempotency-Key'),
      });
      if (server.loseBefore) throw Error('lost before outcome known');
      const prior = server.receipts.get(body.client_request_id),
        serialized = JSON.stringify(body);
      if (prior && prior !== serialized)
        return error(409, 'IDEMPOTENCY_CONFLICT');
      if (!prior) {
        if (body.expected_version !== server.detail.version)
          return error(409, 'VERSION_CONFLICT');
        if (server.detail.status !== 'closed')
          return error(409, 'CASE_NOT_CLOSED');
        const previous = server.detail.version;
        server.applied++;
        server.receipts.set(body.client_request_id, serialized);
        server.detail = {
          ...server.detail,
          status: 'new',
          version: previous + 1,
          next_actor: 'staff',
          next_action: 'Review the same unresolved issue.',
          close_reason: null,
          resolution: null,
          closed_at: null,
          related_case_ref: null,
          history: [
            ...server.detail.history,
            {
              event_type: 'reopened',
              previous_version: previous,
              resulting_version: previous + 1,
              from_status: 'closed',
              to_status: 'new',
              next_actor: 'staff',
              next_action: 'Review the same unresolved issue.',
              close_reason: null,
              resolution: null,
              customer_message: body.message,
              created_at: '2026-11-04T08:01:00Z',
            },
          ],
        };
      }
      if (server.loseAfter) throw Error('lost after commit');
      return json({ ...server.detail, id });
    }
    server.gets++;
    return json({ ...server.detail, id });
  });
  const details = new CaseDetailStore(f.auth, 'test-airport'),
    off = details.connect();
  return {
    ...f,
    details,
    detailServer: server,
    async ready() {
      await f.auth.start();
      await details.open(CASE);
    },
    prepare(message = 'The same parking issue still needs help.') {
      details.beginContinue();
      details.input(message);
      return {
        caseId: details.getState().detail!.id,
        version: details.getState().detail!.version,
        owner: f.auth.capture(),
        message,
        sameIssue: true,
      };
    },
    dispose() {
      off();
      details.dispose();
      f.dispose();
    },
  };
}
