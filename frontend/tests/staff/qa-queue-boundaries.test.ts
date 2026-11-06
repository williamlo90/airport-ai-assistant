import { afterEach, expect, it, vi } from 'vitest';
import {
  makeStaffFixture,
  caseId,
  MEMBER,
  STAFF,
} from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
import { decodeQueue } from '../../src/features/staff/queue-contracts';

const owned: (() => void)[] = [];
async function make() {
  const f = makeStaffFixture();
  await f.ready();
  const store = new StaffQueueStore(f.staff, 'qa');
  const off = store.connect();
  owned.push(() => {
    off();
    store.dispose();
    f.dispose();
  });
  return { ...f, store };
}
const deferred = () => {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
afterEach(() => {
  owned.splice(0).forEach((dispose) => dispose());
  vi.restoreAllMocks();
});

it.each(['version', 'actor', 'membership', 'queue'] as const)(
  'rejects a structurally valid capability pair with a contradictory %s basis',
  async (kind) => {
    const f = await make();
    await f.store.ensureQueue();
    const path =
      '/api/v1/staff/cases/' +
      caseId(1) +
      '/capabilities?membership_id=' +
      MEMBER;
    const value = await (await f.fetcher(path)).json();
    if (kind === 'version') value.case_version += 1;
    if (kind === 'actor') value.actor_principal_id = caseId(90);
    if (kind === 'membership') value.membership_id = caseId(91);
    if (kind === 'queue') value.queue.id = caseId(92);
    f.server.custom = (p) =>
      p === path ? Promise.resolve(f.json(value)) : null;
    await f.store.openCase(caseId(1));
    expect(f.store.getState()).toMatchObject({
      detailPhase: 'unavailable',
      detail: null,
      capabilities: null,
    });
    expect(f.store.getState().detailError?.kind).toBe('invalid-response');
  },
);

it('a valid delayed case response cannot replace a newer selected case even when fetch ignores abort', async () => {
  const f = await make();
  await f.store.ensureQueue();
  const path = '/api/v1/staff/cases/' + caseId(1) + '?membership_id=' + MEMBER;
  const response = await f.fetcher(path);
  const pending = deferred();
  f.server.custom = (p) => (p === path ? pending.promise : null);
  const first = f.store.openCase(caseId(1));
  await f.store.openCase(caseId(2));
  expect(f.store.getState().detail?.id).toBe(caseId(2));
  pending.resolve(response);
  await first;
  expect(f.store.getState().detail?.id).toBe(caseId(2));
  expect(f.store.getState().capabilities?.case_id).toBe(caseId(2));
});

it('queue cooldown survives feature disposal and filter changes without an automatic request at expiry', async () => {
  const now = Date.now();
  const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
  const f = await make();
  f.server.custom = (path) =>
    path.includes('/queues/')
      ? Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: 'RATE_LIMITED',
                message: 'Synthetic wait',
                request_id: STAFF,
                retryable: false,
              },
            }),
            {
              status: 429,
              headers: {
                'Content-Type': 'application/json',
                'Retry-After': '60',
              },
            },
          ),
        )
      : null;
  await f.store.ensureQueue();
  const deadline = f.store.getState().retryAt;
  expect(deadline).toBe(now + 60000);
  f.store.dispose();
  f.server.custom = null;
  const replacement = new StaffQueueStore(f.staff, 'qa');
  const off = replacement.connect();
  owned.push(() => {
    off();
    replacement.dispose();
  });
  replacement.configure({ view: 'mine' });
  const before = f.server.requests.length;
  await replacement.ensureQueue();
  expect(f.server.requests).toHaveLength(before);
  expect(replacement.getState().retryAt).toBe(deadline);
  clock.mockReturnValue(deadline + 1);
  expect(f.server.requests).toHaveLength(before);
  await replacement.ensureQueue();
  expect(replacement.getState().page?.view).toBe('mine');
  expect(f.server.requests).toHaveLength(before + 1);
});

it('an invalid next-page cursor never triggers a silent first-page fallback', async () => {
  const f = await make();
  await f.store.ensureQueue();
  f.server.custom = (path) =>
    path.includes('cursor=')
      ? Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: 'BAD_REQUEST',
                message: 'Synthetic expired cursor',
                request_id: STAFF,
                retryable: false,
              },
            }),
            { status: 400, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      : null;
  const before = f.server.requests.length;
  await f.store.next();
  expect(f.server.requests).toHaveLength(before + 1);
  expect(f.store.getState()).toMatchObject({
    phase: 'unavailable',
    page: null,
  });
  expect(f.store.getState().error?.status).toBe(400);
  await f.store.loadQueue(true);
  expect(f.server.requests.at(-1)?.path).not.toContain('cursor=');
  expect(f.store.getState()).toMatchObject({ phase: 'ready', pageNumber: 1 });
});

it('rejects duplicate rows, contradictory cursor metadata and reversed microsecond ordering', async () => {
  const f = await make();
  await f.store.ensureQueue();
  const state = f.store.getState();
  const member = f.staff.captureScope()!.membership;
  const duplicate = structuredClone(state.page!);
  duplicate.items[1] = duplicate.items[0]!;
  expect(() => decodeQueue(duplicate, member, state.filter)).toThrow();
  const incoherent = structuredClone(state.page!);
  incoherent.has_more = false;
  expect(() => decodeQueue(incoherent, member, state.filter)).toThrow();
  const reversed = structuredClone(state.page!);
  reversed.items[0]!.created_at = '2026-11-01T09:00:00.000002Z';
  reversed.items[1]!.created_at = '2026-11-01T09:00:00.000001Z';
  expect(() => decodeQueue(reversed, member, state.filter)).toThrow();
});
