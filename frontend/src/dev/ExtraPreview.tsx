/** Development-only controlled UI fixture. No provider, email or database calls. */
import { useEffect, useMemo } from 'react';
import { useLocation } from 'react-router';
import { AuthCoordinator } from '../app/auth/coordinator';
import { AuthContext } from '../app/auth/context';
import { PageFrame } from '../app/routing/pages';
import { StaffContext } from '../features/staff/context';
import { StaffStore } from '../features/staff/store';
import { RatingStore } from '../features/feedback/store';
import { RatingPanel } from '../features/feedback/Rating';
import { AdminStore } from '../features/admin/store';
import { AdminPanel } from '../features/admin/AdminPage';
const principal = '11111111-1111-4111-8111-111111111111',
  target = '22222222-2222-4222-8222-222222222222',
  membership = '33333333-3333-4333-8333-333333333333',
  org = '44444444-4444-4444-8444-444444444444',
  queue = '55555555-5555-4555-8555-555555555555';
function fixture(adminMode: boolean) {
  const json = (v: unknown) =>
    new Response(JSON.stringify(v), {
      headers: { 'Content-Type': 'application/json' },
    });
  let rating: unknown = null,
    member = {
      membership_id: '66666666-6666-4666-8666-666666666666',
      principal_id: target,
      organization_id: org,
      display_name: 'Morgan Reed',
      principal_active: true,
      active: true,
      version: 3,
      roles: ['cs'],
      queue_ids: [queue],
    };
  const workspace = {
    membership_id: membership,
    membership_version: 7,
    organization: { id: org, name: 'Westbridge Airport Services' },
    queues: [
      { id: queue, name: 'Parking support', active: true },
      {
        id: '88888888-8888-4888-8888-888888888888',
        name: 'Lounge support',
        active: true,
      },
    ],
  };
  const auth = new AuthCoordinator({
    readProof: () => 'p'.repeat(43),
    fetch: async (input, init) => {
      const path = String(input);
      if (path === '/api/v1/me')
        return json({
          session_kind: adminMode ? 'staff' : 'anonymous',
          principal_id: adminMode ? principal : null,
        });
      if (path === '/api/v1/staff/me')
        return json({
          session_kind: 'staff',
          principal_id: principal,
          memberships: [
            {
              id: membership,
              organization_id: org,
              version: 1,
              roles: ['admin'],
              queue_ids: [],
            },
          ],
        });
      if (path.endsWith('/ratings')) {
        if (init?.method === 'POST') {
          const body = JSON.parse(String(init.body));
          rating = {
            id: target,
            score: body.score,
            comment: body.comment,
            version: 1,
            created_at: '2026-11-01T00:00:00Z',
            updated_at: '2026-11-01T00:00:00Z',
          };
        }
        return json({
          target_type: 'conversation',
          target_id: principal,
          eligible: true,
          rating,
        });
      }
      if (path.includes('/memberships')) {
        if (init?.method === 'PATCH') {
          const b = JSON.parse(String(init.body));
          member = {
            ...member,
            active: b.active,
            roles: b.roles,
            queue_ids: b.queue_ids,
            version: member.version + 1,
          };
        }
        return json({ workspace, member });
      }
      if (path.includes('/admin/staff?'))
        return json({
          workspace,
          items: [member],
          limit: 25,
          has_more: false,
          next_after: null,
          consistency: 'live_keyset',
        });
      return new Response(null, { status: 404 });
    },
  });
  const staff = new StaffStore(auth),
    off = staff.connect(),
    admin = new AdminStore(staff),
    ratingStore = new RatingStore(auth),
    offAdmin = admin.connect(),
    offRating = ratingStore.connect();
  return {
    auth,
    staff,
    admin,
    ratingStore,
    async ready() {
      await auth.start();
      if (!adminMode)
        await ratingStore.open({ kind: 'conversation', id: principal });
    },
    dispose() {
      offRating();
      offAdmin();
      off();
      staff.dispose();
      auth.dispose();
    },
  };
}
export function Component() {
  const location = useLocation(),
    adminMode = location.pathname.endsWith('/admin'),
    f = useMemo(() => fixture(adminMode), [adminMode]),
    lease = useMemo(() => {
      let value = 0;
      return {
        acquire: () => ++value,
        isCurrent: (generation: number) => generation === value,
      };
    }, []);
  useEffect(() => {
    const generation = lease.acquire();
    void f.ready();
    return () => {
      queueMicrotask(() => {
        if (lease.isCurrent(generation)) f.dispose();
      });
    };
  }, [f, lease]);
  return (
    <AuthContext value={f.auth}>
      <StaffContext value={f.staff}>
        <PageFrame staff={adminMode}>
          <p role="note">
            Design preview — Synthetic accounts and feedback. No data is sent to
            a server.
          </p>
          {adminMode ? (
            <AdminPanel store={f.admin} />
          ) : (
            <>
              <h1>Handling result</h1>
              <p>The parking procedure was explained. No action is pending.</p>
              <RatingPanel store={f.ratingStore} />
            </>
          )}
        </PageFrame>
      </StaffContext>
    </AuthContext>
  );
}
