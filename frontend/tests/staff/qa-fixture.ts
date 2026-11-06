import { vi } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import type { Membership } from '../../src/app/auth/contracts';
import { StaffStore } from '../../src/features/staff/store';

export const PERSON = '11111111-1111-4111-8111-111111111111';
export const OTHER_PERSON = '22222222-2222-4222-8222-222222222222';
export const MEMBER = '33333333-3333-4333-8333-333333333333';
export const ORG = '44444444-4444-4444-8444-444444444444';
export const QUEUE = '55555555-5555-4555-8555-555555555555';
export const OTHER_MEMBER = '66666666-6666-4666-8666-666666666666';
export const OTHER_ORG = '77777777-7777-4777-8777-777777777777';
export const OTHER_QUEUE = '88888888-8888-4888-8888-888888888888';
export const CONTACT = 'staff@example.com';
export const PASSWORD = ' Synthetic password with spaces ';
export const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
export const error = (status: number, code: string) =>
  json(
    {
      error: {
        code,
        message: 'DO NOT DISPLAY RAW SERVER TEXT',
        request_id: PERSON,
        retryable: false,
      },
    },
    status,
  );
export const member = (): Membership => ({
  id: MEMBER,
  organization_id: ORG,
  version: 1,
  roles: ['cs'],
  queue_ids: [QUEUE],
});
export const deferred = () => {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
export type Kind = 'guest' | 'anonymous' | 'customer' | 'staff';

export function staffFixture() {
  const server = {
    kind: 'anonymous' as Kind,
    principal: null as string | null,
    proof: 'a'.repeat(43) as string | null,
    memberships: [member()] as Membership[],
    sequence: 0,
    handler: null as
      null | ((path: string, init?: RequestInit) => Promise<Response> | null),
  };
  const rotate = () => {
    server.proof = String.fromCharCode(98 + (server.sequence++ % 20)).repeat(
      43,
    );
  };
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input),
        method = init?.method ?? 'GET';
      const custom = server.handler?.(path, init);
      if (custom) return custom;
      if (path === '/api/v1/me' && method === 'GET') {
        return server.kind === 'guest'
          ? error(401, 'AUTH_REQUIRED')
          : json({ session_kind: server.kind, principal_id: server.principal });
      }
      if (path === '/api/v1/staff/me' && method === 'GET') {
        return server.kind === 'staff'
          ? json({
              session_kind: 'staff',
              principal_id: server.principal,
              memberships: server.memberships,
            })
          : error(401, 'AUTH_REQUIRED');
      }
      if (path === '/api/v1/auth/sessions' && method === 'POST') {
        if (server.kind !== 'guest') return error(401, 'AUTH_REQUIRED');
        server.kind = 'anonymous';
        server.principal = null;
        rotate();
        return json({ session_kind: 'anonymous', principal_id: null });
      }
      if (path === '/api/v1/staff/auth/login' && method === 'POST') {
        const body = JSON.parse(String(init?.body));
        if (
          server.kind !== 'anonymous' ||
          body.contact !== CONTACT ||
          body.password !== PASSWORD
        )
          return error(401, 'STAFF_LOGIN_FAILED');
        server.kind = 'staff';
        server.principal = PERSON;
        rotate();
        return json({ session_kind: 'staff', principal_id: PERSON });
      }
      if (path === '/api/v1/auth/logout' && method === 'POST') {
        server.kind = 'guest';
        server.principal = null;
        server.proof = null;
        return new Response(null, { status: 204 });
      }
      return error(404, 'RESOURCE_NOT_FOUND');
    },
  );
  const auth = new AuthCoordinator({
    readProof: () => server.proof,
    fetch: fetcher,
  });
  const staff = new StaffStore(auth),
    off = staff.connect();
  return {
    server,
    auth,
    staff,
    fetcher,
    async ready(kind: Kind = 'anonymous') {
      server.kind = kind;
      server.proof = kind === 'guest' ? null : 'a'.repeat(43);
      server.principal =
        kind === 'customer' || kind === 'staff' ? PERSON : null;
      await auth.start();
    },
    posts(path: string) {
      return fetcher.mock.calls.filter(
        ([input, init]) => String(input) === path && init?.method === 'POST',
      );
    },
    dispose() {
      off();
      staff.dispose();
      auth.dispose();
    },
  };
}
