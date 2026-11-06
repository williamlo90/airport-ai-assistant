import { describe, expect, it } from 'vitest';
import { AuthCoordinator } from '../../src/app/auth/coordinator';

const P = 'p'.repeat(43);
const Q = 'q'.repeat(43);
const id = '11111111-1111-4111-8111-111111111111';
const json = (v: unknown, status = 200) =>
  new Response(JSON.stringify(v), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('independent auth adversarial regressions', () => {
  it.each(['unauthorized', 'rotated-proof'] as const)(
    'challenge %s closes the old gate and clears retained input',
    async (mode) => {
      let proof: string | null = P;
      let expired = false;
      let posts = 0;
      const auth = new AuthCoordinator({
        readProof: () => proof,
        fetch: async (url) => {
          if (String(url) === '/api/v1/me') {
            return expired
              ? json({}, 401)
              : json({ session_kind: 'anonymous', principal_id: null });
          }
          posts++;
          if (mode === 'rotated-proof') {
            proof = Q;
            return json(
              { challenge_id: id, status: 'verification_requested' },
              202,
            );
          }
          expired = true;
          return json({}, 401);
        },
      });
      try {
        await auth.start();
        const owner = auth.capture();
        auth.keep('/cases/' + id, 'private old input', owner);
        await expect(
          auth.requestChallenge('synthetic@example.com'),
        ).rejects.toBeDefined();
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(auth.isCurrent(owner)).toBe(false);
        expect(auth.retained('/cases/' + id, owner)).toBeUndefined();
        expect(posts).toBe(1);
      } finally {
        auth.dispose();
      }
    },
  );

  it('a late challenge rejection cannot reset the replacement account', async () => {
    let proof = P;
    let session = {
      session_kind: 'anonymous',
      principal_id: null as string | null,
    };
    let finish!: (response: Response) => void;
    let reads = 0;
    const auth = new AuthCoordinator({
      readProof: () => proof,
      fetch: async (url) => {
        if (String(url) === '/api/v1/me') {
          reads++;
          return json(session);
        }
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      },
    });
    try {
      await auth.start();
      const pending = expect(
        auth.requestChallenge('synthetic@example.com'),
      ).rejects.toBeDefined();
      proof = Q;
      session = { session_kind: 'customer', principal_id: id };
      await auth.recheck();
      const replacement = auth.capture();
      auth.keep('/cases/' + id, 'replacement input', replacement);
      finish(json({}, 401));
      await pending;
      expect(auth.isCurrent(replacement)).toBe(true);
      expect(auth.retained('/cases/' + id, replacement)).toBe(
        'replacement input',
      );
      expect(reads).toBe(2);
    } finally {
      auth.dispose();
    }
  });

  it('drops staff metadata if proof rotates between me and staff/me', async () => {
    let proof = P;
    const paths: string[] = [];
    const auth = new AuthCoordinator({
      readProof: () => proof,
      fetch: async (url) => {
        const path = String(url);
        paths.push(path);
        if (path === '/api/v1/staff/me') {
          proof = Q;
          return json({
            session_kind: 'staff',
            principal_id: id,
            memberships: [
              {
                id,
                organization_id: id,
                version: 1,
                roles: ['supervisor'],
                queue_ids: [],
              },
            ],
          });
        }
        return json({
          session_kind: proof === P ? 'staff' : 'customer',
          principal_id: id,
        });
      },
    });
    try {
      await auth.start();
      expect(auth.getState()).toMatchObject({
        phase: 'ready',
        kind: 'customer',
        roles: [],
      });
      expect(auth.capture().csrfProof).toBe(Q);
      expect(paths).toEqual(['/api/v1/me', '/api/v1/staff/me', '/api/v1/me']);
    } finally {
      auth.dispose();
    }
  });

  it('lost logout response publishes end and rechecks without claiming success or replaying', async () => {
    let proof: string | null = P;
    const signals: unknown[] = [];
    let posts = 0;
    const auth = new AuthCoordinator({
      readProof: () => proof,
      signals: {
        publish: (s) => {
          signals.push(s);
        },
        subscribe: () => () => {},
      },
      fetch: async (url) => {
        if (String(url) === '/api/v1/me')
          return proof
            ? json({ session_kind: 'anonymous', principal_id: null })
            : json({}, 401);
        posts++;
        expect(auth.getState().phase).toBe('transition');
        expect(signals).toHaveLength(1);
        proof = null;
        throw new Error('synthetic response lost after cookie removal');
      },
    });
    try {
      await auth.start();
      const result = await auth.change({ kind: 'logout' });
      expect(result).toMatchObject({
        outcome: 'unknown',
        state: { phase: 'guest' },
      });
      expect(posts).toBe(1);
      expect(signals).toMatchObject([
        { type: 'auth_transition_begin' },
        { type: 'auth_transition_end' },
      ]);
      expect(Object.keys(signals[0] as object).sort()).toEqual([
        'transitionId',
        'type',
      ]);
    } finally {
      auth.dispose();
    }
  });
});
