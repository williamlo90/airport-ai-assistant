import { fixture, ID, OTHER, json, error } from '../chat/fixture';
import { CaseDraftStore } from '../../src/features/cases/store';
import type {
  DraftView,
  ReportPayload,
} from '../../src/features/cases/contracts';
export { ID, OTHER, json, error };
export const DRAFT = '33333333-3333-4333-8333-333333333333';
export const NEXT = '44444444-4444-4444-8444-444444444444';
export const payload: ReportPayload = {
  category: 'booking_issue',
  service_type: 'parking',
  summary: 'Parking extension question',
  customer_report:
    'My flight changed. Please explain my existing parking options.',
  contact_email: 'customer@example.com',
  booking_reference: 'PARK-9',
  transaction_reference: null,
  attachment_ids: [],
};
export function preview(): DraftView {
  return {
    draft_id: DRAFT,
    conversation_id: ID,
    action_type: 'create_case',
    payload: structuredClone(payload),
    destination: {
      queue_id: OTHER,
      organization_id: OTHER,
      queue_name: 'Customer support',
      organization_name: 'Westbridge Airport',
      service_type: 'general',
    },
    summary: 'Server preview: parking support request.',
    language: 'en',
    version: 1,
    payload_hash: 'a'.repeat(64),
    expires_at: new Date(Date.now() + 600000).toISOString(),
    state: 'pending',
    result_ref: null,
    case_id: null,
  };
}
export function caseFixture() {
  const f = fixture();
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  const delegate = f.fetcher.getMockImplementation()!;
  const server = {
    draft: preview(),
    creates: 0,
    patches: 0,
    cancels: 0,
    gets: 0,
    bodies: [] as unknown[],
    handler: null as
      | null
      | ((
          method: string,
          path: string,
          init?: RequestInit,
        ) => Promise<Response> | null),
  };
  f.fetcher.mockImplementation(async (input, init) => {
    const path = String(input),
      method = init?.method ?? 'GET';
    if (path.startsWith('/api/v1/action-drafts')) {
      const custom = server.handler?.(method, path, init);
      if (custom) return custom;
      if (method === 'POST') {
        server.creates++;
        const body = JSON.parse(String(init?.body));
        server.bodies.push(body);
        server.draft.payload = body.payload;
        return json(server.draft, 201);
      }
      if (method === 'GET') {
        server.gets++;
        return json({
          ...server.draft,
          draft_id: path.endsWith(NEXT) ? NEXT : DRAFT,
        });
      }
      if (method === 'PATCH') {
        server.patches++;
        const body = JSON.parse(String(init?.body));
        server.bodies.push(body);
        if (body.expected_version !== server.draft.version)
          return error(409, 'VERSION_CONFLICT');
        server.draft = {
          ...server.draft,
          payload: body.payload,
          version: server.draft.version + 1,
          payload_hash: 'b'.repeat(64),
          summary: 'Updated server preview',
          destination: {
            ...server.draft.destination,
            queue_name: 'Updated general support',
          },
        };
        return json(server.draft);
      }
      if (method === 'DELETE') {
        server.cancels++;
        server.draft.state = 'cancelled';
        return json(server.draft);
      }
    }
    return delegate(input, init);
  });
  const cases = new CaseDraftStore(f.auth, f.store, 'test-site'),
    off = cases.connect();
  return {
    ...f,
    cases,
    previewServer: server,
    async ready() {
      await f.auth.start();
      await f.store.open(ID);
      f.store.input({ body: payload.customer_report, service: 'other' });
      await cases.prepareFromChat('booking', 'customer@example.com');
      cases.input({ ...cases.getState().form, summary: payload.summary });
    },
    dispose() {
      off();
      cases.dispose();
      f.dispose();
    },
  };
}
