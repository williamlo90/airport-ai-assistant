import { afterEach, expect, it, vi } from 'vitest';
import { canHandleCases } from '../../src/features/staff/store';
import {
  staffFixture,
  member,
  json,
  error,
  CONTACT,
  PASSWORD,
  PERSON,
  MEMBER,
  OTHER_MEMBER,
  OTHER_ORG,
  OTHER_QUEUE,
} from './qa-fixture';

const owned: ReturnType<typeof staffFixture>[] = [];
const make = () => {
  const f = staffFixture();
  owned.push(f);
  return f;
};
afterEach(() => {
  vi.restoreAllMocks();
  owned.splice(0).forEach((f) => f.dispose());
});

it('guest preparation is explicit and does not automatically log in or load cases', async () => {
  const f = make();
  await f.ready('guest');
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(0);
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(0);
  await f.staff.prepare();
  expect(f.auth.getState()).toMatchObject({
    phase: 'ready',
    kind: 'anonymous',
  });
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(1);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(0);
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/cases')),
  ).toBe(false);
});

it('customer login gate preserves the current account until explicit logout and never silently bootstraps', async () => {
  const f = make();
  await f.ready('customer');
  const owner = f.auth.capture();
  f.auth.keep('qa-private-report', { body: 'Customer private report' }, owner);
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.auth.isCurrent(owner)).toBe(true);
  expect(f.auth.retained('qa-private-report', owner)).toEqual({
    body: 'Customer private report',
  });
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(0);
  await f.staff.logout(owner);
  expect(f.auth.getState().phase).toBe('guest');
  expect(f.auth.retained('qa-private-report', owner)).toBeUndefined();
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(0);
});

it('admin queues are not combined with customer-service roles from a different membership', async () => {
  const f = make();
  f.server.memberships = [
    { ...member(), roles: ['admin'] },
    {
      id: OTHER_MEMBER,
      organization_id: OTHER_ORG,
      version: 1,
      roles: ['cs'],
      queue_ids: [OTHER_QUEUE],
    },
  ];
  await f.ready('staff');
  expect(f.staff.getState().selectedId).toBeNull();
  f.staff.select(MEMBER);
  const administrative = f.staff.captureScope()!;
  expect(administrative.membership.roles).toEqual(['admin']);
  expect(canHandleCases(administrative.membership)).toBe(false);
  f.staff.select(OTHER_MEMBER);
  const service = f.staff.captureScope()!;
  expect(canHandleCases(service.membership)).toBe(true);
  expect(service.membership.organization_id).toBe(OTHER_ORG);
  expect(service.membership.queue_ids).toEqual([OTHER_QUEUE]);
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/cases')),
  ).toBe(false);
});

it('membership changes on the same staff account invalidate old scope and replace permissions after fresh inspection', async () => {
  const f = make();
  await f.ready('staff');
  const previous = f.staff.captureScope()!;
  f.server.memberships = [
    { ...member(), version: 2, roles: ['admin'], queue_ids: [] },
  ];
  const checking = f.auth.recheck();
  expect(f.staff.getState()).toMatchObject({
    ready: false,
    memberships: [],
    selectedId: null,
  });
  await checking;
  const current = f.staff.captureScope()!;
  expect(f.auth.isCurrent(previous.owner)).toBe(false);
  expect(current.membership).toMatchObject({
    version: 2,
    roles: ['admin'],
    queue_ids: [],
  });
  expect(canHandleCases(current.membership)).toBe(false);
  f.server.kind = 'guest';
  await f.auth.recheck();
  expect(f.staff.getState()).toMatchObject({
    ready: false,
    principalId: null,
    memberships: [],
  });
  expect(f.staff.captureScope()).toBeNull();
});

it('a nonstaff login200 cannot produce verified staff scope', async () => {
  const f = make();
  await f.ready();
  f.server.handler = (path) =>
    path.endsWith('/staff/auth/login')
      ? Promise.resolve(json({ session_kind: 'anonymous', principal_id: null }))
      : null;
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.staff.getState()).toMatchObject({
    ready: false,
    message: 'unknown',
    memberships: [],
  });
  expect(f.auth.getState()).toMatchObject({
    phase: 'ready',
    kind: 'anonymous',
  });
});

it('lost login response inspects the actual rotated staff session without replaying credentials', async () => {
  const f = make();
  await f.ready();
  f.server.handler = (path) => {
    if (!path.endsWith('/staff/auth/login')) return null;
    f.server.kind = 'staff';
    f.server.principal = PERSON;
    f.server.proof = 'b'.repeat(43);
    return Promise.reject(Error('Lost login response'));
  };
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.staff.getState()).toMatchObject({
    ready: true,
    principalId: PERSON,
    message: 'unknown',
  });
  expect(f.staff.captureScope()?.membership.id).toBe(MEMBER);
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1);
  expect(JSON.stringify(f.staff.getState())).not.toContain(PASSWORD);
  expect(JSON.stringify(f.auth.getState())).not.toContain(PASSWORD);
});

it('429 cooldown survives auth checking and explicit logout while guest preparation still needs another click after waiting', async () => {
  const f = make();
  await f.ready();
  const now = Date.now();
  vi.spyOn(Date, 'now').mockReturnValue(now);
  f.server.handler = (path) => {
    if (!path.endsWith('/staff/auth/login')) return null;
    const response = error(429, 'RATE_LIMITED');
    response.headers.set('Retry-After', '60');
    return Promise.resolve(response);
  };
  await f.staff.login(CONTACT, PASSWORD);
  await f.auth.recheck();
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1);
  await f.staff.logout(f.auth.capture());
  await f.staff.prepare();
  expect(f.auth.getState().phase).toBe('guest');
  expect(f.posts('/api/v1/auth/logout')).toHaveLength(1);
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(0);
  vi.spyOn(Date, 'now').mockReturnValue(now + 61000);
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(0);
  await f.staff.prepare();
  expect(f.posts('/api/v1/auth/sessions')).toHaveLength(1);
});
