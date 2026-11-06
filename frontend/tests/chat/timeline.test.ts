import { afterEach, expect, it, vi } from 'vitest';
import { fixture, ID, OTHER, json, error } from './fixture';
import { ConversationTimeline } from '../../src/features/chat/timeline';
import {
  decodeEvents,
  type ConversationEvent,
} from '../../src/features/chat/event-contracts';
const clean: (() => void)[] = [];
const event = (
  n: number,
  author: 'customer' | 'staff' | 'ai' = 'customer',
): ConversationEvent =>
  ({
    id: '90000000-0000-4000-8000-' + String(n).padStart(12, '0'),
    sequence: n,
    event_type:
      author === 'customer'
        ? 'message_received'
        : author === 'staff'
          ? 'staff_message'
          : 'ai_message',
    created_at: '2026-11-04T12:00:00Z',
    payload: {
      message_id: '80000000-0000-4000-8000-' + String(n).padStart(12, '0'),
      body: 'Original ' + n,
      language: n % 2 ? 'en' : 'id',
      author_type: author,
    },
  }) as ConversationEvent;
async function setup(count = 0) {
  const f = fixture();
  await f.auth.start();
  f.server.events = Array.from({ length: count }, (_, i) => event(i + 1));
  const timeline = new ConversationTimeline(f.auth, () => {});
  clean.push(() => {
    timeline.dispose();
    f.dispose();
  });
  return { ...f, timeline };
}
const tick = async () => {
  for (let i = 0; i < 15; i++) await Promise.resolve();
};
it('source outage erases the cached prefix and stops polling until a full authorized restart', async () => {
  vi.useFakeTimers();
  const f = await setup(1);
  f.timeline.start(ID, 1, 0);
  await tick();
  expect(f.timeline.getState().items).toHaveLength(1);
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await f.timeline.refresh();
  expect(f.timeline.getState()).toMatchObject({
    phase: 'error',
    items: [],
    snapshot: null,
  });
  const calls = f.fetcher.mock.calls.length;
  await vi.advanceTimersByTimeAsync(60000);
  expect(f.fetcher.mock.calls).toHaveLength(calls);
  f.server.override = null;
  f.timeline.start(ID, 1, 0);
  await tick();
  expect(String(f.fetcher.mock.calls.at(-1)![0])).toBe(
    '/api/v1/conversations/' + ID + '/events?limit=50',
  );
  expect(f.timeline.getState().items).toHaveLength(1);
});
afterEach(() => {
  clean.splice(0).forEach((f) => f());
  vi.useRealTimers();
});
it('pages ordered stable events through50+1 then empty incremental polls without duplicates', async () => {
  vi.useFakeTimers();
  const f = await setup(51);
  f.timeline.start(ID, 1, 0);
  await tick();
  expect(f.timeline.getState().items).toHaveLength(50);
  expect(f.timeline.canSend()).toBe(false);
  await vi.advanceTimersByTimeAsync(500);
  expect(f.timeline.getState().items).toHaveLength(51);
  expect(f.timeline.canSend()).toBe(true);
  await vi.advanceTimersByTimeAsync(10000);
  expect(f.timeline.getState().items).toHaveLength(51);
  expect(
    f.fetcher.mock.calls
      .filter(([p]) => String(p).includes('/events?'))
      .map(([p]) => String(p)),
  ).toEqual([
    '/api/v1/conversations/' + ID + '/events?limit=50',
    '/api/v1/conversations/' + ID + '/events?limit=50&after=c50',
    '/api/v1/conversations/' + ID + '/events?limit=50&after=c51',
  ]);
});
it('refresh/remount restarts fromzero and restores durable content under fresh permission', async () => {
  const f = await setup(2);
  f.timeline.start(ID, 1, 0);
  await tick();
  expect(f.timeline.getState().items).toHaveLength(2);
  f.timeline.reset();
  f.timeline.start(ID, 1, 0);
  await tick();
  expect(f.timeline.getState().items.map((e) => e.id)).toEqual([
    event(1).id,
    event(2).id,
  ]);
});
it('ended drains backlog then stops automaticpolling and rejectscomposer', async () => {
  vi.useFakeTimers();
  const f = await setup(51);
  f.server.mode = 'ended';
  f.timeline.start(ID, 1, 0);
  await tick();
  await vi.advanceTimersByTimeAsync(500);
  const calls = f.fetcher.mock.calls.length;
  await vi.advanceTimersByTimeAsync(60000);
  expect(f.fetcher.mock.calls).toHaveLength(calls);
  expect(f.timeline.canSend()).toBe(false);
});
it('pause aborts late reads andresumecheckspermission before exposingmemoryagain', async () => {
  const f = await setup(1);
  let release!: (r: Response) => void;
  const delayed = new Promise<Response>((r) => (release = r));
  f.server.override = async () => delayed;
  f.timeline.start(ID, 1, 0);
  f.timeline.pause();
  release(
    json({
      items: [event(1)],
      next_cursor: 'c1',
      has_more: false,
      mode: 'ai',
      generation_epoch: 0,
      version: 1,
    }),
  );
  await tick();
  expect(f.timeline.getState().phase).toBe('paused');
  expect(f.timeline.getState().items).toHaveLength(0);
  f.server.override = null;
  f.timeline.resume();
  await tick();
  expect(f.timeline.getState().items).toHaveLength(1);
});
it('authreset clears history and oldresponses cannotcross conversation or proof', async () => {
  const f = await setup(1);
  await f.store.open(ID);
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await tick();
  expect(f.store.timeline.getState().items).toHaveLength(1);
  await f.auth.recheck();
  expect(f.store.timeline.getState().items).toHaveLength(0);
});
it('429 waits exact deadline withoutrequeststorm andinvalidcursor waits explicit restart', async () => {
  vi.useFakeTimers();
  const f = await setup();
  f.server.override = async () =>
    new Response('{}', {
      status: 429,
      headers: { 'Content-Type': 'application/json', 'Retry-After': '60' },
    });
  f.timeline.start(ID, 1, 0);
  await tick();
  const count = f.fetcher.mock.calls.length;
  await f.timeline.refresh();
  await vi.advanceTimersByTimeAsync(59999);
  expect(f.fetcher.mock.calls).toHaveLength(count);
  f.server.override = async () => error(400, 'BAD_REQUEST');
  await vi.advanceTimersByTimeAsync(1);
  expect(f.timeline.getState().phase).toBe('error');
  const after = f.fetcher.mock.calls.length;
  await vi.advanceTimersByTimeAsync(60000);
  expect(f.fetcher.mock.calls).toHaveLength(after);
  f.server.override = null;
  await f.timeline.refresh(true);
  expect(f.timeline.getState().phase).toBe('ready');
});
it('transienterrors retry onlythree times and deniedreads purge privatehistory', async () => {
  vi.useFakeTimers();
  const f = await setup(1);
  f.timeline.start(ID, 1, 0);
  await tick();
  f.server.override = async () => error(503, 'UNAVAILABLE');
  await f.timeline.refresh();
  await vi.advanceTimersByTimeAsync(5000 + 10000 + 20000 + 60000);
  expect(
    f.fetcher.mock.calls.filter(([p]) => String(p).includes('/events?')),
  ).toHaveLength(5);
  expect(f.timeline.getState().snapshot).toBeNull();
  f.server.override = async () => error(404, 'RESOURCE_NOT_FOUND');
  await f.timeline.refresh();
  expect(f.timeline.getState().items).toHaveLength(0);
});
it('strict events reject author spoofing,sequencegap,modecontradiction,regressingepoch or extra fields', () => {
  const base = {
    items: [event(1)],
    next_cursor: 'c1',
    has_more: false,
    mode: 'ai',
    generation_epoch: 2,
    version: 4,
  };
  expect(decodeEvents(base, 0, 4, 2, null).items).toHaveLength(1);
  for (const bad of [
    { ...base, generation_epoch: 1 },
    { ...base, version: 3 },
    { ...base, items: [event(2)] },
    {
      ...base,
      items: [
        { ...event(1), payload: { ...event(1).payload, author_type: 'staff' } },
      ],
    },
    {
      ...base,
      items: [
        {
          id: OTHER,
          sequence: 1,
          created_at: '2026-11-04T12:00:00Z',
          event_type: 'chat_ended',
          payload: { case_id: ID, mode: 'human' },
        },
      ],
    },
    { ...base, secret: 'x' },
  ])
    expect(() => decodeEvents(bad, 0, 4, 2, null)).toThrow();
});
it('storedmessage + receipt renders one durablecustomerreport andneverincludes staff/AI text', async () => {
  const f = await setup();
  await f.store.open(ID);
  f.store.input({ body: 'Customer original', service: '' });
  await f.store.send();
  f.server.events.push(event(2, 'staff'), event(3, 'ai'));
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await tick();
  expect(f.store.receivedMessages()).toHaveLength(1);
  expect(f.store.plainReport().body).toBe('Customer original');
});
it('fresh pollversions recheck metadata language before accepting snapshot', async () => {
  const f = await setup();
  await f.store.open(ID);
  f.server.meta.version = 2;
  f.server.meta.generation_epoch = 1;
  f.server.meta.language = 'id';
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await tick();
  await vi.waitFor(() =>
    expect(f.store.timeline.getState().phase).toBe('ready'),
  );
  expect(f.store.getState().meta).toMatchObject({
    language: 'id',
    version: 2,
    generation_epoch: 1,
  });
  expect(f.store.timeline.getState().phase).toBe('ready');
});
it('current ended snapshot prevents a newmessage or language mutation', async () => {
  const f = await setup();
  await f.store.open(ID);
  f.server.mode = 'ended';
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await tick();
  f.store.input({ body: 'Must not send', service: '' });
  await f.store.send();
  await f.store.language('id');
  expect(f.server.posts).toBe(0);
  expect(f.server.patches).toBe(0);
});

it('a newly observed head closes the send gate before metadata publication can run', async () => {
  const f = await setup();
  await f.store.open(ID);
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await vi.waitFor(() =>
    expect(f.store.timeline.getState().phase).toBe('ready'),
  );
  let release!: (r: Response) => void;
  const held = new Promise<Response>((r) => (release = r));
  f.server.override = async (path) =>
    path.includes('/events?')
      ? json({
          items: [],
          next_cursor: 'c0',
          has_more: false,
          mode: 'ended',
          version: 2,
          generation_epoch: 1,
        })
      : held;
  const checking = f.store.timeline.refresh();
  await vi.waitFor(() =>
    expect(f.store.timeline.getState().phase).toBe('loading'),
  );
  expect(f.store.timeline.canSend()).toBe(false);
  f.store.input({
    body: 'Do not send during metadata validation',
    service: '',
  });
  await f.store.send();
  expect(f.server.posts).toBe(0);
  release(json({ ...f.server.meta, version: 2, generation_epoch: 1 }));
  await checking;
  expect(f.store.timeline.getState().snapshot?.mode).toBe('ended');
  expect(f.store.timeline.canSend()).toBe(false);
});
