import { afterEach, expect, it } from 'vitest';
import { fixture, content, DOC } from './fixture';
import { content as schema } from '../../src/features/knowledge/contracts';
import { OTHER_ORG, deferred, json } from '../staff/qa-fixture';
const owned: Awaited<ReturnType<typeof fixture>>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function setup() {
  const f = await fixture();
  owned.push(f);
  await f.store.refresh();
  await f.store.open(DOC);
  return f;
}
it('current-scoped directory opens actual versions and index status with no commands', async () => {
  const f = await setup();
  expect(f.store.getState()).toMatchObject({
    phase: 'ready',
    document: { id: DOC },
    version: { status: 'draft' },
    index: { ready: false },
  });
  expect(f.calls.every((c) => c.method === 'GET')).toBe(true);
});
it('index then publish and withdrawal each need explicit immutable review, never a ready override', async () => {
  const f = await setup();
  f.store.review({ kind: 'index' });
  expect(f.calls.every((c) => c.method === 'GET')).toBe(true);
  await f.store.confirm();
  expect(f.store.getState().index?.ready).toBe(true);
  f.store.review({ kind: 'publish' });
  await f.store.confirm();
  expect(f.store.getState().version?.status).toBe('published');
  f.store.review({ kind: 'edit', content: content() });
  expect(f.store.getState().review).toBeNull();
  f.store.review({ kind: 'withdraw', reason: 'Source is superseded.' });
  await f.store.confirm();
  expect(f.store.getState().version?.status).toBe('withdrawn');
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(3);
});
it('lost index response recovers actual readiness by GET without re-embedding or publishing', async () => {
  const f = await setup();
  f.state.lost = true;
  f.store.review({ kind: 'index' });
  await f.store.confirm();
  expect(f.store.getState()).toMatchObject({
    phase: 'unknown',
    version: null,
    index: null,
    review: null,
  });
  await f.store.confirm();
  await f.store.recover();
  expect(f.store.getState()).toMatchObject({
    notice: 'current',
    index: { ready: true },
    version: { status: 'draft' },
  });
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(1);
});
it('lost create is discovered by exact key without blind recreate or receipt claim', async () => {
  const f = await fixture();
  owned.push(f);
  await f.store.refresh();
  f.state.lost = true;
  f.store.review({
    kind: 'create',
    title: 'New info',
    document_key: 'new-info',
    content: content(),
  });
  await f.store.confirm();
  expect(f.store.getState().phase).toBe('unknown');
  await f.store.recover();
  expect(f.calls.at(-1)?.path).toContain('document_key=new-info');
  expect(f.store.getState().notice).toBe('current');
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(1);
});
it('scope refresh purges body and late reads cannot restore it', async () => {
  const f = await setup();
  await f.auth.recover();
  expect(f.store.getState()).toMatchObject({
    phase: 'idle',
    version: null,
    review: null,
  });
  f.state.document.organization_id = OTHER_ORG;
  await f.store.refresh();
  expect(f.store.getState()).toMatchObject({
    phase: 'unavailable',
    page: null,
  });
});
it('draft edit reviews exact metadata including audience and applicability', async () => {
  const f = await setup();
  const v = {
    ...content(),
    body: 'Updated guidance.',
    visibility: 'staff' as const,
    allowed_roles: ['cs' as const],
    applicability: {
      terminal: 'T2',
      booking_created_from: null,
      booking_created_to: null,
    },
  };
  f.store.review({ kind: 'edit', content: v });
  expect(f.store.getState().review?.command).toEqual({
    kind: 'edit',
    content: v,
  });
  f.store.cancel();
  expect(f.calls.every((c) => c.method === 'GET')).toBe(true);
});
it('Unicode/dates/visibility/source validation matches constrained content', () => {
  expect(
    schema.safeParse({ ...content(), body: '😀'.repeat(20000) }).success,
  ).toBe(true);
  for (const c of [
    { body: ' ' },
    { body: 'x\0' },
    { body: '\uD800' },
    { body: '😀'.repeat(20001) },
    { source_uri: 'https://user:pass@example.com/a' },
    { source_uri: 'https://example.com/a?key=x' },
    { valid_from: '2026-01-01T00:00:00' },
    { visibility: 'staff' },
    { allowed_roles: ['admin'] },
    { valid_to: '2020-01-01T00:00:00Z' },
  ])
    expect(schema.safeParse({ ...content(), ...c }).success).toBe(false);
});

it('exact-key unknown creation ignores an old later-page UUID cursor', async () => {
  const f = await fixture();
  owned.push(f);
  const original = f.server.handler!;
  let page = 0;
  f.server.handler = (path, init) => {
    const url = new URL(path, 'https://test.invalid');
    if (
      url.pathname.endsWith('/documents') &&
      (init?.method ?? 'GET') === 'GET' &&
      !url.searchParams.has('document_key')
    ) {
      page++;
      const rows =
        page === 1
          ? Array.from({ length: 25 }, (_, i) => ({
              ...f.state.document,
              id: 'eeeeeeee-eeee-4eee-8eee-' + String(i + 1).padStart(12, '0'),
            }))
          : [
              {
                ...f.state.document,
                id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
              },
            ];
      return Promise.resolve(
        new Response(
          JSON.stringify({
            membership_id: '33333333-3333-4333-8333-333333333333',
            organization_id: f.state.document.organization_id,
            items: rows,
            limit: 25,
            has_more: page === 1,
            next_after: page === 1 ? rows.at(-1)!.id : null,
            consistency: 'live_keyset',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        ),
      );
    }
    return original(path, init);
  };
  await f.store.refresh();
  await f.store.next();
  f.state.lost = true;
  f.store.review({
    kind: 'create',
    title: 'New info',
    document_key: 'new-info',
    content: content(),
  });
  await f.store.confirm();
  await f.store.recover();
  const last = f.calls.at(-1)!;
  expect(last.path).toContain('document_key=new-info');
  expect(last.path).not.toContain('after=');
  expect(f.store.getState().page?.items[0]?.id).toBe(DOC);
  expect(f.calls.filter((c) => c.method === 'POST')).toHaveLength(1);
});

it('a delayed knowledge version page cannot repopulate body or review after the admin role is revoked', async () => {
  const f = await setup(),
    delayed = deferred();
  const previous = f.server.handler;
  let started!: () => void;
  const dispatched = new Promise<void>((done) => {
    started = done;
  });
  f.server.handler = (path, init) => {
    if (path.includes('/admin/knowledge/') && path.includes('/versions?')) {
      started();
      return delayed.promise;
    }
    return previous?.(path, init) ?? null;
  };
  const pending = f.store.recover();
  await dispatched;
  f.server.memberships[0] = {
    ...f.server.memberships[0]!,
    version: 2,
    roles: ['cs'],
  };
  await f.auth.recover();
  delayed.resolve(
    json({ items: [f.state.version], has_more: false, next_after: null }),
  );
  await pending;
  expect(f.store.getState()).toMatchObject({
    phase: 'idle',
    page: null,
    document: null,
    version: null,
    versions: null,
    review: null,
    index: null,
    notice: null,
  });
  const writes = f.calls.filter((c) => c.method !== 'GET').length;
  f.store.review({ kind: 'publish' });
  await f.store.confirm();
  expect(f.calls.filter((c) => c.method !== 'GET')).toHaveLength(writes);
});
