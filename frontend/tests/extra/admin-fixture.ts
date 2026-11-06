import {
  staffFixture,
  json,
  PERSON,
  OTHER_PERSON,
  MEMBER,
  ORG,
  QUEUE,
} from '../staff/qa-fixture';
import { AdminStore } from '../../src/features/admin/store';
export { json, PERSON, OTHER_PERSON, MEMBER, ORG, QUEUE };
export const workspace = () => ({
  membership_id: MEMBER,
  membership_version: 7,
  organization: { id: ORG, name: 'Airport services' },
  queues: [{ id: QUEUE, name: 'Parking support', active: true }],
});
export const member = () => ({
  membership_id: '99999999-9999-4999-8999-999999999999',
  principal_id: OTHER_PERSON,
  organization_id: ORG,
  display_name: 'Morgan Reed',
  principal_active: true,
  active: true,
  version: 3,
  roles: ['cs'],
  queue_ids: [QUEUE],
});
export async function adminFixture() {
  const f = staffFixture();
  f.server.memberships[0] = { ...f.server.memberships[0]!, roles: ['admin'] };
  await f.ready('staff');
  const admin = new AdminStore(f.staff),
    off = admin.connect();
  const calls: { path: string; method: string; body: unknown }[] = [];
  const server = {
    workspace: workspace(),
    member: member(),
    lost: false,
    conflict: false,
  };
  f.server.handler = (path, init) => {
    if (!path.includes('/admin/')) return null;
    const method = init?.method ?? 'GET';
    calls.push({
      path,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    if (path.includes('/memberships')) {
      if (method === 'PATCH') {
        if (server.conflict)
          return Promise.resolve(
            new Response(JSON.stringify({}), {
              status: 409,
              headers: { 'Content-Type': 'application/json' },
            }),
          );
        server.member = {
          ...server.member,
          ...JSON.parse(String(init?.body)),
          version: server.member.version + 1,
        };
        if (server.lost) return Promise.reject(Error('lost'));
      }
      return Promise.resolve(
        json({ workspace: server.workspace, member: server.member }),
      );
    }
    return Promise.resolve(
      json({
        workspace: server.workspace,
        items: [server.member],
        limit: 25,
        has_more: false,
        next_after: null,
        consistency: 'live_keyset',
      }),
    );
  };
  return {
    ...f,
    admin,
    calls,
    access: server,
    dispose() {
      off();
      f.dispose();
    },
  };
}
