import { afterEach, expect, it } from 'vitest';
import {
  adminFixture,
  OTHER_PERSON,
  PERSON,
  MEMBER,
  ORG,
  QUEUE,
  json,
} from './admin-fixture';
import {
  OTHER_MEMBER,
  OTHER_ORG,
  member as authMember,
  deferred,
} from '../staff/qa-fixture';
const owned: Awaited<ReturnType<typeof adminFixture>>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function setup() {
  const f = await adminFixture();
  owned.push(f);
  await f.admin.refresh();
  await f.admin.choose(OTHER_PERSON);
  return f;
}
it('uses exact selected organization and latest server actor version, explicit immutable review before PATCH', async () => {
  const f = await setup();
  const current = f.admin.getState().current!;
  expect(current.workspace.membership_version).toBe(7);
  f.admin.edit({ active: false, roles: ['supervisor'], queue_ids: [QUEUE] });
  await f.admin.save();
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
  f.admin.review();
  expect(f.admin.getState().review?.before.active).toBe(true);
  await f.admin.save();
  expect(f.calls.at(-1)).toMatchObject({
    path: '/api/v1/admin/staff/' + OTHER_PERSON + '/memberships',
    method: 'PATCH',
    body: {
      actor_membership_id: MEMBER,
      actor_version: 7,
      expected_version: 3,
      active: false,
      roles: ['supervisor'],
      queue_ids: [QUEUE],
    },
  });
  expect(f.admin.getState().notice).toBe('saved');
});
it('editing invalidates review, no self-principal write, invalid queue rejected', async () => {
  const f = await setup();
  f.admin.edit({ active: false, roles: ['cs'], queue_ids: [QUEUE] });
  f.admin.review();
  f.admin.edit({ active: true, roles: ['admin'], queue_ids: [QUEUE] });
  await f.admin.save();
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
  f.admin.edit({ active: true, roles: ['admin'], queue_ids: [OTHER_MEMBER] });
  f.admin.review();
  expect(f.admin.getState().review).toBeNull();
  f.access.member.principal_id = PERSON;
  await f.admin.recover();
  expect(f.admin.getState().current).toBeNull();
});
it('unknown PATCH never automatically replays, GET shows current state with explicit new review required', async () => {
  const f = await setup();
  f.access.lost = true;
  f.admin.edit({ active: false, roles: ['cs'], queue_ids: [QUEUE] });
  f.admin.review();
  await f.admin.save();
  expect(f.admin.getState()).toMatchObject({
    phase: 'unknown',
    current: null,
    edit: null,
    review: null,
  });
  await f.admin.save();
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1);
  await f.admin.recover();
  expect(f.admin.getState()).toMatchObject({
    phase: 'ready',
    notice: 'current',
    current: { member: { active: false, version: 4 } },
  });
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1);
  expect(f.admin.getState().review).toBeNull();
});
it('409 target/actor conflicts clear old review and require fresh GET/review', async () => {
  const f = await setup();
  f.access.conflict = true;
  f.admin.edit({ active: false, roles: ['cs'], queue_ids: [QUEUE] });
  f.admin.review();
  await f.admin.save();
  expect(f.admin.getState()).toMatchObject({
    phase: 'unavailable',
    current: null,
    review: null,
  });
  await f.admin.save();
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1);
  await f.admin.recover();
  expect(f.admin.getState().review).toBeNull();
});
it('organization switch and same membership role/version refresh clear private state', async () => {
  const f = await setup();
  f.server.memberships.push({
    ...authMember(),
    id: OTHER_MEMBER,
    organization_id: OTHER_ORG,
    roles: ['admin'],
  });
  await f.auth.recover();
  expect(f.admin.getState().current).toBeNull();
  await f.admin.refresh();
  await f.admin.choose(OTHER_PERSON);
  f.staff.select(OTHER_MEMBER);
  expect(f.admin.getState()).toMatchObject({
    phase: 'idle',
    current: null,
    page: null,
    edit: null,
  });
  expect(f.staff.captureScope()?.membership.organization_id).toBe(OTHER_ORG);
});
it('rejects foreign target, workspace, organization and directory metadata', async () => {
  const f = await setup();
  f.access.workspace.organization.id = OTHER_ORG;
  await f.admin.recover();
  expect(f.admin.getState()).toMatchObject({
    phase: 'unavailable',
    current: null,
  });
  f.access.workspace.organization.id = ORG;
  f.access.workspace.membership_id = OTHER_MEMBER;
  await f.admin.recover();
  expect(f.admin.getState().current).toBeNull();
});
it('scope switch drops in-flight metadata response', async () => {
  const f = await setup(),
    pending = deferred();
  f.server.handler = (path) =>
    path.includes('/admin/') ? pending.promise : null;
  const work = f.admin.recover();
  f.admin.clear();
  pending.resolve(
    json({ workspace: f.access.workspace, member: f.access.member }),
  );
  await work;
  expect(f.admin.getState()).toMatchObject({ phase: 'idle', current: null });
});
it('self membership is readable but never editable or submitted', async () => {
  const f = await adminFixture();
  owned.push(f);
  f.access.member.principal_id = PERSON;
  await f.admin.refresh();
  await f.admin.choose(PERSON);
  expect(f.admin.isSelf).toBe(true);
  f.admin.edit({ active: false, roles: ['admin'], queue_ids: [] });
  f.admin.review();
  await f.admin.save();
  expect(f.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
});

it('a pending admin PATCH cannot restore the old member or review after actual permission revocation', async () => {
  const f = await setup(),
    delayed = deferred();
  f.admin.edit({ roles: ['cs'], queue_ids: [QUEUE], active: false });
  f.admin.review();
  const previous = f.server.handler;
  let started!: () => void;
  const dispatched = new Promise<void>((done) => {
    started = done;
  });
  let patches = 0;
  f.server.handler = (path, init) => {
    if (path.includes('/admin/') && init?.method === 'PATCH') {
      patches++;
      started();
      return delayed.promise;
    }
    return previous?.(path, init) ?? null;
  };
  const pending = f.admin.save();
  await dispatched;
  f.server.memberships[0] = {
    ...f.server.memberships[0]!,
    version: 2,
    roles: ['cs'],
  };
  await f.auth.recover();
  delayed.resolve(
    json({
      workspace: f.access.workspace,
      member: { ...f.access.member, active: false, version: 4 },
    }),
  );
  await pending;
  expect(f.admin.getState()).toMatchObject({
    phase: 'idle',
    page: null,
    current: null,
    edit: null,
    review: null,
    notice: null,
  });
  await f.admin.save();
  expect(patches).toBe(1);
});
