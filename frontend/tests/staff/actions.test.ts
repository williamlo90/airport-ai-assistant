import { afterEach, expect, it, vi } from 'vitest';
import {
  makeStaffFixture,
  caseId,
  MEMBER,
  denied,
} from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
import { StaffActionStore } from '../../src/features/staff/action-store';
const clean: (() => void)[] = [];
async function setup(supervisor = false, n = 1) {
  const f = makeStaffFixture();
  if (supervisor) f.server.roles = ['supervisor'];
  await f.ready();
  const q = new StaffQueueStore(f.staff, 'test'),
    off = q.connect();
  await q.ensureQueue();
  await q.openCase(caseId(n));
  const a = new StaffActionStore(q, caseId(n)),
    offa = a.connect();
  clean.push(() => {
    offa();
    a.dispose();
    off();
    q.dispose();
    f.dispose();
  });
  return { ...f, q, a };
}
afterEach(() => {
  clean.splice(0).forEach((f) => f());
  vi.restoreAllMocks();
});
it('self-claims exact server membership once, accepts new case and refreshes current capabilities', async () => {
  const f = await setup();
  let body: unknown;
  f.server.custom = (path, init) => {
    if (init?.method === 'PATCH') {
      expect(path).toBe('/api/v1/staff/cases/' + caseId(1) + '/assignment');
      body = JSON.parse(String(init.body));
      expect(new Headers(init.headers).has('Idempotency-Key')).toBe(false);
    }
    return null;
  };
  await Promise.all([f.a.claim(), f.a.claim()]);
  expect(body).toEqual({ expected_version: 1, assignee_id: MEMBER });
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
  expect(f.q.getState().detail).toMatchObject({
    version: 2,
    status: 'in_progress',
    assigned_membership_id: MEMBER,
  });
  expect(f.q.getState().capabilities?.case_version).toBe(2);
  expect(f.a.getState().notice).toBe('saved');
  expect(f.q.getState().page?.items.some((v) => v.id === caseId(1))).toBe(
    false,
  );
});
it('supervisor uses eligible target and confirmed assignment, keeps preassigned new status', async () => {
  const f = await setup(true);
  await f.a.directory();
  f.a.choose(MEMBER);
  f.a.requestAssignment();
  expect(f.server.requests.some((v) => v.method === 'PATCH')).toBe(false);
  await f.a.confirmAssignment();
  expect(f.q.getState().detail).toMatchObject({
    assigned_membership_id: MEMBER,
    status: 'new',
    version: 2,
  });
  expect(f.a.getState().notice).toBe('saved');
});
it('invalidates confirmation when case version changes and does not use an arbitrary assignee', async () => {
  const f = await setup(true);
  await f.a.directory();
  f.a.choose('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  f.a.requestAssignment();
  expect(f.a.getState().confirmation).toBe(false);
  f.a.choose(MEMBER);
  f.a.requestAssignment();
  await f.q.openCase(caseId(2));
  await f.a.confirmAssignment();
  expect(f.server.requests.some((v) => v.method === 'PATCH')).toBe(false);
});
it('status requires explicit permitted value and next action, sends no closure or invented fields', async () => {
  const f = await setup(false, 4);
  f.a.edit({ status: 'awaiting_customer', nextAction: '   ' });
  await f.a.status();
  expect(f.server.requests.some((v) => v.method === 'PATCH')).toBe(false);
  let payload: unknown;
  f.server.custom = (_, init) => {
    if (init?.method === 'PATCH') payload = JSON.parse(String(init.body));
    return null;
  };
  f.a.edit({ nextAction: 'Please provide your booking confirmation.' });
  await f.a.status();
  expect(payload).toEqual({
    expected_version: 2,
    status: 'awaiting_customer',
    next_action: 'Please provide your booking confirmation.',
  });
  expect(f.q.getState().detail?.status).toBe('awaiting_customer');
});
it('lost acknowledgement reconciles GET without resend or inferred own success', async () => {
  const f = await setup(false, 4);
  f.a.edit({ status: 'awaiting_customer', nextAction: 'Please confirm.' });
  f.server.custom = (_, init) =>
    init?.method === 'PATCH'
      ? Promise.reject(new TypeError('connection lost'))
      : null;
  await f.a.status();
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    notice: 'uncertain',
    nextAction: 'Please confirm.',
  });
  expect(f.q.getState().detail?.version).toBe(2);
  await f.a.status();
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
});
it('409 retains authorized draft after fresh read but requires explicit review before another send', async () => {
  const f = await setup(false, 4);
  f.a.edit({
    status: 'awaiting_external',
    nextAction: 'Waiting for parking support.',
  });
  f.server.custom = (_, init) =>
    init?.method === 'PATCH' ? Promise.resolve(denied(409)) : null;
  await f.a.status();
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    nextAction: 'Waiting for parking support.',
  });
  await f.a.status();
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
  f.a.review();
  f.server.custom = null;
  await f.a.status();
  expect(f.q.getState().detail?.status).toBe('awaiting_external');
});
it('403 quarantines draft through /me recheck and restores only after current scoped GET+caps', async () => {
  const f = await setup(false, 4);
  f.a.edit({
    status: 'awaiting_external',
    nextAction: 'Private draft next action.',
  });
  f.server.custom = (_, init) =>
    init?.method === 'PATCH' ? Promise.resolve(denied(403)) : null;
  await f.a.status();
  expect(f.a.getState().nextAction).toBe('');
  expect(f.q.getState().detail).toBeNull();
  await f.q.ensureQueue();
  await f.q.openCase(caseId(4));
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    nextAction: 'Private draft next action.',
  });
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
});
it('403 followed by revoked read purges private draft, detail and row', async () => {
  const f = await setup(false, 4);
  f.a.edit({ status: 'awaiting_external', nextAction: 'Private text.' });
  f.server.custom = (path, init) =>
    init?.method === 'PATCH'
      ? Promise.resolve(denied(403))
      : path.includes('/staff/cases/')
        ? Promise.resolve(denied())
        : null;
  await f.a.status();
  await f.q.ensureQueue();
  await f.q.openCase(caseId(4));
  expect(f.a.getState().nextAction).toBe('');
  expect(f.q.getState().detail).toBeNull();
});
it('429 deadline survives action store remount without automatic requests', async () => {
  const f = await setup();
  f.server.custom = (_, init) =>
    init?.method === 'PATCH'
      ? Promise.resolve(
          new Response('{}', {
            status: 429,
            headers: {
              'Content-Type': 'application/json',
              'Retry-After': '60',
            },
          }),
        )
      : null;
  await f.a.claim();
  const a2 = new StaffActionStore(f.q, caseId(1)),
    off = a2.connect();
  clean.push(() => {
    off();
    a2.dispose();
  });
  expect(a2.getState().retryAt).toBeGreaterThan(Date.now());
  await a2.claim();
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
});
it('includes the exact active conversation version on status without accepting or sending a chat', async () => {
  const f = await setup(false, 4);
  const cap = structuredClone(f.q.getState().capabilities!);
  cap.active_conversation = { id: caseId(60), version: 7, mode: 'human' };
  let body: unknown;
  f.server.custom = (path, init) => {
    if (path.includes('/capabilities')) return Promise.resolve(f.json(cap));
    if (init?.method === 'PATCH') {
      body = JSON.parse(String(init.body));
      cap.case_version++;
      cap.active_conversation!.version++;
    }
    return null;
  };
  await f.q.openCase(caseId(4), true);
  f.a.edit({
    status: 'awaiting_customer',
    nextAction: 'Please provide the reference.',
  });
  await f.a.status();
  expect(body).toEqual({
    expected_version: 2,
    expected_conversation_version: 7,
    status: 'awaiting_customer',
    next_action: 'Please provide the reference.',
  });
  expect(f.q.getState().capabilities?.active_conversation?.version).toBe(8);
  expect(
    f.server.requests.some(
      (v) => v.path.includes('/accept') || v.path.includes('/send'),
    ),
  ).toBe(false);
});
it('directory uses server pages and cursor, permits nullable labels and rejects malformed page echoes', async () => {
  const f = await setup(true);
  const cap = f.q.getState().capabilities!;
  f.server.custom = (path) => {
    if (!path.includes('/eligible-assignees')) return null;
    const next = path.includes('cursor=');
    return Promise.resolve(
      f.json({
        case_id: cap.case_id,
        case_version: cap.case_version,
        membership_id: cap.membership_id,
        membership_version: cap.membership_version,
        items: next
          ? [{ membership_id: caseId(60), display_name: null }]
          : Array.from({ length: 25 }, (_, i) => ({
              membership_id: caseId(i + 1),
              display_name: null,
            })),
        has_more: !next,
        next_cursor: next ? null : 'next_page',
      }),
    );
  };
  await f.a.directory();
  expect(f.a.getState().directory?.items).toHaveLength(25);
  await f.a.directory('next');
  expect(f.a.getState().directoryPage).toBe(2);
  expect(f.server.requests.at(-1)?.path).toContain('cursor=next_page');
  f.server.custom = (path) =>
    path.includes('/eligible-assignees') ? Promise.resolve(f.json({})) : null;
  await f.a.directory();
  expect(f.a.getState().directory).toBeNull();
  expect(f.a.getState().target).toBeNull();
});
it('an already-sent response arriving after case change cannot affect replacement case or restore a command', async () => {
  const f = await setup();
  let reject!: (reason: unknown) => void;
  f.server.custom = (_, init) =>
    init?.method === 'PATCH'
      ? new Promise((_, fail) => {
          reject = fail;
        })
      : null;
  const pending = f.a.claim();
  await f.q.openCase(caseId(2));
  reject(new TypeError('late response'));
  await pending;
  expect(f.q.getState().detail?.id).toBe(caseId(2));
  expect(f.a.getState().notice).toBeNull();
  expect(f.server.requests.filter((v) => v.method === 'PATCH')).toHaveLength(1);
});
