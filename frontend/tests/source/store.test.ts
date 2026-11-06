import { afterEach, expect, it } from 'vitest';
import {
  sourceFixture,
  booking,
  details,
  statusOnly,
  BOOK,
  TX,
  json,
  error,
} from './fixture';
import {
  decodeBooking,
  decodeTransaction,
  decodePage,
} from '../../src/features/source/contracts';
const owned: ReturnType<typeof sourceFixture>[] = [];
const make = () => {
  const f = sourceFixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('does not fetch on entry; explicit booking read has no inferred payment access or automatic requests', async () => {
  const f = make();
  await f.ready();
  expect(f.source.paths).toEqual([]);
  await f.search();
  expect(f.source.paths).toEqual(['/api/v1/bookings/' + BOOK]);
  expect(f.sources.getState()).toMatchObject({
    phase: 'ready',
    booking: { reference: BOOK, allowed_scopes: ['booking:read'] },
    transaction: null,
    page: null,
  });
});
it('direct transaction does not require a booking lookup or expose a redacted booking reference', async () => {
  const f = make();
  await f.ready();
  f.source.transaction = statusOnly();
  await f.search('transaction', TX);
  expect(f.source.paths).toEqual(['/api/v1/transactions/' + TX]);
  expect(f.sources.getState().transaction).toEqual(statusOnly());
});
it('scope downgrade replaces complete transaction rather than retaining prior money/refund fields', async () => {
  const f = make();
  await f.ready();
  await f.search('transaction', TX);
  expect(f.sources.getState().transaction?.amount_minor).toBe(150050);
  f.source.transaction = statusOnly();
  await f.sources.lookup();
  expect(f.sources.getState().transaction).toEqual(statusOnly());
  expect(Object.hasOwn(f.sources.getState().transaction!, 'amount_minor')).toBe(
    false,
  );
});
it('source502/503/404 never fallback to a private snapshot or an empty successful page', async () => {
  const f = make();
  await f.ready();
  for (const status of [502, 503, 404]) {
    f.source.handler = null;
    await f.search('transaction', TX);
    f.source.handler = () =>
      Promise.resolve(
        error(
          status,
          status === 503 ? 'SOURCE_UNAVAILABLE' : 'RESOURCE_NOT_FOUND',
        ),
      );
    await f.sources.lookup();
    expect(f.sources.getState()).toMatchObject({
      phase: 'unavailable',
      booking: null,
      transaction: null,
      page: null,
      error: { status },
    });
    expect(f.sources.queries.getQueryCache().getAll()).toHaveLength(0);
  }
  expect(f.sources.getState().input.reference).toBe('');
});
it('booking pagination rechecks booking and page authorization and encodes opaque bytes exactly', async () => {
  const f = make();
  await f.ready();
  await f.search();
  f.source.page.next_cursor = 'page.one/+*= end';
  await f.sources.transactions();
  expect(f.sources.getState().pageNumber).toBe(1);
  f.source.page = { items: [statusOnly()], next_cursor: null };
  await f.sources.transactions(true);
  const path = f.source.paths.at(-1)!;
  expect(new URL(path, 'https://test.invalid').searchParams.get('cursor')).toBe(
    'page.one/+*= end',
  );
  expect(path).toContain('%2E');
  expect(path).not.toContain('page.one');
  expect(f.sources.getState()).toMatchObject({
    phase: 'ready',
    pageNumber: 2,
    page: {
      items: [{ status: 'unknown', allowed_scopes: ['payment_status:read'] }],
    },
  });
  expect(f.sources.getState().page?.items[0]?.amount_minor).toBeUndefined();
});
it('booking grant does not grant transactions and a failed page erases old booking/pages/cursors', async () => {
  const f = make();
  await f.ready();
  await f.search();
  await f.sources.transactions();
  f.source.handler = (path) =>
    path.includes('/transactions?')
      ? Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'))
      : null;
  await f.sources.transactions();
  expect(f.sources.getState()).toMatchObject({
    phase: 'unavailable',
    booking: null,
    page: null,
    pageNumber: 0,
  });
  const count = f.source.paths.length;
  await f.sources.transactions(true);
  expect(f.source.paths).toHaveLength(count);
});
it('late source response cannot overwrite edited reference, another lookup or an ABA selection', async () => {
  const f = make();
  await f.ready();
  let finish!: (r: Response) => void;
  f.source.handler = () =>
    new Promise((r) => {
      finish = r;
    });
  f.sources.changeInput({ kind: 'booking', reference: BOOK });
  const first = f.sources.lookup();
  await Promise.resolve();
  f.sources.changeInput({ kind: 'booking', reference: 'PK-OTHER' });
  f.source.handler = null;
  await f.sources.lookup();
  f.sources.changeInput({ kind: 'booking', reference: BOOK });
  await f.sources.lookup();
  finish(json({ ...booking(), location: 'Stale private result' }));
  await first;
  expect(f.sources.getState().booking?.location).toBe('Terminal 2 Parking');
});
it('auth checking/replacement and route detach clear snapshots, reference and cursor without automatic reads', async () => {
  const f = make();
  await f.ready();
  await f.search();
  await f.sources.transactions();
  const count = f.source.paths.length;
  await f.auth.recover();
  expect(f.sources.getState()).toMatchObject({
    phase: 'idle',
    booking: null,
    page: null,
    input: { reference: '' },
  });
  expect(f.source.paths).toHaveLength(count);
  await f.search();
  f.sources.detach();
  expect(f.sources.getState().booking).toBeNull();
  expect(f.sources.queries.getQueryCache().getAll()).toHaveLength(0);
});
it('an old response after an auth proof replacement cannot restore source fields', async () => {
  const f = make();
  await f.ready();
  let finish!: (r: Response) => void;
  f.source.handler = () =>
    new Promise((r) => {
      finish = r;
    });
  f.sources.changeInput({ kind: 'transaction', reference: TX });
  const read = f.sources.lookup();
  await Promise.resolve();
  f.server.proof = 'q'.repeat(43);
  await f.auth.recover();
  finish(json(details()));
  await read;
  expect(f.sources.getState()).toMatchObject({
    phase: 'idle',
    transaction: null,
    input: { reference: '' },
  });
});
it('invalid source identity/parent/page shapes are not accepted as source facts', async () => {
  const f = make();
  await f.ready();
  f.source.handler = () =>
    Promise.resolve(json({ ...booking(), reference: 'WRONG' }));
  await f.search();
  expect(f.sources.getState()).toMatchObject({
    phase: 'unavailable',
    booking: null,
  });
  expect(() =>
    decodePage({ items: [details(), details()], next_cursor: null }, BOOK, 20),
  ).toThrow();
  expect(() =>
    decodePage(
      {
        items: [{ ...details(), booking_reference: 'OTHER' }],
        next_cursor: null,
      },
      BOOK,
      20,
    ),
  ).toThrow();
  expect(() =>
    decodePage({ items: [details()], next_cursor: null }, BOOK, 0),
  ).toThrow();
});
it('rejects forbidden money projection including null and invalid exponent/duplicate scopes', () => {
  for (const patch of [
    { amount_minor: null },
    { currency: null },
    { refund_status: null },
    { currency_exponent: null },
    { amount_minor: 0 },
  ])
    expect(() => decodeTransaction({ ...statusOnly(), ...patch })).toThrow();
  for (const patch of [
    { currency_exponent: 0 },
    { allowed_scopes: ['payment_details:read'] },
    { allowed_scopes: ['payment_status:read', 'payment_status:read'] },
    { amount_minor: 1.5 },
    { amount_minor: 1e12 + 1 },
    { status: null },
  ])
    expect(() => decodeTransaction({ ...details(), ...patch })).toThrow();
  expect(
    decodeTransaction({ ...details(), status: 'unknown', amount_minor: 0 })
      .amount_minor,
  ).toBe(0);
});
it('validates dates, timezone, schedule and booking-only scope without inventing defaults', () => {
  for (const patch of [
    { service_timezone: 'Invalid/Zone' },
    { start_at: '2026-02-30T00:00:00Z' },
    { start_at: '2026-11-11T02:00:00Z' },
    { observed_at: '2026-11-04T02:00:00' },
    { allowed_scopes: ['booking:read', 'payment_status:read'] },
    { status: ['confirmed'] },
  ])
    expect(() => decodeBooking({ ...booking(), ...patch })).toThrow();
});
it('source facts never enter chat input, verification, report drafts or source writes', async () => {
  const f = make();
  await f.ready();
  const input = f.store.getState().input;
  await f.search();
  await f.sources.transactions();
  await f.search('transaction', TX);
  expect(f.store.getState().input).toEqual(input);
  expect(
    f.source.paths.every(
      (p) =>
        p.startsWith('/api/v1/bookings/') ||
        p.startsWith('/api/v1/transactions/'),
    ),
  ).toBe(true);
  const mutations = f.fetcher.mock.calls.filter(
    ([, i]) => i?.method && i.method !== 'GET',
  );
  expect(mutations).toHaveLength(0);
});
