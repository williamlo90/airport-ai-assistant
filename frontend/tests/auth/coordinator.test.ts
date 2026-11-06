import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';
import {
  decodeStaff,
  parseSignal,
  readCsrfCookie,
} from '../../src/app/auth/contracts';
import type { AuthSignal } from '../../src/app/auth/contracts';
import type { SessionView } from '../../src/api/contracts';
import { record } from '../../src/api/contracts';
const id = '11111111-1111-4111-8111-111111111111',
  other = '22222222-2222-4222-8222-222222222222',
  org = '33333333-3333-4333-8333-333333333333';
const P = 'p'.repeat(43),
  Q = 'q'.repeat(43),
  R = 'r'.repeat(43),
  resource = '/cases/' + id;
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const failure = (status: number, code = 'ACTION_FORBIDDEN') =>
  json(
    { error: { code, message: 'PRIVATE', request_id: id, retryable: false } },
    status,
  );
const staff = (principal = id, roles = ['cs']) => ({
  session_kind: 'staff',
  principal_id: principal,
  memberships: [
    { id: other, organization_id: org, version: 1, roles, queue_ids: [id] },
  ],
});
const owned: AuthCoordinator[] = [];
function fixture(
  initial: SessionView | null = {
    session_kind: 'anonymous',
    principal_id: null,
  },
  signal?: {
    publish: (s: AuthSignal) => void;
    subscribe: (f: (s: unknown) => void) => () => void;
  },
) {
  const server = {
    proof: initial ? P : (null as string | null),
    session: initial,
    staff: staff(),
    businessStatus: 200,
    override: null as
      null | ((url: string, init?: RequestInit) => Promise<Response>),
  };
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path = String(input);
      if (server.override) return server.override(path, init);
      if (path === '/api/v1/me')
        return server.session
          ? json(server.session)
          : failure(401, 'AUTH_REQUIRED');
      if (path === '/api/v1/staff/me') return json(server.staff);
      if (path === '/api/v1/auth/sessions') {
        server.session = { session_kind: 'anonymous', principal_id: null };
        server.proof = Q;
        return json(server.session);
      }
      if (path === '/api/v1/auth/logout') {
        server.session = null;
        server.proof = null;
        return new Response(null, { status: 204 });
      }
      if (path === '/api/v1/auth/challenges/' + id + '/verify') {
        server.session = { session_kind: 'customer', principal_id: id };
        server.proof = R;
        return json(server.session);
      }
      if (path === '/api/v1/staff/auth/login') {
        server.session = { session_kind: 'staff', principal_id: id };
        server.proof = R;
        return json(server.session);
      }
      return server.businessStatus === 200
        ? json({ id })
        : failure(server.businessStatus);
    },
  );
  const coordinator = new AuthCoordinator({
    readProof: () => server.proof,
    fetch: fetcher,
    ...(signal ? { signals: signal } : {}),
  });
  owned.push(coordinator);
  return { server, fetcher, coordinator };
}
async function settle() {
  for (let i = 0; i < 12; i++) await Promise.resolve();
}
afterEach(() => {
  owned.splice(0).forEach((auth) => auth.dispose());
  vi.restoreAllMocks();
});
describe('session coordinator', () => {
  it('inspects once, never bootstraps on mount/StrictMode duplicate start', async () => {
    const f = fixture(null);
    await Promise.all([f.coordinator.start(), f.coordinator.start()]);
    expect(f.coordinator.getState().phase).toBe('guest');
    expect(f.fetcher).toHaveBeenCalledTimes(1);
    expect(f.fetcher.mock.calls[0]![0]).toBe('/api/v1/me');
    expect(() => f.coordinator.capture()).toThrow();
  });
  it('requires stable proof; no proof cannot open business gate', async () => {
    const f = fixture();
    f.server.proof = null;
    await f.coordinator.start();
    expect(f.coordinator.getState().phase).toBe('unavailable');
    expect(() => f.coordinator.capture()).toThrow();
  });
  it('reads staff memberships and does not treat admin-only as a case role', async () => {
    const f = fixture({ session_kind: 'staff', principal_id: id });
    f.server.staff = staff(id, ['admin']);
    await f.coordinator.start();
    expect(f.coordinator.getState()).toMatchObject({
      phase: 'ready',
      kind: 'staff',
      roles: ['admin'],
    });
    expect(
      f.coordinator.membershipsFor(f.coordinator.capture())[0]?.organization_id,
    ).toBe(org);
    expect(f.fetcher).toHaveBeenCalledTimes(2);
  });
  it('rejects mismatched or revoked staff metadata', async () => {
    const f = fixture({ session_kind: 'staff', principal_id: id });
    f.server.staff = staff(other);
    await f.coordinator.start();
    expect(f.coordinator.getState().phase).toBe('unavailable');
  });
  it('rejects a response after two unstable proof reads', async () => {
    const f = fixture();
    f.server.override = async () => {
      f.server.proof = f.server.proof === P ? Q : P;
      return json({ session_kind: 'anonymous', principal_id: null });
    };
    await f.coordinator.start();
    expect(f.coordinator.getState().phase).toBe('unavailable');
    expect(f.fetcher).toHaveBeenCalledTimes(2);
  });
  it('rechecks a rotated proof before accepting a snapshot', async () => {
    const f = fixture();
    let calls = 0;
    f.server.override = async () => {
      calls++;
      if (calls === 1) f.server.proof = Q;
      return json({ session_kind: 'customer', principal_id: other });
    };
    await f.coordinator.start();
    expect(f.coordinator.capture()).toMatchObject({
      csrfProof: Q,
      principalId: other,
    });
    expect(calls).toBe(2);
  });
  it('closes gate, clears commands, quarantines draft then requires fresh authorized resource read', async () => {
    const f = fixture();
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    f.coordinator.keep(resource, 'unsent report', owner);
    f.coordinator.keep('command', { payload: 'old' }, owner, 'command');
    const pending = f.coordinator.recheck();
    expect(f.coordinator.getState().phase).toBe('checking');
    expect(f.coordinator.retained(resource, owner)).toBeUndefined();
    await pending;
    const next = f.coordinator.capture();
    expect(next.boundaryId).toBe(owner.boundaryId);
    expect(next.authEpoch).not.toBe(owner.authEpoch);
    expect(f.coordinator.retained('command', next)).toBeUndefined();
    expect(f.coordinator.retained(resource, next)).toBeUndefined();
    await f.coordinator.readResource({
      path: resource,
      method: 'GET',
      boundary: next,
      isRelevant: () => true,
      successStatus: 200,
      decode: record,
    });
    expect(f.coordinator.retained(resource, next)).toBe('unsent report');
  });
  it('clears drafts on actual account/proof change instead of moving them', async () => {
    const f = fixture();
    await f.coordinator.start();
    const old = f.coordinator.capture();
    f.coordinator.keep(resource, 'private', old);
    f.server.session = { session_kind: 'customer', principal_id: other };
    f.server.proof = Q;
    await f.coordinator.recheck();
    const next = f.coordinator.capture();
    expect(next.boundaryId).not.toBe(old.boundaryId);
    await f.coordinator.readResource({
      path: resource,
      method: 'GET',
      boundary: next,
      isRelevant: () => true,
      successStatus: 200,
      decode: record,
    });
    expect(f.coordinator.retained(resource, next)).toBeUndefined();
  });
  it('refuses an old command before dispatch even without channel delivery', async () => {
    const f = fixture();
    await f.coordinator.start();
    const old = f.coordinator.capture();
    f.server.proof = Q;
    f.server.session = { session_kind: 'customer', principal_id: other };
    const count = f.fetcher.mock.calls.length;
    await expect(
      f.coordinator.request({
        path: resource,
        method: 'POST',
        body: { private: 'OLD' },
        boundary: old,
        isRelevant: () => true,
        successStatus: 200,
        decode: record,
      }),
    ).rejects.toMatchObject({ kind: 'stale', outcome: 'not-sent' });
    await settle();
    expect(
      f.fetcher.mock.calls
        .slice(count)
        .every(([, opts]) => opts?.method === 'GET'),
    ).toBe(true);
  });
  it('keeps the old captured header across the preflight/send race', async () => {
    const f = fixture();
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    let header = '';
    f.server.override = async (path, opts) => {
      if (path === resource.replace(/^/, '/api/v1')) {
        header = new Headers(opts?.headers).get('X-CSRF-Token') ?? '';
        f.server.proof = Q;
        f.server.session = { session_kind: 'customer', principal_id: other };
        return failure(403, 'CSRF_REQUIRED');
      }
      return json(f.server.session);
    };
    await expect(
      f.coordinator.request({
        path: resource,
        method: 'POST',
        body: { old: 'payload' },
        boundary: owner,
        isRelevant: () => true,
        successStatus: 200,
        decode: record,
      }),
    ).rejects.toMatchObject({ kind: 'stale' });
    expect(header).toBe(P);
    await settle();
    expect(f.coordinator.capture().principalId).toBe(other);
  });
  it('aborts a request on recheck and discards a late response without resetting the new session', async () => {
    const f = fixture();
    await f.coordinator.start();
    const old = f.coordinator.capture();
    let finish!: (r: Response) => void;
    let signal: AbortSignal | null | undefined;
    f.server.override = (path, opts) =>
      path === '/api/v1/me'
        ? Promise.resolve(json(f.server.session))
        : new Promise((resolve) => {
            finish = resolve;
            signal = opts?.signal;
          });
    const pending = f.coordinator.request({
      path: resource,
      method: 'GET',
      boundary: old,
      isRelevant: () => true,
      successStatus: 200,
      decode: record,
    });
    const assertion = expect(pending).rejects.toMatchObject({ kind: 'stale' });
    f.server.proof = Q;
    f.server.session = { session_kind: 'customer', principal_id: other };
    await f.coordinator.recheck();
    const next = f.coordinator.capture();
    expect(signal?.aborted).toBe(true);
    finish(json({ old: 'private' }));
    await assertion;
    expect(f.coordinator.isCurrent(next)).toBe(true);
  });
  it('401 clears boundary and does not replay a business command or create anonymous session', async () => {
    const f = fixture();
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    f.server.businessStatus = 401;
    f.server.session = null;
    await expect(
      f.coordinator.request({
        path: resource,
        method: 'POST',
        boundary: owner,
        isRelevant: () => true,
        successStatus: 200,
        decode: record,
      }),
    ).rejects.toMatchObject({ status: 401 });
    await settle();
    expect(f.coordinator.getState().phase).toBe('guest');
    expect(
      f.fetcher.mock.calls.filter(([, opts]) => opts?.method === 'POST'),
    ).toHaveLength(1);
    expect(
      f.fetcher.mock.calls.some(([path]) => path === '/api/v1/auth/sessions'),
    ).toBe(false);
  });
  it.each(['ACTION_FORBIDDEN', 'CSRF_REQUIRED'])(
    '403 %s quarantines authorized draft until session and resource recheck',
    async (code) => {
      const f = fixture();
      await f.coordinator.start();
      const owner = f.coordinator.capture();
      f.coordinator.keep(resource, 'still authorized draft', owner);
      f.server.override = async (path, opts) =>
        path === '/api/v1/me'
          ? json(f.server.session)
          : opts?.method === 'PATCH'
            ? failure(403, code)
            : json({ id });
      await expect(
        f.coordinator.mutateResource(resource, {
          path: resource + '/status',
          method: 'PATCH',
          boundary: owner,
          isRelevant: () => true,
          successStatus: 200,
          decode: record,
        }),
      ).rejects.toMatchObject({ status: 403, code });
      const next = f.coordinator.capture();
      expect(f.coordinator.retained(resource, next)).toBeUndefined();
      await f.coordinator.readResource({
        path: resource,
        method: 'GET',
        boundary: next,
        isRelevant: () => true,
        successStatus: 200,
        decode: record,
      });
      expect(f.coordinator.retained(resource, next)).toBe(
        'still authorized draft',
      );
      expect(
        f.fetcher.mock.calls.filter(([, opts]) => opts?.method === 'PATCH'),
      ).toHaveLength(1);
    },
  );
  it.each([403, 404])(
    'resource read %s erases data rather than restoring private draft',
    async (status) => {
      const f = fixture();
      await f.coordinator.start();
      const owner = f.coordinator.capture();
      f.coordinator.keep(resource, 'private', owner);
      f.server.businessStatus = status;
      await expect(
        f.coordinator.readResource({
          path: resource,
          method: 'GET',
          boundary: owner,
          isRelevant: () => true,
          successStatus: 200,
          decode: record,
        }),
      ).rejects.toMatchObject({ status });
      expect(f.coordinator.retained(resource, owner)).toBeUndefined();
      f.server.businessStatus = 200;
      await f.coordinator.readResource({
        path: resource,
        method: 'GET',
        boundary: owner,
        isRelevant: () => true,
        successStatus: 200,
        decode: record,
      });
      expect(f.coordinator.retained(resource, owner)).toBeUndefined();
    },
  );
  it('begins transition and publishes before auth request; ends and rechecks after cookie removal', async () => {
    const order: string[] = [];
    const f = fixture(undefined, {
      publish: (s) => order.push(s.type),
      subscribe: () => () => {},
    });
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    f.coordinator.keep(resource, 'old draft', owner);
    f.coordinator.onReset(() => order.push('gate-reset'));
    const original = f.fetcher.getMockImplementation()!;
    f.server.override = async (path) => {
      if (path === '/api/v1/auth/logout') {
        order.push('logout-post');
        expect(f.coordinator.getState().phase).toBe('transition');
        expect(f.coordinator.retained(resource, owner)).toBeUndefined();
        f.server.session = null;
        f.server.proof = null;
        return new Response(null, { status: 204 });
      }
      return f.server.session ? json(f.server.session) : failure(401);
    };
    void original;
    const result = await f.coordinator.change({ kind: 'logout' });
    expect(result).toMatchObject({
      outcome: 'confirmed',
      state: { phase: 'guest' },
    });
    expect(order.indexOf('auth_transition_begin')).toBeLessThan(
      order.indexOf('logout-post'),
    );
    expect(order.indexOf('logout-post')).toBeLessThan(
      order.indexOf('auth_transition_end'),
    );
  });
  it('auth failure still publishes end and rechecks; credentials are never retried', async () => {
    const signals: AuthSignal[] = [];
    const f = fixture(undefined, {
      publish: (s) => signals.push(s),
      subscribe: () => () => {},
    });
    await f.coordinator.start();
    f.server.override = async (path) =>
      path === '/api/v1/me'
        ? json(f.server.session)
        : failure(401, 'STAFF_LOGIN_FAILED');
    const result = await f.coordinator.change({
      kind: 'staff-login',
      contact: 'staff@example.com',
      password: 'synthetic',
    });
    expect(result.outcome).toBe('rejected');
    expect(result.state.phase).toBe('ready');
    expect(signals.map((s) => s.type)).toEqual([
      'auth_transition_begin',
      'auth_transition_end',
    ]);
    expect(
      f.fetcher.mock.calls.filter(([, o]) => o?.method === 'POST'),
    ).toHaveLength(1);
    expect(JSON.stringify(signals)).not.toContain('synthetic');
  });
  it('verification upgrade offers only a caller-owned plain-text resume hook, not old commands', async () => {
    const f = fixture();
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    f.coordinator.keep(resource, 'old data', owner);
    const result = await f.coordinator.change({
      kind: 'verify',
      challengeId: id,
      code: '12345678',
    });
    expect(result.resumeFromBoundaryId).toBe(owner.boundaryId);
    expect(f.coordinator.getState()).toMatchObject({
      phase: 'ready',
      kind: 'customer',
    });
    expect(
      f.coordinator.retained(resource, f.coordinator.capture()),
    ).toBeUndefined();
  });
  it('never verifies a code or staff login using a customer session', async () => {
    const f = fixture({ session_kind: 'customer', principal_id: id });
    await f.coordinator.start();
    await expect(
      f.coordinator.change({
        kind: 'verify',
        challengeId: id,
        code: '12345678',
      }),
    ).rejects.toMatchObject({ kind: 'invalid-request' });
    expect(f.fetcher).toHaveBeenCalledTimes(1);
  });
  it('handles remote begin/end with gate closed until a fresh read, without signal data granting access', async () => {
    let receive!: (raw: unknown) => void;
    const f = fixture(undefined, {
      publish: () => {},
      subscribe: (fn) => {
        receive = fn;
        return () => {};
      },
    });
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    f.coordinator.keep(resource, 'private', owner);
    receive({ type: 'auth_transition_begin', transitionId: other });
    expect(f.coordinator.getState().phase).toBe('transition');
    expect(() => f.coordinator.capture()).toThrow();
    await f.coordinator.recheck();
    expect(f.coordinator.getState().phase).toBe('transition');
    f.server.session = { session_kind: 'customer', principal_id: id };
    f.server.proof = Q;
    receive({ type: 'auth_transition_end', transitionId: other });
    await settle();
    expect(f.coordinator.getState()).toMatchObject({
      phase: 'ready',
      kind: 'customer',
    });
    expect(
      f.coordinator.retained(resource, f.coordinator.capture()),
    ).toBeUndefined();
  });
  it('ignores malformed signals with roles, proof or payload', async () => {
    let receive!: (s: unknown) => void;
    const f = fixture(undefined, {
      publish: () => {},
      subscribe: (fn) => {
        receive = fn;
        return () => {};
      },
    });
    await f.coordinator.start();
    const before = f.coordinator.getState();
    receive({
      type: 'auth_transition_begin',
      transitionId: other,
      role: 'admin',
    });
    expect(f.coordinator.getState()).toBe(before);
  });
  it('lost end stays blocked until explicit recovery and fresh session inspection', async () => {
    let receive!: (s: unknown) => void;
    const f = fixture(undefined, {
      publish: () => {},
      subscribe: (fn) => {
        receive = fn;
        return () => {};
      },
    });
    await f.coordinator.start();
    receive({ type: 'auth_transition_begin', transitionId: other });
    expect(f.coordinator.getState().phase).toBe('transition');
    await f.coordinator.recover();
    expect(f.coordinator.getState().phase).toBe('ready');
  });
  it('disabled HTTP preview does not inspect or mutate sessions', async () => {
    const fetcher = vi.fn();
    const auth = new AuthCoordinator({
      enabled: false,
      fetch: fetcher,
      readProof: () => null,
    });
    owned.push(auth);
    await auth.start();
    await auth.recheck();
    expect(fetcher).not.toHaveBeenCalled();
    await expect(auth.change({ kind: 'bootstrap' })).rejects.toMatchObject({
      kind: 'stale',
    });
  });
  it('dispose unsubscribes and blocks future reads/transitions', async () => {
    const stop = vi.fn();
    const f = fixture(undefined, { publish: () => {}, subscribe: () => stop });
    await f.coordinator.start();
    f.coordinator.dispose();
    const count = f.fetcher.mock.calls.length;
    await f.coordinator.recheck();
    expect(f.fetcher).toHaveBeenCalledTimes(count);
    expect(stop).toHaveBeenCalledOnce();
    expect(() => f.coordinator.capture()).toThrow();
  });
});
describe('wire boundaries', () => {
  it('rejects malformed/duplicate CSRF cookies and accepts only the named readable proof', () => {
    expect(readCsrfCookie('__Host-airport_csrf=' + P)).toBe(P);
    expect(readCsrfCookie('__Host-airport_session=PRIVATE')).toBeNull();
    expect(
      readCsrfCookie('__Host-airport_csrf=' + P + '; __Host-airport_csrf=' + Q),
    ).toBeNull();
    expect(readCsrfCookie('__Host-airport_csrf=short')).toBeNull();
  });
  it('validates membership identity/scope and signal shape without storing untrusted data', () => {
    expect(decodeStaff(staff()).memberships[0]?.queue_ids).toEqual([id]);
    expect(() => decodeStaff(staff(id, ['superuser']))).toThrow();
    expect(() =>
      decodeStaff({
        ...staff(),
        memberships: [...staff().memberships, ...staff().memberships],
      }),
    ).toThrow();
    expect(
      parseSignal({ type: 'auth_transition_end', transitionId: other }),
    ).toEqual({ type: 'auth_transition_end', transitionId: other });
    expect(
      parseSignal({
        type: 'auth_transition_end',
        transitionId: other,
        csrf: P,
      }),
    ).toBeNull();
  });
});

it.each(['401', 'proof-change'])(
  'challenge %s invalidates only its owning session without replay',
  async (mode) => {
    const f = fixture();
    await f.coordinator.start();
    const owner = f.coordinator.capture();
    f.coordinator.keep(resource, 'PRIVATE', owner);
    f.server.override = async (path) => {
      if (path === '/api/v1/me')
        return f.server.session ? json(f.server.session) : failure(401);
      f.server.session = null;
      if (mode === 'proof-change') f.server.proof = Q;
      return failure(401, 'AUTH_REQUIRED');
    };
    await expect(
      f.coordinator.requestChallenge('synthetic@example.com'),
    ).rejects.toBeDefined();
    await settle();
    expect(f.coordinator.isCurrent(owner)).toBe(false);
    expect(f.coordinator.retained(resource, owner)).toBeUndefined();
    expect(
      f.fetcher.mock.calls.filter(([, o]) => o?.method === 'POST'),
    ).toHaveLength(1);
  },
);
