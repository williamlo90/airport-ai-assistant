import { afterEach, expect, it } from 'vitest';
import { fixture, json, error, ID, OTHER } from '../chat/fixture';
import { RatingStore } from '../../src/features/feedback/store';
import { ratingInput } from '../../src/features/feedback/contracts';
const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((f) => f()));
export const rating = (version = 1) => ({
  id: OTHER,
  score: 4,
  comment: 'Helpful.',
  version,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
});
async function setup() {
  const f = fixture();
  await f.auth.start();
  const store = new RatingStore(f.auth),
    off = store.connect();
  owned.push(() => {
    off();
    f.dispose();
  });
  return { ...f, store };
}
const view = (r: ReturnType<typeof rating> | null = null, eligible = true) => ({
  target_type: 'conversation',
  target_id: ID,
  eligible,
  rating: r,
});
it('reads eligibility, never submits an ineligible user-only conversation', async () => {
  const f = await setup();
  f.server.override = async () => json(view(null, false));
  await f.store.open({ kind: 'conversation', id: ID });
  await f.store.save(4, 'Helpful.');
  expect(
    f.fetcher.mock.calls.filter(([, i]) => i?.method === 'POST'),
  ).toHaveLength(0);
});
it('sends one explicit versioned rating with no client identity, only after actual GET', async () => {
  const f = await setup();
  let posts = 0;
  f.server.override = async (path, init) => {
    expect(path).toBe('/api/v1/conversations/' + ID + '/ratings');
    if (init?.method === 'POST') {
      posts++;
      expect(JSON.parse(String(init.body))).toEqual({
        score: 4,
        comment: 'Helpful.',
        expected_version: 0,
      });
      return json(view(rating()));
    }
    return json(view());
  };
  await f.store.open({ kind: 'conversation', id: ID });
  await f.store.save(4, 'Helpful.');
  expect(posts).toBe(1);
  expect(f.store.getState().notice).toBe('saved');
});
it('unknown POST locks mutation; recovery uses only GET and labels current state rather than causality', async () => {
  const f = await setup();
  let writes = 0,
    reads = 0;
  f.server.override = async (_, init) => {
    if (init?.method === 'POST') {
      writes++;
      throw Error('lost');
    }
    reads++;
    return json(view(reads > 1 ? rating(3) : null));
  };
  await f.store.open({ kind: 'conversation', id: ID });
  await f.store.save(4, 'Helpful.');
  expect(f.store.getState().phase).toBe('unknown');
  expect(f.store.getState().value).toBeNull();
  await f.store.save(5, 'Again');
  expect(writes).toBe(1);
  await f.store.recover();
  expect(reads).toBe(2);
  expect(f.store.getState()).toMatchObject({
    phase: 'ready',
    notice: 'current',
    value: { rating: { version: 3 } },
  });
  expect(writes).toBe(1);
});
it('version conflict clears the old form and requires a fresh GET before another command', async () => {
  const f = await setup();
  f.server.override = async (_, i) =>
    i?.method === 'POST' ? error(409, 'VERSION_CONFLICT') : json(view());
  await f.store.open({ kind: 'conversation', id: ID });
  await f.store.save(4, 'Helpful.');
  expect(f.store.getState()).toMatchObject({
    phase: 'unavailable',
    value: null,
  });
  await f.store.save(3, 'New');
  expect(
    f.fetcher.mock.calls.filter(([, i]) => i?.method === 'POST'),
  ).toHaveLength(1);
});
it('hides existing rating on source hold and discards delayed old-target responses', async () => {
  const f = await setup();
  f.server.override = async () => json(view(rating()));
  await f.store.open({ kind: 'conversation', id: ID });
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await f.store.recover();
  expect(f.store.getState().value).toBeNull();
  let resolve!: (v: Response) => void;
  f.server.override = () =>
    new Promise((r) => {
      resolve = r;
    });
  const pending = f.store.open({ kind: 'conversation', id: ID });
  f.store.clear();
  resolve(json(view(rating())));
  await pending;
  expect(f.store.getState()).toMatchObject({ phase: 'idle', value: null });
});
it('rejects mismatched resource identity and masks raw errors', async () => {
  const f = await setup();
  f.server.override = async () => json({ ...view(rating()), target_id: OTHER });
  await f.store.open({ kind: 'conversation', id: ID });
  expect(f.store.getState()).toMatchObject({
    phase: 'unavailable',
    value: null,
  });
  expect(f.store.getState().error?.message).not.toContain(OTHER);
});
it('auth boundary reset purges comment and ignores late save', async () => {
  const f = await setup();
  f.server.override = async () => json(view(rating()));
  await f.store.open({ kind: 'conversation', id: ID });
  await f.auth.recover();
  expect(f.store.getState().value).toBeNull();
});
it('keeps existing feedback readable after reopen without editing', async () => {
  const f = await setup();
  f.server.override = async () => json(view(rating(), false));
  await f.store.open({ kind: 'conversation', id: ID });
  expect(f.store.getState().value?.rating?.score).toBe(4);
  await f.store.save(1, 'Changed');
  expect(
    f.fetcher.mock.calls.filter(([, i]) => i?.method === 'POST'),
  ).toHaveLength(0);
});
it('validates code points, integer ranges, no default, null or meaningful Unicode', () => {
  const valid = { score: 5, expected_version: 0, comment: '😀'.repeat(2000) };
  expect(ratingInput.safeParse(valid).success).toBe(true);
  for (const change of [
    { score: 0 },
    { score: 1.5 },
    { score: '5' },
    { score: undefined },
    { comment: '😀'.repeat(2001) },
    { comment: '   ' },
    { comment: '\uD800' },
    { comment: '\0' },
    { expected_version: 2147483647 },
  ])
    expect(ratingInput.safeParse({ ...valid, ...change }).success).toBe(false);
});
it('rate-limited reads and saves do not retry automatically or on immediate click', async () => {
  const f = await setup();
  f.server.override = async () =>
    new Response(JSON.stringify({}), {
      status: 429,
      headers: { 'Content-Type': 'application/json', 'Retry-After': '10' },
    });
  await f.store.open({ kind: 'conversation', id: ID });
  const calls = f.fetcher.mock.calls.length;
  await f.store.recover();
  await f.store.save(5, '');
  expect(f.fetcher.mock.calls.length).toBe(calls);
});

it('explicit GET recovers the safe target after source restoration, never POST replay', async () => {
  const f = await setup();
  let denied = true,
    posts = 0;
  f.server.override = async (_, i) => {
    if (i?.method === 'POST') posts++;
    return denied ? error(503, 'SOURCE_UNAVAILABLE') : json(view(rating()));
  };
  await f.store.open({ kind: 'conversation', id: ID });
  expect(f.store.getState().value).toBeNull();
  denied = false;
  await f.store.recover();
  expect(f.store.getState()).toMatchObject({
    phase: 'ready',
    notice: 'current',
  });
  denied = true;
  await f.store.save(4, 'Helpful.');
  denied = false;
  await f.store.recover();
  expect(posts).toBe(1);
  expect(f.store.getState().value?.rating?.score).toBe(4);
});

it('a dispatched rating save cannot restore old feedback after actual account and proof rotation', async () => {
  const f = await setup();
  let resolve!: (response: Response) => void;
  let started!: () => void;
  const dispatched = new Promise<void>((done) => {
    started = done;
  });
  let posts = 0;
  f.server.override = async (path, init) => {
    if (path === '/api/v1/me')
      return json({
        session_kind: f.server.kind,
        principal_id: f.server.principal,
      });
    if (init?.method === 'POST') {
      posts++;
      started();
      return new Promise<Response>((done) => {
        resolve = done;
      });
    }
    return json(view());
  };
  await f.store.open({ kind: 'conversation', id: ID });
  const pending = f.store.save(4, 'Old account feedback');
  await dispatched;
  expect(f.store.getState().phase).toBe('sending');
  f.server.kind = 'customer';
  f.server.principal = OTHER;
  f.server.proof = 'q'.repeat(43);
  await f.auth.recover();
  resolve(json(view({ ...rating(), comment: 'Old account feedback' })));
  await pending;
  expect(f.store.getState()).toMatchObject({
    phase: 'idle',
    value: null,
    notice: null,
  });
  await f.store.save(5, 'Do not replay');
  expect(posts).toBe(1);
});
