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
async function setup(supervisor = false, n = 4) {
  const f = makeStaffFixture();
  if (supervisor) f.server.roles = ['supervisor'];
  await f.ready();
  const q = new StaffQueueStore(f.staff, 'qa-actions');
  const off = q.connect();
  await q.ensureQueue();
  await q.openCase(caseId(n));
  const a = new StaffActionStore(q, caseId(n));
  const offa = a.connect();
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
  clean.splice(0).forEach((dispose) => dispose());
  vi.restoreAllMocks();
});
const patches = (f: Awaited<ReturnType<typeof setup>>) =>
  f.server.requests.filter((v) => v.method === 'PATCH');

it('a confirmed PATCH followed by unavailable queue does not silently retry reads or claim fresh verification', async () => {
  const f = await setup();
  f.a.edit({
    status: 'awaiting_external',
    nextAction: 'Contact the parking team.',
  });
  let sent = false;
  f.server.custom = (path, init) => {
    if (init?.method === 'PATCH') sent = true;
    return sent && path.includes('/queues/')
      ? Promise.resolve(denied(503))
      : null;
  };
  const before = f.server.requests.length;
  await f.a.status();
  const reads = f.server.requests
    .slice(before)
    .filter((v) => v.path.includes('/queues/'));
  expect(patches(f)).toHaveLength(1);
  expect(reads).toHaveLength(1);
  expect(f.q.getState().detail).toBeNull();
  expect(f.a.getState().notice).not.toBe('saved');
});

it('proof rotation immediately before confirming assignment does not throw or send the stale intent', async () => {
  const f = await setup(true, 1);
  await f.a.directory();
  f.a.choose(MEMBER);
  f.a.requestAssignment();
  expect(f.a.getState().confirmation).toBe(true);
  f.server.proof = 'z'.repeat(43);
  await expect(f.a.confirmAssignment()).resolves.toBeUndefined();
  expect(patches(f)).toHaveLength(0);
  expect(f.a.getState().confirmation).toBe(false);
});

it('matching fresh GET values after a lost acknowledgement remain uncertain and require explicit review', async () => {
  const f = await setup();
  const id = caseId(4);
  const detailPath = '/api/v1/staff/cases/' + id + '?membership_id=' + MEMBER;
  const capPath =
    '/api/v1/staff/cases/' + id + '/capabilities?membership_id=' + MEMBER;
  const detail = await (await f.fetcher(detailPath)).json();
  const cap = await (await f.fetcher(capPath)).json();
  const text = 'Wait for the external operator.';
  const old = detail.version;
  detail.version += 1;
  detail.status = 'awaiting_external';
  detail.next_actor = 'external';
  detail.next_action = text;
  detail.history.push({
    event_type: 'status_changed',
    previous_version: old,
    resulting_version: detail.version,
    from_status: 'in_progress',
    to_status: detail.status,
    next_actor: detail.next_actor,
    next_action: text,
    close_reason: null,
    resolution: null,
    customer_message: null,
    created_at: detail.updated_at,
  });
  cap.case_version = detail.version;
  f.server.custom = (path, init) => {
    if (init?.method === 'PATCH')
      return Promise.reject(new TypeError('Synthetic lost acknowledgement'));
    if (path === detailPath) return Promise.resolve(f.json(detail));
    if (path === capPath) return Promise.resolve(f.json(cap));
    return null;
  };
  f.a.edit({ status: 'awaiting_external', nextAction: text });
  await f.a.status();
  expect(f.q.getState().detail).toMatchObject({
    version: old + 1,
    status: 'awaiting_external',
    next_action: text,
  });
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    notice: 'uncertain',
    nextAction: text,
  });
  await f.a.status();
  expect(patches(f)).toHaveLength(1);
});

it('a proof change during the 403 identity recheck destroys the quarantined draft', async () => {
  const f = await setup();
  f.a.edit({
    status: 'awaiting_external',
    nextAction: 'Private text from the old proof.',
  });
  let rotate = true;
  f.server.custom = (path, init) => {
    if (init?.method === 'PATCH') return Promise.resolve(denied(403));
    if (path === '/api/v1/me' && rotate) {
      rotate = false;
      f.server.proof = 'z'.repeat(43);
    }
    return null;
  };
  await f.a.status();
  await f.q.ensureQueue();
  await f.q.openCase(caseId(4));
  expect(f.q.getState().detail?.id).toBe(caseId(4));
  expect(f.a.getState().nextAction).toBe('');
  expect(
    f.auth.retained(
      '/staff/cases/' + caseId(4) + '?membership_id=' + MEMBER,
      f.auth.capture(),
    ),
  ).toBeUndefined();
  expect(patches(f)).toHaveLength(1);
});

it('a delayed eligible directory cannot repopulate choices after another case is selected', async () => {
  const f = await setup(true, 1);
  const path =
    '/api/v1/staff/cases/' +
    caseId(1) +
    '/eligible-assignees?membership_id=' +
    MEMBER +
    '&limit=25';
  const response = await f.fetcher(path);
  let release!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  f.server.custom = (p) => (p === path ? pending : null);
  const work = f.a.directory();
  await f.q.openCase(caseId(2));
  release(response);
  await work;
  expect(f.a.getState()).toMatchObject({
    directory: null,
    target: null,
    confirmation: false,
  });
  expect(patches(f)).toHaveLength(0);
});

it('a revoked assignee response clears selection and cannot replay the old confirmation after review', async () => {
  const f = await setup(true, 1);
  await f.a.directory();
  f.a.choose(MEMBER);
  f.a.requestAssignment();
  f.server.custom = (_, init) =>
    init?.method === 'PATCH'
      ? Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: 'CASE_ASSIGNEE_INVALID',
                message: 'Synthetic target revoked',
                request_id: MEMBER,
                retryable: false,
              },
            }),
            { status: 422, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      : null;
  await f.a.confirmAssignment();
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    directory: null,
    target: null,
    confirmation: false,
  });
  f.a.review();
  await f.a.confirmAssignment();
  expect(patches(f)).toHaveLength(1);
});

it('revoked queue read erases retained case input even if the same membership later regains access', async () => {
  const f = await setup();
  f.a.edit({
    status: 'awaiting_external',
    nextAction: 'Private draft before scope revocation.',
  });
  f.server.custom = (path) =>
    path.includes('/queues/') ? Promise.resolve(denied(404)) : null;
  await f.q.loadQueue(true);
  expect(f.q.getState().page).toBeNull();
  expect(f.a.getState().nextAction).toBe('');
  expect(
    f.auth.retained(
      '/staff/cases/' + caseId(4) + '?membership_id=' + MEMBER,
      f.auth.capture(),
    ),
  ).toBeUndefined();
  f.server.custom = null;
  await f.q.loadQueue(true);
  await f.q.openCase(caseId(4));
  expect(f.q.getState().detail?.id).toBe(caseId(4));
  expect(f.a.getState().nextAction).toBe('');
});

it('confirmed reassignment preserves an independent unsent status draft and requires review of its new basis', async () => {
  const f = await setup(true);
  const text = 'Unsent instructions for the next team.';
  f.a.edit({ status: 'awaiting_external', nextAction: text });
  await f.a.directory();
  const target = f.a
    .getState()
    .directory!.items.find((v) => v.membership_id !== MEMBER)!;
  f.a.choose(target.membership_id);
  f.a.requestAssignment();
  await f.a.confirmAssignment();
  expect(f.q.getState().detail?.assigned_membership_id).toBe(
    target.membership_id,
  );
  expect(f.a.getState()).toMatchObject({
    phase: 'review',
    status: 'awaiting_external',
    nextAction: text,
  });
  await f.a.status();
  expect(patches(f)).toHaveLength(1);
});
