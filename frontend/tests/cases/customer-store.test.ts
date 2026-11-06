import { afterEach, expect, it } from 'vitest';
import { fixture, json, error, OTHER } from '../chat/fixture';
import { CustomerCasesStore } from '../../src/features/cases/customer-store';
import { decodeCustomerCases } from '../../src/features/cases/customer-contracts';

const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((f) => f()));
const item = (n = 1) => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  public_reference: 'CASE-' + String(n).padStart(32, '0'),
  status: 'new',
  summary: 'Parking help',
  next_actor: 'staff',
  next_action: 'Review the request',
  version: 1,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
});
const page = (more = false) => ({
  items: more ? Array.from({ length: 25 }, (_, i) => item(i + 1)) : [item(26)],
  status: null,
  limit: 25,
  sort: 'created_asc',
  has_more: more,
  next_cursor: more ? 'opaque_cursor' : null,
  watermark: '2026-11-06T00:00:00Z',
  consistency: 'live_keyset',
});
async function setup() {
  const f = fixture();
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  await f.auth.start();
  const store = new CustomerCasesStore(f.auth),
    off = store.connect();
  owned.push(() => {
    off();
    f.dispose();
  });
  return { ...f, cases: store };
}
it('paginates with the exact opaque cursor, replaces pages and resets cursor on refresh/filter', async () => {
  const f = await setup(),
    paths: string[] = [];
  f.server.override = async (path) => {
    paths.push(path);
    const url = new URL(path, 'https://test.invalid');
    return json({
      ...page(!url.searchParams.has('cursor')),
      status: url.searchParams.get('status'),
    });
  };
  await f.cases.refresh();
  expect(f.cases.getState().page?.items).toHaveLength(25);
  await f.cases.next();
  expect(paths[1]).toContain('cursor=opaque_cursor');
  expect(f.cases.getState()).toMatchObject({
    phase: 'ready',
    canGoBack: true,
    page: { items: [item(26)] },
  });
  await f.cases.previous();
  expect(paths[2]).not.toContain('cursor');
  await f.cases.refresh('new');
  expect(paths.at(-1)).toBe('/api/v1/cases?limit=25&status=new');
});
it('does not request customer records using an anonymous session', async () => {
  const f = fixture(),
    cases = new CustomerCasesStore(f.auth);
  owned.push(() => f.dispose());
  await f.auth.start();
  const initial = f.fetcher.mock.calls.length;
  await cases.refresh();
  expect(f.fetcher).toHaveBeenCalledTimes(initial);
  expect(cases.getState()).toMatchObject({ phase: 'unavailable', page: null });
});
it('erases old content on denied/source-unavailable pages and never retries them automatically', async () => {
  const f = await setup();
  f.server.override = async () => json(page());
  await f.cases.refresh();
  f.server.override = async () => error(404, 'RESOURCE_NOT_FOUND');
  await f.cases.refresh();
  expect(f.cases.getState()).toMatchObject({
    phase: 'unavailable',
    page: null,
  });
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await f.cases.refresh();
  expect(f.cases.getState()).toMatchObject({
    phase: 'unavailable',
    page: null,
  });
});
it('drops a late page after a newer filter selection and on an auth boundary change', async () => {
  const f = await setup();
  let finish!: (r: Response) => void;
  f.server.override = () =>
    new Promise<Response>((resolve) => {
      finish = resolve;
    });
  const old = f.cases.refresh();
  f.server.override = async () => json({ ...page(), status: 'new' });
  await f.cases.refresh('new');
  finish(json(page()));
  await old;
  expect(f.cases.getState().status).toBe('new');
  expect(f.cases.getState().phase).toBe('ready');
  f.server.override = () =>
    new Promise<Response>((resolve) => {
      finish = resolve;
    });
  const pending = f.cases.refresh();
  f.server.proof = 'q'.repeat(43);
  expect(() => f.auth.capture()).toThrow();
  finish(json(page()));
  await pending;
  expect(f.cases.getState().page).toBeNull();
});
it('rejects malformed continuation, duplicates and mismatched filters', () => {
  expect(() => decodeCustomerCases({ ...page(), has_more: true })).toThrow();
  expect(() =>
    decodeCustomerCases({ ...page(), items: [item(), item()] }),
  ).toThrow();
  expect(() => decodeCustomerCases({ ...page(), status: 'closed' })).toThrow();
});
it('accepts actual microsecond database ordering even when JavaScript milliseconds tie', () => {
  expect(
    decodeCustomerCases({
      ...page(),
      items: [
        { ...item(2), created_at: '2026-11-01T00:00:00.000001Z' },
        { ...item(1), created_at: '2026-11-01T00:00:00.000002Z' },
      ],
    }).items,
  ).toHaveLength(2);
});
