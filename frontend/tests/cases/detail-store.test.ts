import { afterEach, expect, it } from 'vitest';
import {
  detailFixture,
  closedCase,
  CASE,
  NEXT_CASE,
  OTHER,
  json,
  error,
} from './detail-fixture';
import { decodeCase } from '../../src/features/cases/detail-contracts';
const owned: ReturnType<typeof detailFixture>[] = [];
const make = () => {
  const f = detailFixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('reads the full authorized case without writes and accepts null creation history', async () => {
  const f = make();
  await f.ready();
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    detail: {
      id: CASE,
      status: 'closed',
      version: 2,
      history: [
        { from_status: null },
        { resolution: 'Explained how to review the existing parking booking.' },
      ],
    },
  });
  expect(f.detailServer.posts).toHaveLength(0);
});
it('explicit same-issue confirmation sends exactly the four contract fields and preserves closure history', async () => {
  const f = make();
  await f.ready();
  await f.details.continue(f.prepare());
  expect(f.detailServer.posts[0]).toMatchObject({
    path: '/api/v1/cases/' + CASE + '/continue',
    body: {
      expected_version: 2,
      intent: 'same_issue',
      message: 'The same parking issue still needs help.',
    },
    idempotency: null,
  });
  expect(Object.keys(f.detailServer.posts[0]!.body).sort()).toEqual([
    'client_request_id',
    'expected_version',
    'intent',
    'message',
  ]);
  expect(f.detailServer.posts[0]!.body.client_request_id).toMatch(
    /^[a-f0-9-]{36}$/,
  );
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    notice: 'recorded',
    detail: {
      status: 'new',
      close_reason: null,
      resolution: null,
      next_actor: 'staff',
    },
  });
  expect(f.details.getState().detail?.history[1]?.resolution).toBe(
    closedCase().resolution,
  );
  expect(f.detailServer.applied).toBe(1);
});
it('lost response followed by active GET stays uncertain and never infers own success from matching history', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseAfter = true;
  await f.details.continue(f.prepare());
  expect(f.details.getState()).toMatchObject({
    phase: 'unknown',
    unknown: true,
    canRetry: false,
    notice: 'uncertain',
    detail: { status: 'new', version: 3 },
  });
  await f.details.recover();
  await f.details.retry();
  expect(f.detailServer.posts).toHaveLength(1);
  expect(f.detailServer.applied).toBe(1);
});
it('unknown unchanged closed case permits only explicit same-body/client-ID retry after another read', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseBefore = true;
  await f.details.continue(f.prepare());
  expect(f.details.getState()).toMatchObject({
    phase: 'unknown',
    canRetry: true,
  });
  await f.details.recover();
  expect(f.detailServer.posts).toHaveLength(1);
  f.detailServer.loseBefore = false;
  await f.details.retry();
  expect(f.detailServer.posts).toHaveLength(2);
  expect(f.detailServer.posts[0]).toEqual(f.detailServer.posts[1]);
  expect(f.detailServer.applied).toBe(1);
  expect(f.details.getState().notice).toBe('recorded');
});
it('changed version while unknown blocks retry and requires explicit fresh review, never new UUID automatically', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseBefore = true;
  await f.details.continue(f.prepare());
  f.detailServer.detail.version = 3;
  await f.details.recover();
  await f.details.retry();
  expect(f.details.getState().canRetry).toBe(false);
  expect(f.detailServer.posts).toHaveLength(1);
  await f.details.reviewAgain();
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    notice: 'changed',
    editing: false,
    unknown: false,
  });
  f.detailServer.loseBefore = false;
  await f.details
    .continue({ ...f.prepare(), sameIssue: false })
    .catch(() => {});
  expect(f.detailServer.posts).toHaveLength(1);
  await f.details.continue(f.prepare());
  expect(f.detailServer.posts[1]!.body.client_request_id).not.toBe(
    f.detailServer.posts[0]!.body.client_request_id,
  );
});
it('409 refreshes actual case and does not silently replace or replay consent', async () => {
  const f = make();
  await f.ready();
  let posts = 0;
  f.detailServer.handler = (_path, init) => {
    if (init?.method === 'POST') {
      posts++;
      f.detailServer.detail.version = 3;
      return Promise.resolve(error(409, 'VERSION_CONFLICT'));
    }
    return null;
  };
  await f.details.continue(f.prepare());
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    editing: false,
    notice: 'changed',
    detail: { version: 3 },
  });
  expect(posts).toBe(1);
});
it('422 leaves plain input available for correction without implying success', async () => {
  const f = make();
  await f.ready();
  f.detailServer.handler = (_p, i) =>
    i?.method === 'POST'
      ? Promise.resolve(error(422, 'VALIDATION_ERROR'))
      : null;
  await f.details.continue(f.prepare());
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    editing: true,
    unknown: false,
    error: { status: 422 },
    message: 'The same parking issue still needs help.',
  });
});
it('foreign404 purges report, history, input, unknown and executable retry', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseBefore = true;
  await f.details.continue(f.prepare());
  f.detailServer.handler = () =>
    Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  await f.details.recover();
  await f.details.retry();
  expect(f.details.getState()).toMatchObject({
    phase: 'unavailable',
    detail: null,
    message: '',
    unknown: false,
    canRetry: false,
  });
  expect(f.detailServer.posts).toHaveLength(1);
});
it('503 hides old private case and withholds retry until a fresh authorized GET', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseBefore = true;
  await f.details.continue(f.prepare());
  f.detailServer.handler = () => Promise.resolve(error(503, 'UNAVAILABLE'));
  await f.details.recover();
  expect(f.details.getState()).toMatchObject({
    phase: 'unavailable',
    detail: null,
    canRetry: false,
  });
  await f.details.retry();
  expect(f.detailServer.posts).toHaveLength(1);
  f.detailServer.handler = null;
  await f.details.open(CASE);
  expect(f.details.getState()).toMatchObject({
    phase: 'unknown',
    canRetry: true,
  });
});
it('soft recheck and route detach destroy commands but restore plain owned explanation after authorized GET', async () => {
  const f = make();
  await f.ready();
  f.detailServer.loseBefore = true;
  await f.details.continue(f.prepare());
  await f.auth.recover();
  await f.details.open(CASE);
  expect(f.details.getState()).toMatchObject({
    phase: 'ready',
    unknown: true,
    editing: false,
    canRetry: false,
    message: 'The same parking issue still needs help.',
  });
  await f.details.retry();
  expect(f.detailServer.posts).toHaveLength(1);
  f.details.detach(CASE);
  await f.details.open(CASE);
  expect(f.details.getState().canRetry).toBe(false);
  expect(f.detailServer.posts).toHaveLength(1);
});
it('auth replacement and different case selection fence a late successful POST', async () => {
  const f = make();
  await f.ready();
  let finish!: (r: Response) => void;
  f.detailServer.handler = (_p, i) =>
    i?.method === 'POST'
      ? new Promise((r) => {
          finish = r;
        })
      : null;
  const sending = f.details.continue(f.prepare());
  await Promise.resolve();
  await f.details.open(NEXT_CASE);
  finish(json({ ...closedCase(), version: 4 }));
  await sending;
  expect(f.details.getState()).toMatchObject({
    detail: { id: NEXT_CASE },
    notice: null,
  });
  f.server.proof = 'q'.repeat(43);
  await f.auth.recover();
  expect(f.details.getState().detail).toBeNull();
  await f.details.open(NEXT_CASE);
  expect(f.details.getState().message).toBe('');
});
it('does not offer continuation for active cases or accept stale versions and unconfirmed/blank explanations', async () => {
  const f = make();
  await f.ready();
  const intent = f.prepare();
  await f.details.continue({ ...intent, version: 1 });
  await expect(
    f.details.continue({ ...intent, sameIssue: false }),
  ).rejects.toThrow();
  await expect(
    f.details.continue({ ...intent, message: ' ' }),
  ).rejects.toThrow();
  f.detailServer.detail = {
    ...closedCase(),
    status: 'new',
    close_reason: null,
    resolution: null,
    closed_at: null,
    next_actor: 'staff',
  };
  await f.details.open(CASE);
  f.details.beginContinue();
  await f.details.continue(intent);
  expect(f.detailServer.posts).toHaveLength(0);
});
it('rejects wrong-ID/malformed success as uncertain, not as a successful continuation', async () => {
  const f = make();
  await f.ready();
  f.detailServer.handler = (_p, i) =>
    i?.method === 'POST'
      ? Promise.resolve(json({ ...closedCase(), id: NEXT_CASE, version: 3 }))
      : null;
  await f.details.continue(f.prepare());
  expect(f.details.getState()).toMatchObject({
    phase: 'unknown',
    notice: 'uncertain',
  });
});
it('validates bounded snapshot history and strict enums without coercion', () => {
  expect(decodeCase(closedCase())).toEqual(closedCase());
  for (const patch of [
    { status: ['closed'] },
    { next_actor: 'invalid' },
    { id: 'bad' },
    { closed_at: null },
    { resolution: null },
  ])
    expect(() => decodeCase({ ...closedCase(), ...patch })).toThrow();
  expect(() =>
    decodeCase({
      ...closedCase(),
      history: [
        {
          ...closedCase().history[0],
          resulting_version: 3,
          previous_version: 2,
        },
      ],
    }),
  ).toThrow();
  expect(() =>
    decodeCase({
      ...closedCase(),
      history: [...closedCase().history, ...closedCase().history],
    }),
  ).toThrow();
  expect(OTHER).not.toBe(CASE);
});
