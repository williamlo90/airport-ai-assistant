import { afterEach, expect, it } from 'vitest';
import {
  submissionFixture,
  DRAFT,
  NEXT,
  OTHER,
  ID,
  json,
  result,
} from './submission-fixture';
import { error } from './fixture';
import {
  decodeAction,
  decodeCommit,
} from '../../src/features/cases/submission';
const owned: ReturnType<typeof submissionFixture>[] = [];
const make = () => {
  const f = submissionFixture();
  owned.push(f);
  return f;
};
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('reads status first and never posts on entry or repeated opening', async () => {
  const f = make();
  await f.ready();
  await f.submission.open(DRAFT);
  expect(f.submits.gets).toBe(2);
  expect(f.submits.requests).toHaveLength(0);
  expect(f.submission.getState().phase).toBe('review');
});
it('sends only reviewed version/hash with stable UUID key and shows minimal committed receipt', async () => {
  const f = make();
  await f.ready();
  await f.submission.confirm(f.intent());
  expect(f.submits.requests[0]).toMatchObject({
    body: { expected_version: 1, payload_hash: 'a'.repeat(64) },
  });
  expect(f.submits.requests[0]?.key).toMatch(/^[a-f0-9-]{36}$/);
  expect(f.submits.commits).toBe(1);
  expect(f.submission.getState()).toMatchObject({
    phase: 'receipt',
    receipt: {
      case_id: OTHER,
      result_ref: result().result_ref,
      expires_at: null,
    },
  });
  await f.submission.confirm(f.intent());
  expect(f.submits.requests).toHaveLength(1);
});
it('recovers lost confirm response by GET actions with exactly one commit and no resend', async () => {
  const f = make();
  await f.ready();
  f.submits.loseResponse = true;
  await f.submission.confirm(f.intent());
  expect(f.submission.getState().phase).toBe('receipt');
  expect(f.submits.commits).toBe(1);
  expect(f.submits.requests).toHaveLength(1);
  expect(f.submits.gets).toBe(2);
});
it('pending does not mean failed; explicit retry checks again and preserves command key/body', async () => {
  const f = make();
  await f.ready();
  f.submits.loseBeforeCommit = true;
  await f.submission.confirm(f.intent());
  expect(f.submission.getState()).toMatchObject({
    phase: 'unknown',
    canRetry: true,
  });
  expect(f.submits.requests).toHaveLength(1);
  await f.submission.recover();
  expect(f.submits.requests).toHaveLength(1);
  f.submits.loseBeforeCommit = false;
  await f.submission.retry();
  expect(f.submits.requests).toHaveLength(2);
  expect(f.submits.requests[0]).toEqual(f.submits.requests[1]);
  expect(f.submits.commits).toBe(1);
});
it('retry discovers a late commit before POSTing again', async () => {
  const f = make();
  await f.ready();
  f.submits.loseBeforeCommit = true;
  await f.submission.confirm(f.intent());
  f.previewServer.draft.state = 'committed';
  await f.submission.retry();
  expect(f.submits.requests).toHaveLength(1);
  expect(f.submission.getState().phase).toBe('receipt');
});
it('committed reload needs only actions authorization and no old preview or TTL', async () => {
  const f = make();
  await f.ready();
  f.previewServer.draft.state = 'committed';
  f.previewServer.draft.expires_at = '2001-01-01T00:00:00Z';
  const gets = f.previewServer.gets;
  f.previewServer.handler = () =>
    Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  await f.submission.open(DRAFT);
  expect(f.submission.getState().phase).toBe('receipt');
  expect(f.previewServer.gets).toBe(gets);
  expect(f.submits.requests).toHaveLength(0);
});
it('soft auth recheck drops executable consent; pending requires a fresh review and new explicit send', async () => {
  const f = make();
  await f.ready();
  f.submits.loseBeforeCommit = true;
  await f.submission.confirm(f.intent());
  const before = f.submits.requests[0];
  await f.auth.recover();
  await f.submission.open(DRAFT);
  expect(f.submission.getState()).toMatchObject({
    phase: 'review',
    reviewAgain: true,
    canRetry: false,
  });
  await f.submission.retry();
  expect(f.submits.requests).toHaveLength(1);
  f.submits.loseBeforeCommit = false;
  await f.submission.confirm(f.intent());
  expect(f.submits.requests[1]?.key).not.toBe(before?.key);
  expect(f.submits.commits).toBe(1);
});
it('status503/404/malformed cannot unlock confirm or fabricate a receipt', async () => {
  const f = make();
  await f.ready();
  const intent = f.intent();
  for (const response of [
    error(503, 'UNAVAILABLE'),
    error(404, 'RESOURCE_NOT_FOUND'),
    json({
      draft_id: DRAFT,
      state: 'committed',
      version: 1,
      case_id: OTHER,
      expires_at: null,
      result_ref: null,
    }),
  ]) {
    f.submits.handler = () => Promise.resolve(response.clone());
    await f.submission.open(DRAFT);
    await f.submission.confirm(intent);
    expect(f.submission.getState()).toMatchObject({
      phase: 'unavailable',
      receipt: null,
    });
  }
  expect(f.submits.requests).toHaveLength(0);
});
it('409 and410 fetch latest state, require review, and never rewrite/resend command', async () => {
  const f = make();
  await f.ready();
  let posts = 0;
  f.submits.handler = (path) => {
    if (path.endsWith('/confirm')) {
      posts++;
      f.previewServer.draft.version++;
      f.previewServer.draft.payload_hash = 'b'.repeat(64);
      return Promise.resolve(error(409, 'CASE_DESTINATION_CHANGED'));
    }
    return null;
  };
  await f.submission.confirm(f.intent());
  expect(f.submission.getState()).toMatchObject({
    phase: 'review',
    reviewAgain: true,
  });
  expect(f.cases.getState().draft?.version).toBe(2);
  expect(posts).toBe(1);
  f.submits.handler = (path) => {
    if (path.endsWith('/confirm')) {
      posts++;
      f.previewServer.draft.state = 'expired';
      return Promise.resolve(error(410, 'DRAFT_EXPIRED'));
    }
    return null;
  };
  await f.submission.confirm(f.intent());
  await f.submission.confirm(f.intent());
  expect(posts).toBe(2);
  expect(f.cases.getState().draft?.state).toBe('expired');
});
it('422 leaves contact correction available without a verified badge or automatic retry', async () => {
  const f = make();
  await f.ready();
  f.submits.handler = (path) =>
    path.endsWith('/confirm')
      ? Promise.resolve(error(422, 'CASE_CONTACT_VERIFICATION_REQUIRED'))
      : null;
  await f.submission.confirm(f.intent());
  expect(f.submission.getState()).toMatchObject({
    phase: 'review',
    error: { status: 422 },
  });
  f.cases.beginEdit();
  expect(f.cases.getState().editing).toBe(true);
});
it('late confirmation cannot replace another selected draft or a changed identity', async () => {
  const f = make();
  await f.ready();
  let finish!: (r: Response) => void;
  f.submits.handler = (path) =>
    path.endsWith('/confirm')
      ? new Promise((r) => {
          finish = r;
        })
      : null;
  const sending = f.submission.confirm(f.intent());
  await Promise.resolve();
  await f.submission.open(NEXT);
  finish(json(result(), 201));
  await sending;
  expect(f.submission.getState()).toMatchObject({
    phase: 'review',
    receipt: null,
  });
  expect(f.cases.getState().draft?.draft_id).toBe(NEXT);
  f.submits.handler = null;
  const old = f.intent();
  f.server.proof = 'q'.repeat(43);
  await f.auth.recover();
  await f.submission.confirm(old);
  expect(f.submits.requests).toHaveLength(0);
});
it('rejects stale intent, dirty editor and expired preview before any dispatch', async () => {
  const f = make();
  await f.ready();
  const intent = f.intent();
  await f.submission.confirm({ ...intent, hash: 'b'.repeat(64) });
  f.cases.beginEdit();
  await f.submission.confirm(intent);
  f.cases.stopEdit();
  f.previewServer.draft.state = 'expired';
  await f.submission.open(DRAFT);
  await f.submission.confirm(intent);
  expect(f.submits.requests).toHaveLength(0);
});
it('validates receipt/status and POST result separately without enum coercion or invented fields', () => {
  expect(decodeCommit(result())).toEqual(result());
  const committed = {
    draft_id: DRAFT,
    state: 'committed',
    version: 1,
    expires_at: null,
    case_id: OTHER,
    result_ref: result().result_ref,
  };
  expect(decodeAction(committed)).toEqual(committed);
  for (const patch of [
    { state: ['committed'] },
    { case_id: null },
    { result_ref: null },
    { expires_at: '2001-01-01T00:00:00Z' },
    { version: 0 },
    { draft_id: 'bad' },
  ])
    expect(() => decodeAction({ ...committed, ...patch })).toThrow();
  expect(() => decodeCommit({ ...result(), message_id: undefined })).toThrow();
  expect(() => decodeCommit({ ...result(), state: ['committed'] })).toThrow();
  expect(() =>
    decodeAction({
      ...committed,
      state: 'pending',
      case_id: null,
      result_ref: null,
      expires_at: null,
    }),
  ).toThrow();
  expect(ID).not.toBe(OTHER);
});
