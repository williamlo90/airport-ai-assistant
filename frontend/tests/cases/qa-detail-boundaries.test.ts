import { afterEach, expect, it, vi } from 'vitest';
import {
  detailFixture,
  closedCase,
  CASE,
  NEXT_CASE,
  json,
  error,
} from './detail-fixture';

const owned: ReturnType<typeof detailFixture>[] = [];
const make = () => {
  const f = detailFixture();
  owned.push(f);
  return f;
};
afterEach(() => {
  vi.restoreAllMocks();
  owned.splice(0).forEach((f) => f.dispose());
});
const deferred = () => {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

it('accepts a definitive historical replay200 showing a later closed state without inventing an active status', async () => {
  const f = make();
  await f.ready();
  f.detailServer.handler = (_p, init) =>
    init?.method === 'POST'
      ? Promise.resolve(
          json({
            ...closedCase(),
            version: 6,
            resolution: 'A later closure result',
          }),
        )
      : null;
  await f.details.continue(f.prepare());
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    notice: 'recorded',
    detail: {
      status: 'closed',
      version: 6,
      resolution: 'A later closure result',
    },
  });
});

it('late409 reconciliation cannot close another case editor or annotate it with the old conflict', async () => {
  const f = make();
  await f.ready();
  const delayed = deferred();
  let start!: () => void;
  const started = new Promise<void>((done) => {
    start = done;
  });
  f.detailServer.handler = (path, init) => {
    if (init?.method === 'POST')
      return Promise.resolve(error(409, 'VERSION_CONFLICT'));
    if (path.endsWith(CASE)) {
      start();
      return delayed.promise;
    }
    return null;
  };
  const sending = f.details.continue(f.prepare());
  await started;
  await f.details.open(NEXT_CASE);
  f.details.beginContinue();
  f.details.input('Different case private edit');
  delayed.resolve(json(closedCase()));
  await sending;
  expect(f.details.getState()).toMatchObject({
    detail: { id: NEXT_CASE },
    editing: true,
    message: 'Different case private edit',
    error: null,
    notice: null,
  });
});

it('late reviewAgain GET cannot transfer the old continuation intent onto another selected case', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseBefore = true;
  await f.details.continue(f.prepare());
  f.detailServer.detail.version = 3;
  await f.details.recover();
  const delayed = deferred();
  f.detailServer.handler = (path, init) =>
    init?.method !== 'POST' && path.endsWith(CASE) ? delayed.promise : null;
  const review = f.details.reviewAgain();
  await f.details.open(NEXT_CASE);
  f.details.beginContinue();
  f.details.input('Another case explanation');
  delayed.resolve(json({ ...closedCase(), version: 3 }));
  await review;
  expect(f.details.getState()).toMatchObject({
    detail: { id: NEXT_CASE },
    editing: true,
    message: 'Another case explanation',
    notice: null,
  });
});

it('429 cooldown survives same-proof auth checking and requires another explicit action after the deadline', async () => {
  const f = make();
  await f.ready();
  const now = Date.now();
  vi.spyOn(Date, 'now').mockReturnValue(now);
  let posts = 0;
  f.detailServer.handler = (_p, init) => {
    if (init?.method !== 'POST') return null;
    posts++;
    const r = error(429, 'RATE_LIMITED');
    r.headers.set('Retry-After', '60');
    return Promise.resolve(r);
  };
  await f.details.continue(f.prepare());
  await f.auth.recheck();
  await f.details.open(CASE);
  await f.details.continue(f.prepare());
  expect(posts).toBe(1);
  vi.spyOn(Date, 'now').mockReturnValue(now + 61000);
  expect(posts).toBe(1);
  await f.details.continue(f.prepare());
  expect(posts).toBe(2);
});

it('a delayed successful continuation cannot restore private history after auth replacement', async () => {
  const f = make();
  await f.ready();
  const delayed = deferred();
  f.detailServer.handler = (_p, init) =>
    init?.method === 'POST' ? delayed.promise : null;
  const sending = f.details.continue(f.prepare());
  f.server.proof = 'z'.repeat(43);
  f.server.principal = CASE;
  await f.auth.recheck();
  delayed.resolve(json({ ...closedCase(), version: 3 }));
  await sending;
  expect(f.details.getState()).toMatchObject({
    phase: 'idle',
    detail: null,
    message: '',
    notice: null,
  });
  expect(f.details.queries.getQueryCache().getAll()).toHaveLength(0);
});
