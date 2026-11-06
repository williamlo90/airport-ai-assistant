import { afterEach, expect, it, vi } from 'vitest';
import {
  sourceFixture,
  BOOK,
  TX,
  booking,
  details,
  statusOnly,
  json,
  error,
} from './fixture';
import { decodeTransaction } from '../../src/features/source/contracts';

const owned: ReturnType<typeof sourceFixture>[] = [];
const make = () => {
  const f = sourceFixture();
  owned.push(f);
  return f;
};
afterEach(() => {
  vi.restoreAllMocks();
  owned.splice(0).forEach((f) => f.dispose());
});
const deferred = () => {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

it('late successful booking read cannot restore snapshots after its parallel transaction page fails', async () => {
  const f = make();
  await f.ready();
  await f.search();
  const delayed = deferred();
  f.source.handler = (path) =>
    path.includes('/transactions?')
      ? Promise.resolve(error(503, 'SOURCE_UNAVAILABLE'))
      : path.endsWith(BOOK)
        ? delayed.promise
        : null;
  await f.sources.transactions();
  expect(f.sources.getState()).toMatchObject({
    phase: 'unavailable',
    booking: null,
    page: null,
  });
  f.source.handler = null;
  await f.search('transaction', TX);
  delayed.resolve(json({ ...booking(), location: 'Old booking response' }));
  await Promise.resolve();
  expect(f.sources.getState()).toMatchObject({
    phase: 'ready',
    booking: null,
    page: null,
    transaction: { reference: TX },
  });
});

it('a late payment page cannot publish details after its fresh booking authorization is denied', async () => {
  const f = make();
  await f.ready();
  await f.search();
  const delayed = deferred();
  f.source.handler = (path) =>
    path.includes('/transactions?')
      ? delayed.promise
      : Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  await f.sources.transactions();
  delayed.resolve(json({ items: [details()], next_cursor: 'still-private' }));
  await Promise.resolve();
  expect(f.sources.getState()).toMatchObject({
    phase: 'unavailable',
    booking: null,
    transaction: null,
    page: null,
    pageNumber: 0,
    input: { reference: '' },
  });
  expect(f.sources.queries.getQueryCache().getAll()).toHaveLength(0);
});

it('an empty authorized page with a cursor remains pageable and does not retain prior payment details', async () => {
  const f = make();
  await f.ready();
  await f.search();
  f.source.page = { items: [details()], next_cursor: 'first.next' };
  await f.sources.transactions();
  f.source.page = { items: [], next_cursor: 'empty.page.next' };
  await f.sources.transactions(true);
  expect(f.sources.getState()).toMatchObject({
    page: { items: [], next_cursor: 'empty.page.next' },
    pageNumber: 2,
  });
  f.source.page = { items: [statusOnly()], next_cursor: null };
  await f.sources.transactions(true);
  expect(
    new URL(f.source.paths.at(-1)!, 'https://test.invalid').searchParams.get(
      'cursor',
    ),
  ).toBe('empty.page.next');
  expect(f.sources.getState().page?.items).toEqual([statusOnly()]);
});

it('429 waiting survives editing the reference and auth recheck without an automatic source request', async () => {
  const f = make();
  await f.ready();
  const now = Date.now();
  vi.spyOn(Date, 'now').mockReturnValue(now);
  let reads = 0;
  f.source.handler = () => {
    reads++;
    const r = error(429, 'RATE_LIMITED');
    r.headers.set('Retry-After', '60');
    return Promise.resolve(r);
  };
  await f.search();
  await f.auth.recheck();
  await f.search('transaction', TX);
  expect(reads).toBe(1);
  vi.spyOn(Date, 'now').mockReturnValue(now + 61000);
  expect(reads).toBe(1);
  await f.sources.lookup();
  expect(reads).toBe(2);
});

it('late page failure after route detach cannot erase a different fresh transaction lookup', async () => {
  const f = make();
  await f.ready();
  await f.search();
  const delayed = deferred();
  f.source.handler = (path) =>
    path.includes('/transactions?') ? delayed.promise : null;
  const paging = f.sources.transactions();
  await Promise.resolve();
  f.sources.detach();
  f.source.handler = null;
  await f.search('transaction', TX);
  delayed.resolve(error(403, 'ACTION_FORBIDDEN'));
  await paging;
  expect(f.sources.getState()).toMatchObject({
    phase: 'ready',
    input: { kind: 'transaction', reference: TX },
    transaction: { reference: TX },
    booking: null,
    page: null,
  });
});

it('details projections require every money field while authorized unknown zero is distinct from status-only absence', () => {
  for (const field of [
    'currency',
    'amount_minor',
    'currency_exponent',
    'refund_status',
  ] as const) {
    const value = { ...details() };
    delete value[field];
    expect(() => decodeTransaction(value)).toThrow();
  }
  expect(
    decodeTransaction({
      ...details(),
      status: 'unknown',
      refund_status: 'unknown',
      amount_minor: 0,
    }),
  ).toMatchObject({ status: 'unknown', amount_minor: 0 });
  expect(Object.hasOwn(decodeTransaction(statusOnly()), 'amount_minor')).toBe(
    false,
  );
});
