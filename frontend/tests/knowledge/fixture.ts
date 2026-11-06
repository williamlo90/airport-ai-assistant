import {
  adminFixture,
  MEMBER,
  ORG,
  PERSON,
  json,
} from '../extra/admin-fixture';
import { KnowledgeStore } from '../../src/features/knowledge/store';
export const DOC = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  VER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
export const content = () => ({
  body: 'Visitors may wait in the arrivals hall.',
  source_uri: 'urn:airport:parking:waiting',
  service_type: 'parking' as const,
  language: 'en' as const,
  visibility: 'public' as const,
  allowed_roles: [],
  valid_from: '2025-01-01T00:00:00Z',
  valid_to: null,
  applicability: {
    terminal: null,
    booking_created_from: null,
    booking_created_to: null,
  },
  policy_reference: null,
  supersedes_version_id: null,
});
export async function fixture() {
  const f = await adminFixture(),
    store = new KnowledgeStore(f.staff),
    off = store.connect();
  const state = {
    document: {
      id: DOC,
      organization_id: ORG,
      document_key: 'waiting-info',
      title: 'Waiting information',
      revision: 2,
      created_by: PERSON,
      created_at: '2026-11-01T00:00:00Z',
      updated_at: '2026-11-01T00:00:00Z',
    },
    version: {
      ...content(),
      id: VER,
      document_id: DOC,
      version: 1,
      revision: 1,
      organization_id: null,
      content_hash: 'a'.repeat(64),
      basis_hash: 'b'.repeat(64),
      status: 'draft',
      published_at: null as string | null,
      published_by: null as string | null,
      withdrawn_at: null as string | null,
      withdrawn_by: null as string | null,
      withdrawal_reason: null as string | null,
      index_generation: null,
      created_by: PERSON,
      created_at: '2026-11-01T00:00:00Z',
      updated_at: '2026-11-01T00:00:00Z',
    },
    ready: false,
    lost: false,
  };
  const calls: {
    path: string;
    method: string;
    body: Record<string, unknown> | null;
  }[] = [];
  const original = f.server.handler;
  f.server.handler = (path, init) => {
    if (!path.includes('/admin/knowledge/'))
      return original?.(path, init) ?? null;
    const method = init?.method ?? 'GET',
      body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ path, method, body });
    const url = new URL(path, 'https://test.invalid');
    if (method === 'POST' || method === 'PATCH') {
      if (path.endsWith('/documents'))
        state.document = {
          ...state.document,
          title: body.title,
          document_key: body.document_key,
        };
      else if (path.endsWith('/index')) state.ready = true;
      else if (path.endsWith('/publish')) {
        state.version.status = 'published';
        state.version.published_at = '2026-11-01T00:00:00Z';
        state.version.published_by = PERSON;
        state.version.revision++;
      } else if (path.endsWith('/withdraw')) {
        state.version.status = 'withdrawn';
        state.version.withdrawal_reason = body.reason;
        state.version.withdrawn_at = '2026-11-01T00:00:00Z';
        state.version.withdrawn_by = PERSON;
        state.version.revision++;
      } else if (body.content) {
        state.version = {
          ...state.version,
          ...body.content,
          revision: state.version.revision + 1,
        };
        state.ready = false;
      }
      if (state.lost) return Promise.reject(Error('response lost'));
      if (path.endsWith('/index'))
        return Promise.resolve(
          json({
            version_id: VER,
            generation: DOC,
            basis_hash: state.version.basis_hash,
            valid_until: '2099-01-01T00:00:00Z',
          }),
        );
      return Promise.resolve(
        json(
          path.endsWith('/documents') ? state.document : state.version,
          path.endsWith('/documents') || path.endsWith('/versions') ? 201 : 200,
        ),
      );
    }
    if (url.pathname.endsWith('/index'))
      return Promise.resolve(
        json({
          version_id: VER,
          basis_hash: state.version.basis_hash,
          ready: state.ready,
          generation: state.ready ? DOC : null,
          indexing_available: true,
        }),
      );
    if (url.pathname.endsWith('/documents'))
      return Promise.resolve(
        json({
          membership_id: MEMBER,
          organization_id: ORG,
          items: [state.document],
          limit: 25,
          has_more: false,
          next_after: null,
          consistency: 'live_keyset',
        }),
      );
    if (url.pathname.endsWith('/versions'))
      return Promise.resolve(
        json({ items: [state.version], has_more: false, next_after: null }),
      );
    return Promise.resolve(
      json(
        url.pathname.includes('/versions/') ? state.version : state.document,
      ),
    );
  };
  return {
    ...f,
    store,
    state,
    calls,
    dispose() {
      off();
      f.dispose();
    },
  };
}
