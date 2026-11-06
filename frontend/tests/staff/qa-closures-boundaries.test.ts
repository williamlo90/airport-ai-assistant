import { afterEach, expect, it } from 'vitest';
import { makeStaffFixture, caseId, MEMBER } from '../../src/dev/staff-fixtures';
import { StaffQueueStore } from '../../src/features/staff/queue-store';
import { StaffActionStore } from '../../src/features/staff/action-store';
import { decodeMutation } from '../../src/features/staff/action-contracts';

const clean: (() => void)[] = [];
const resolution = 'The team confirmed the outcome.';
const next = 'No further action is required.';
async function setup(supervisor = false) {
  const f = makeStaffFixture();
  if (supervisor) f.server.roles = ['supervisor'];
  await f.ready();
  const q = new StaffQueueStore(f.staff, 'qa-closures');
  const off = q.connect();
  await q.ensureQueue();
  await q.openCase(caseId(4));
  const a = new StaffActionStore(q, caseId(4));
  const offa = a.connect();
  clean.push(() => {
    offa();
    a.dispose();
    off();
    q.dispose();
    f.dispose();
  });
  a.edit({ closeReason: 'resolved', resolution, closeNext: next });
  return { ...f, q, a };
}
afterEach(() => clean.splice(0).forEach((dispose) => dispose()));

async function closedPair(
  f: Awaited<ReturnType<typeof setup>>,
  reason = 'resolved',
) {
  const path = '/api/v1/staff/cases/' + caseId(4) + '?membership_id=' + MEMBER;
  const capPath =
    '/api/v1/staff/cases/' +
    caseId(4) +
    '/capabilities?membership_id=' +
    MEMBER;
  const detail = await (await f.fetcher(path)).json();
  const cap = await (await f.fetcher(capPath)).json();
  const previous = detail.version;
  const from = detail.status;
  Object.assign(detail, {
    version: previous + 1,
    status: 'closed',
    next_actor: 'none',
    next_action: next,
    close_reason: reason,
    resolution,
    closed_at: '2026-11-04T12:00:00Z',
  });
  detail.history.push({
    event_type: 'status_changed',
    previous_version: previous,
    resulting_version: detail.version,
    from_status: from,
    to_status: 'closed',
    next_actor: 'none',
    next_action: next,
    close_reason: reason,
    resolution,
    customer_message: null,
    created_at: detail.closed_at,
  });
  Object.assign(cap, {
    case_version: detail.version,
    allowed_actions: [],
    allowed_statuses: [],
    allowed_close_reasons: [],
    self_claim_membership_id: null,
    active_conversation: null,
  });
  return { path, capPath, detail, cap };
}

it.each(['assignment', 'status'] as const)(
  '%s ACK preserves a separate closure draft through a same-proof auth recheck',
  async (operation) => {
    const f = await setup(true);
    if (operation === 'assignment') {
      await f.a.directory();
      const target = f.a
        .getState()
        .directory!.items.find((v) => v.membership_id !== MEMBER)!;
      f.a.choose(target.membership_id);
      f.a.requestAssignment();
      await f.a.confirmAssignment();
    } else {
      f.a.edit({
        status: 'awaiting_external',
        nextAction: 'Check with the operator.',
      });
      await f.a.status();
    }
    await f.auth.recheck();
    await f.q.ensureQueue();
    await f.q.openCase(caseId(4));
    expect(f.a.getState()).toMatchObject({
      phase: 'review',
      closeReason: 'resolved',
      resolution,
      closeNext: next,
      candidate: null,
      closeConfirmation: false,
    });
  },
);

it('an old duplicate lookup cannot repopulate a candidate after the reference changes', async () => {
  const f = await setup();
  const ref = 'CASE-' + caseId(2).replaceAll('-', '');
  const path =
    '/api/v1/staff/cases/' + caseId(4) + '/duplicate-candidates/search';
  const response = await f.fetcher(path, {
    method: 'POST',
    body: JSON.stringify({
      membership_id: MEMBER,
      expected_version: 2,
      public_reference: ref,
    }),
  });
  let release!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  f.server.custom = (p) => (p === path ? pending : null);
  f.a.edit({ closeReason: 'duplicate', reference: ref });
  const old = f.a.findDuplicate();
  f.a.edit({ reference: 'CASE-' + caseId(3).replaceAll('-', '') });
  release(response);
  await old;
  expect(f.a.getState()).toMatchObject({
    lookup: 'idle',
    candidate: null,
    closeConfirmation: false,
  });
  expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
});

it('proof rotation just before confirming close never throws or sends a stale close', async () => {
  const f = await setup();
  f.a.requestClose();
  expect(f.a.getState().closeConfirmation).toBe(true);
  f.server.proof = 'z'.repeat(43);
  await expect(f.a.confirmClose()).resolves.toBeUndefined();
  expect(f.server.requests.some((r) => r.method === 'PATCH')).toBe(false);
  expect(f.a.getState().closeConfirmation).toBe(false);
});

it('a matching closed GET after a lost acknowledgement does not confirm this browsers close command', async () => {
  const f = await setup();
  const pair = await closedPair(f);
  f.server.custom = (path, init) => {
    if (init?.method === 'PATCH')
      return Promise.reject(
        new TypeError('Synthetic lost close acknowledgement'),
      );
    if (path === pair.path) return Promise.resolve(f.json(pair.detail));
    if (path === pair.capPath) return Promise.resolve(f.json(pair.cap));
    return null;
  };
  f.a.requestClose();
  await f.a.confirmClose();
  expect(f.q.getState().detail?.status).toBe('closed');
  expect(f.a.getState().notice).toBe('uncertain');
  expect(f.a.getState().resolution).toBe('');
  f.a.requestClose();
  await f.a.confirmClose();
  expect(f.server.requests.filter((r) => r.method === 'PATCH')).toHaveLength(1);
});

it('a duplicate ACK cannot name a different visible related reference while concealed references remain valid', async () => {
  const f = await setup();
  const before = f.q.getState().detail!;
  const pair = await closedPair(f, 'duplicate');
  const candidate = {
    case_id: caseId(2),
    public_reference: 'CASE-' + caseId(2).replaceAll('-', ''),
  };
  const command = {
    kind: 'close' as const,
    reason: 'duplicate' as const,
    resolution,
    nextAction: next,
    candidate,
    evidence: '',
  };
  pair.detail.related_case_ref = 'CASE-' + caseId(3).replaceAll('-', '');
  expect(() => decodeMutation(pair.detail, before, command, false)).toThrow();
  pair.detail.related_case_ref = null;
  expect(decodeMutation(pair.detail, before, command, false).status).toBe(
    'closed',
  );
});

it('a newly mounted action store seeing a closed case erases a draft quarantined before auth checking', async () => {
  const f = await setup();
  const pair = await closedPair(f);
  const checking = f.auth.recheck();
  f.a.dispose();
  await checking;
  f.server.custom = (path) =>
    path === pair.path
      ? Promise.resolve(f.json(pair.detail))
      : path === pair.capPath
        ? Promise.resolve(f.json(pair.cap))
        : null;
  const replacement = new StaffActionStore(f.q, caseId(4));
  const off = replacement.connect();
  clean.push(() => {
    off();
    replacement.dispose();
  });
  await f.q.ensureQueue();
  await f.q.openCase(caseId(4));
  expect(f.q.getState().detail?.status).toBe('closed');
  expect(replacement.getState().resolution).toBe('');
  expect(
    f.auth.retained(
      '/staff/cases/' + caseId(4) + '?membership_id=' + MEMBER,
      f.auth.capture(),
    ),
  ).toBeUndefined();
});
