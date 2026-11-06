import { afterEach, expect, it, vi } from 'vitest';
import { CitationStore } from '../../src/features/chat/citation-store';
import { fixture, ID, OTHER, json, error } from './fixture';

const owned: (() => void)[] = [];
afterEach(() => {
  owned.splice(0).forEach((dispose) => dispose());
  vi.useRealTimers();
});
const receipt = (messageId = OTHER) => ({
  message_id: messageId,
  status: 'historical_publication',
  issued_at: null,
  citations: [],
});
async function setup() {
  const f = fixture();
  await f.auth.start();
  const store = new CitationStore(f.auth, ID, OTHER);
  const off = store.connect();
  owned.push(() => {
    off();
    f.dispose();
  });
  return { ...f, citations: store, off };
}
it('a current source-authorization outage purges retained data and all active citation receipts without changing the session', async () => {
  const f = await setup();
  f.server.override = async () => json(receipt());
  await f.citations.load();
  const owner = f.auth.capture();
  f.auth.keep('/conversations/' + ID, { body: 'private cached report' }, owner);
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await expect(
    f.auth.request({
      method: 'GET',
      path: '/conversations/' + ID,
      boundary: owner,
      isRelevant: () => true,
      successStatus: 200,
      decode: (v) => v,
    }),
  ).rejects.toMatchObject({ code: 'SOURCE_UNAVAILABLE' });
  expect(f.citations.getState()).toMatchObject({ open: false, receipt: null });
  expect(f.auth.retained('/conversations/' + ID, owner)).toBeUndefined();
  expect(f.auth.isCurrent(owner)).toBe(true);
});
it('ordinary model unavailability does not masquerade as lost source authority', async () => {
  const f = await setup();
  f.server.override = async () => json(receipt());
  await f.citations.load();
  const owner = f.auth.capture();
  f.server.override = async () => error(503, 'AGENT_UNAVAILABLE');
  await expect(
    f.auth.request({
      method: 'GET',
      path: '/conversations/' + ID,
      boundary: owner,
      isRelevant: () => true,
      successStatus: 200,
      decode: (v) => v,
    }),
  ).rejects.toMatchObject({ code: 'AGENT_UNAVAILABLE' });
  expect(f.citations.getState().receipt).not.toBeNull();
});
it('is lazy, single-flight, authorized GET/no-store and clears on close; no model or business POST', async () => {
  const f = await setup();
  let resolve!: (value: Response) => void;
  f.server.override = () =>
    new Promise<Response>((r) => {
      resolve = r;
    });
  expect(f.fetcher).toHaveBeenCalledTimes(1);
  const load = f.citations.load();
  await f.citations.load();
  expect(f.fetcher).toHaveBeenCalledTimes(2);
  const [path, options] = f.fetcher.mock.calls[1]!;
  expect(path).toBe(`/api/v1/conversations/${ID}/messages/${OTHER}/citations`);
  expect(options).toMatchObject({
    method: 'GET',
    cache: 'no-store',
    credentials: 'same-origin',
  });
  resolve(json(receipt()));
  await load;
  expect(f.citations.getState().phase).toBe('ready');
  f.citations.close();
  expect(f.citations.getState()).toMatchObject({ open: false, receipt: null });
  expect(f.server.posts).toBe(0);
});
it.each(['close', 'unmount', 'epoch', 'proof'] as const)(
  'discards an abort-ignoring response after %s',
  async (change) => {
    const f = await setup();
    let resolve!: (value: Response) => void;
    f.server.override = () =>
      new Promise<Response>((r) => {
        resolve = r;
      });
    const load = f.citations.load();
    if (change === 'close') f.citations.close();
    if (change === 'unmount') f.off();
    if (change === 'epoch') {
      f.server.override = async () => error(401, 'AUTH_REQUIRED');
      await f.auth.recheck();
    }
    if (change === 'proof') f.server.proof = 'q'.repeat(43);
    resolve(json(receipt()));
    await load;
    expect(f.citations.getState().receipt).toBeNull();
    expect(f.citations.getState().open).toBe(false);
  },
);
it('an old resource cannot populate a different message store', async () => {
  const f = await setup();
  let resolve!: (value: Response) => void;
  f.server.override = () =>
    new Promise<Response>((r) => {
      resolve = r;
    });
  const old = f.citations.load();
  f.off();
  const next = new CitationStore(f.auth, OTHER, ID);
  const off = next.connect();
  owned.push(off);
  resolve(json(receipt()));
  await old;
  expect(next.getState()).toMatchObject({ phase: 'idle', receipt: null });
});
it.each([403, 404])(
  'purges and rechecks session after revoked read %s without replay',
  async (status) => {
    const f = await setup();
    const before = f.auth.getState().epoch;
    f.server.override = async (path) =>
      path.endsWith('/me')
        ? json({ session_kind: 'anonymous', principal_id: null })
        : error(status, 'RESOURCE_NOT_FOUND');
    await f.citations.load();
    expect(f.citations.getState()).toMatchObject({
      open: false,
      receipt: null,
    });
    expect(f.auth.getState().epoch).toBeGreaterThan(before);
    expect(
      f.fetcher.mock.calls.filter(([path]) =>
        String(path).endsWith('/citations'),
      ),
    ).toHaveLength(1);
  },
);
it('503 remains a local citation error and a retry is explicit', async () => {
  const f = await setup();
  f.server.override = async () => error(503, 'AGENT_UNAVAILABLE');
  const before = f.auth.getState().epoch;
  await f.citations.load();
  expect(f.citations.getState()).toMatchObject({
    phase: 'error',
    receipt: null,
  });
  expect(f.auth.getState().epoch).toBe(before);
  expect(f.fetcher).toHaveBeenCalledTimes(2);
  f.server.override = async () => json(receipt());
  await f.citations.load();
  expect(f.citations.getState().phase).toBe('ready');
});
it('rejects a successful response for another message', async () => {
  const f = await setup();
  f.server.override = async () => json(receipt(ID));
  await f.citations.load();
  expect(f.citations.getState()).toMatchObject({
    phase: 'error',
    receipt: null,
    error: { kind: 'invalid-response' },
  });
});
it('honors Retry-After across close/remount without timer-driven requests', async () => {
  vi.useFakeTimers();
  const f = await setup();
  f.server.override = async () => {
    const response = error(429, 'RATE_LIMITED');
    response.headers.set('Retry-After', '20');
    return response;
  };
  await f.citations.load();
  f.off();
  const next = new CitationStore(f.auth, ID, OTHER);
  const off = next.connect();
  owned.push(off);
  await next.load();
  expect(f.fetcher).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(20001);
  expect(f.fetcher).toHaveBeenCalledTimes(2);
  f.server.override = async () => json(receipt());
  await next.load();
  expect(next.getState().phase).toBe('ready');
});
