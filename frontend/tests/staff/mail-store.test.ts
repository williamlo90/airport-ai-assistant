import { afterEach, expect, it, vi } from 'vitest';
import { mailFixture, caseId, reply } from './mail-fixture';
import { denied } from '../../src/dev/staff-fixtures';
import { StaffMailStore } from '../../src/features/staff/mail-store';
const owned: Awaited<ReturnType<typeof mailFixture>>[] = [];
async function make() {
  const f = await mailFixture();
  owned.push(f);
  return f;
}
afterEach(() => {
  owned.splice(0).forEach((f) => f.dispose());
  vi.useRealTimers();
});
it('mount is lazy; explicit reads have no email query/overrides and revalidate selected capabilities before display', async () => {
  const f = await make();
  const before = f.server.requests.length;
  expect(f.mail.getState().deliveries.phase).toBe('idle');
  await f.mail.load();
  expect(f.mail.getState().deliveries.phase).toBe('ready');
  expect(f.mail.getState().replies.data?.[0]?.body).toBe(reply().body);
  const calls = f.server.requests.slice(before);
  expect(
    calls.filter((r) => r.path.endsWith('/email-deliveries')),
  ).toHaveLength(1);
  expect(calls.filter((r) => r.path.endsWith('/email-replies'))).toHaveLength(
    1,
  );
  expect(
    calls.filter((r) => r.path.includes('/capabilities?membership_id=')),
  ).toHaveLength(2);
  expect(calls.every((r) => r.method === 'GET')).toBe(true);
});
it('admin-only session cannot read mail even when an email transport would return a body', async () => {
  const f = await make();
  f.server.roles = ['admin'];
  await f.auth.recheck();
  const before = f.server.requests.length;
  await f.mail.load();
  expect(f.mail.getState().available).toBe(false);
  expect(f.server.requests.slice(before)).toHaveLength(0);
});
it.each(['case', 'owner', 'version'] as const)(
  'late mail after %s changes cannot cross the authorized detail boundary',
  async (change) => {
    const f = await make();
    let resolve!: (value: Response) => void;
    const original = f.server.custom!;
    f.server.custom = (path, init) =>
      path.endsWith('/email-replies')
        ? new Promise<Response>((r) => {
            resolve = r;
          })
        : original(path, init);
    const load = f.mail.load();
    if (change === 'owner') {
      f.server.proof = 'q'.repeat(43);
      await f.auth.recheck();
    } else if (change === 'case') await f.queue.openCase(caseId(2));
    else await f.queue.openCase(caseId(1), true);
    resolve(f.json([reply()]));
    await load;
    expect(f.mail.getState().replies.data).toBeNull();
  },
);
it('server capability version change during email read purges both projections before publishing a body', async () => {
  const f = await make();
  const cap = f.queue.getState().capabilities!;
  const original = f.server.custom!;
  f.server.custom = (path, init) =>
    path.includes('/capabilities?')
      ? Promise.resolve(f.json({ ...cap, case_version: cap.case_version + 1 }))
      : original(path, init);
  await f.mail.load();
  expect(f.mail.getState().denied).toMatchObject({ kind: 'stale' });
  expect(f.mail.getState().replies.data).toBeNull();
  expect(f.mail.getState().deliveries.data).toBeNull();
});
it.each([403, 404])(
  'denied email read %s removes all mail and never tries POST or automatic resend',
  async (status) => {
    const f = await make();
    const original = f.server.custom!;
    f.server.custom = (path, init) =>
      path.endsWith('/email-replies')
        ? Promise.resolve(denied(status))
        : original(path, init);
    await f.mail.load();
    expect(f.mail.getState().denied?.status).toBe(status);
    expect(f.mail.getState().deliveries.data).toBeNull();
    const before = f.server.requests.length;
    await f.mail.load();
    expect(f.server.requests).toHaveLength(before);
    expect(f.server.requests.every((r) => r.method === 'GET')).toBe(true);
  },
);
it('delivery503 preserves a separately authorized published reply; retry is explicit', async () => {
  const f = await make();
  const original = f.server.custom!;
  f.server.custom = (path, init) =>
    path.endsWith('/email-deliveries')
      ? Promise.resolve(denied(503))
      : original(path, init);
  await f.mail.load();
  expect(f.mail.getState().deliveries.phase).toBe('error');
  expect(f.mail.getState().replies.data?.[0]?.body).toBe(reply().body);
});
it('Retry-After survives remount and its deadline does not schedule an email request', async () => {
  vi.useFakeTimers();
  const f = await make();
  const original = f.server.custom!;
  f.server.custom = (path, init) => {
    if (!path.endsWith('/email-deliveries')) return original(path, init);
    const response = denied(429);
    response.headers.set('Retry-After', '20');
    return Promise.resolve(response);
  };
  await f.mail.load();
  const before = f.server.requests.length;
  const remounted = new StaffMailStore(f.queue, caseId(1)),
    off = remounted.connect();
  try {
    await remounted.load();
    expect(f.server.requests).toHaveLength(before);
    await vi.advanceTimersByTimeAsync(20001);
    expect(f.server.requests).toHaveLength(before);
  } finally {
    off();
  }
});
