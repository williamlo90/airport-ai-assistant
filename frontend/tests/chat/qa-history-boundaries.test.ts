import { afterEach, expect, it, vi } from 'vitest';
import { fixture, ID, OTHER, json, error } from './fixture';
import { ConversationTimeline } from '../../src/features/chat/timeline';
import {
  decodeEvents,
  type ConversationEvent,
} from '../../src/features/chat/event-contracts';

const clean: (() => void)[] = [];
const message = (
  sequence = 1,
  body = 'Customer original',
): ConversationEvent => ({
  id: '90000000-0000-4000-8000-' + String(sequence).padStart(12, '0'),
  sequence,
  event_type: 'message_received',
  created_at: '2026-11-04T12:00:00Z',
  payload: {
    message_id: '80000000-0000-4000-8000-' + String(sequence).padStart(12, '0'),
    body,
    language: 'id',
    author_type: 'customer',
  },
});
const page = (items: ConversationEvent[], version = 1, epoch = 0) => ({
  items,
  next_cursor: 'c' + (items.at(-1)?.sequence ?? 0),
  has_more: false,
  mode: 'ai',
  version,
  generation_epoch: epoch,
});
async function setup() {
  const f = fixture();
  await f.auth.start();
  const timeline = new ConversationTimeline(f.auth, () => {});
  clean.push(() => {
    timeline.dispose();
    f.dispose();
  });
  return { ...f, timeline };
}
const eventReads = (f: Awaited<ReturnType<typeof setup>>) =>
  f.fetcher.mock.calls.filter(([path]) => String(path).includes('/events?'));
afterEach(() => {
  clean.splice(0).forEach((dispose) => dispose());
  vi.useRealTimers();
});

it('explicit reload recovers an expired cursor with exactly one first-page request', async () => {
  vi.useFakeTimers();
  const f = await setup();
  f.server.events = [message()];
  f.timeline.start(ID, 1, 0);
  await vi.waitFor(() => expect(f.timeline.getState().phase).toBe('ready'));
  f.server.override = async (path) =>
    path.includes('after=')
      ? error(400, 'BAD_REQUEST')
      : json(page([message()]));
  await f.timeline.refresh();
  expect(f.timeline.getState().phase).toBe('error');
  const count = eventReads(f).length;
  f.timeline.reload();
  await vi.waitFor(() => expect(f.timeline.getState().phase).toBe('ready'));
  expect(eventReads(f)).toHaveLength(count + 1);
  expect(String(eventReads(f).at(-1)![0])).not.toContain('after=');
  expect(f.timeline.getState().items).toHaveLength(1);
});

it('reload fences an old valid response even when its transport ignores abort', async () => {
  vi.useFakeTimers();
  const f = await setup();
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => {
    release = resolve;
  });
  f.server.override = async () => held;
  f.timeline.start(ID, 1, 0);
  f.server.override = null;
  f.server.events = [message(1, 'Fresh result')];
  f.timeline.reload();
  await vi.waitFor(() => expect(f.timeline.getState().phase).toBe('ready'));
  release(json(page([message(1, 'Old result')])));
  await vi.advanceTimersByTimeAsync(0);
  expect(f.timeline.getState().items).toEqual([message(1, 'Fresh result')]);
});

it('explicit reload cannot bypass Retry-After or start overlapping deadline polls', async () => {
  vi.useFakeTimers();
  const f = await setup();
  f.server.override = async () =>
    new Response('{}', {
      status: 429,
      headers: { 'Content-Type': 'application/json', 'Retry-After': '60' },
    });
  f.timeline.start(ID, 1, 0);
  await vi.waitFor(() => expect(f.timeline.getState().phase).toBe('error'));
  const count = eventReads(f).length;
  f.server.override = null;
  f.timeline.reload();
  f.timeline.reload();
  await vi.advanceTimersByTimeAsync(59000);
  expect(eventReads(f)).toHaveLength(count);
  await vi.advanceTimersByTimeAsync(1000);
  expect(eventReads(f)).toHaveLength(count + 1);
  expect(f.timeline.getState().phase).toBe('ready');
});

it('valid Unicode bodies use the backend code-point limit rather than UTF-16 units', () => {
  expect(
    decodeEvents(page([message(1, '🙂'.repeat(3000))]), 0, 1, 0, null).items,
  ).toHaveLength(1);
  expect(() =>
    decodeEvents(page([message(1, '🙂'.repeat(4001))]), 0, 1, 0, null),
  ).toThrow();
});

it('history report projection keeps event order distinct from ACK message sequence and excludes staff text', async () => {
  vi.useFakeTimers();
  const f = await setup();
  f.server.meta.version = 2;
  f.server.meta.generation_epoch = 1;
  f.server.meta.language = 'id';
  f.server.events = [
    {
      id: OTHER,
      sequence: 1,
      event_type: 'language_changed',
      created_at: '2026-11-04T12:00:00Z',
      payload: { language: 'id' },
    },
    message(2),
    {
      ...message(3, 'Staff private-to-report text'),
      event_type: 'staff_message',
      payload: {
        ...message(3).payload,
        author_type: 'staff',
        body: 'Staff private-to-report text',
      },
    } as ConversationEvent,
  ];
  await f.store.open(ID);
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await vi.waitFor(() =>
    expect(f.store.timeline.getState().phase).toBe('ready'),
  );
  expect(f.store.plainReport().body).toBe('Customer original');
  const saved = f.store.receivedMessages()[0]!;
  expect(saved).not.toHaveProperty('receipt');
  expect(saved).not.toHaveProperty('sequence');
  expect(saved).toHaveProperty('eventSequence', 2);
});

it('metadata ACL denial after a successful event page prevents publication and purges private input', async () => {
  vi.useFakeTimers();
  const f = await setup();
  await f.store.open(ID);
  f.store.input({ body: 'Unsent private text', service: '' });
  f.server.override = async (path) =>
    path.includes('/events?')
      ? json(page([message()], 2, 1))
      : error(403, 'ACTION_FORBIDDEN');
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await vi.waitFor(() => expect(f.store.getState().phase).toBe('unavailable'));
  expect(f.store.timeline.getState()).toMatchObject({
    phase: 'idle',
    id: null,
    snapshot: null,
    items: [],
  });
  expect(f.store.getState().error).toMatchObject({ status: 403 });
  expect(f.store.getState()).toMatchObject({
    phase: 'unavailable',
    meta: null,
    input: { body: '', service: '' },
  });
});

it('metadata/event snapshot mismatch cannot expose a page or enable sending', async () => {
  vi.useFakeTimers();
  const f = await setup();
  await f.store.open(ID);
  f.server.override = async (path) =>
    path.includes('/events?')
      ? json(page([message()], 2, 1))
      : json({ ...f.server.meta, version: 3, generation_epoch: 2 });
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await vi.waitFor(() =>
    expect(f.store.timeline.getState().phase).toBe('error'),
  );
  expect(f.store.timeline.getState().items).toEqual([]);
  expect(f.store.timeline.canSend()).toBe(false);
  expect(f.store.getState().meta?.version).toBe(1);
});

it('a write completing while hidden does not restart history polling', async () => {
  vi.useFakeTimers();
  const f = await setup();
  await f.store.open(ID);
  const off = f.store.observeHistory(ID);
  clean.push(off);
  await vi.waitFor(() =>
    expect(f.store.timeline.getState().phase).toBe('ready'),
  );
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => {
    release = resolve;
  });
  f.server.override = async (path) =>
    path.endsWith('/messages') ? held : json(f.server.meta);
  f.store.input({ body: 'Message sent before hiding', service: '' });
  const sending = f.store.send();
  f.store.pauseHistory();
  const count = eventReads(f).length;
  f.server.meta.last_sequence = 1;
  f.server.meta.version = 2;
  release(
    json({ message_id: OTHER, sequence: 1, processing_state: 'received' }, 201),
  );
  await sending;
  await vi.advanceTimersByTimeAsync(60000);
  expect(eventReads(f)).toHaveLength(count);
  expect(f.store.timeline.getState().phase).toBe('paused');
});
