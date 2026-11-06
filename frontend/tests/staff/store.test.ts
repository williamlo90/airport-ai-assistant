import { afterEach, expect, it } from 'vitest';
import {
  staffFixture,
  deferred,
  error,
  json,
  CONTACT,
  PASSWORD,
  OTHER_PERSON,
} from './qa-fixture';
const owned: ReturnType<typeof staffFixture>[] = [];
const make = () => {
  const f = staffFixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('one pending login cannot send a second command or retain credentials in staff state', async () => {
  const f = make();
  await f.ready();
  const pending = deferred();
  f.server.handler = (path) =>
    path.endsWith('/staff/auth/login') ? pending.promise : null;
  const first = f.staff.login(CONTACT, PASSWORD);
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1);
  expect(f.staff.getState()).toMatchObject({ busy: true, ready: false });
  expect(JSON.stringify(f.staff.getState())).not.toContain(PASSWORD);
  pending.resolve(error(401, 'STAFF_LOGIN_FAILED'));
  await first;
  expect(f.staff.getState()).toMatchObject({
    busy: false,
    message: 'rejected',
    ready: false,
  });
});
it('forbidden login rechecks the current session without replay or fake staff metadata', async () => {
  const f = make();
  await f.ready();
  f.server.handler = (path) =>
    path.endsWith('/staff/auth/login')
      ? Promise.resolve(error(403, 'CSRF_REQUIRED'))
      : null;
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1);
  expect(f.auth.getState()).toMatchObject({
    phase: 'ready',
    kind: 'anonymous',
  });
  expect(f.staff.captureScope()).toBeNull();
});
it('staff metadata for another principal cannot open workspace after a successful login response', async () => {
  const f = make();
  await f.ready();
  f.server.handler = (path) =>
    path.endsWith('/staff/me')
      ? Promise.resolve(
          json({
            session_kind: 'staff',
            principal_id: OTHER_PERSON,
            memberships: f.server.memberships,
          }),
        )
      : null;
  await f.staff.login(CONTACT, PASSWORD);
  expect(f.auth.getState().phase).toBe('unavailable');
  expect(f.staff.getState()).toMatchObject({
    ready: false,
    principalId: null,
    memberships: [],
  });
  expect(f.staff.captureScope()).toBeNull();
});
