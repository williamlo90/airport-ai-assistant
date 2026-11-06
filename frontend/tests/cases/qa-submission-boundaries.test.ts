import { afterEach, expect, it, vi } from 'vitest';
import {
  submissionFixture,
  DRAFT,
  OTHER,
  json,
  result,
} from './submission-fixture';
import { error } from './fixture';
import { decodeAction } from '../../src/features/cases/submission';

const owned: ReturnType<typeof submissionFixture>[] = [];
const make = () => {
  const f = submissionFixture();
  owned.push(f);
  return f;
};
afterEach(() => {
  vi.restoreAllMocks();
  owned.splice(0).forEach((f) => f.dispose());
});
const deferred = () => {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

it('late confirm403 cannot discard a newly prepared intake after leaving submission', async () => {
  const f = make();
  await f.ready();
  const delayed = deferred();
  f.submits.handler = (path) =>
    path.endsWith('/confirm') ? delayed.promise : null;
  const sending = f.submission.confirm(f.intent());
  f.submission.detach(DRAFT);
  await f.cases.prepareFromChat('contact', 'customer@example.com');
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'New intake after leaving review',
  });
  delayed.resolve(error(403, 'ACTION_FORBIDDEN'));
  await sending;
  expect(f.cases.getState()).toMatchObject({
    phase: 'intake',
    form: { summary: 'New intake after leaving review' },
  });
});

it('late confirm409 cannot reopen the old preview over a newly prepared intake', async () => {
  const f = make();
  await f.ready();
  const delayed = deferred();
  f.submits.handler = (path) =>
    path.endsWith('/confirm') ? delayed.promise : null;
  const sending = f.submission.confirm(f.intent());
  f.submission.detach(DRAFT);
  await f.cases.prepareFromChat('contact', 'customer@example.com');
  f.cases.input({
    ...f.cases.getState().form,
    summary: 'Different active intake',
  });
  delayed.resolve(error(409, 'VERSION_CONFLICT'));
  await sending;
  expect(f.cases.getState()).toMatchObject({
    phase: 'intake',
    draft: null,
    form: { summary: 'Different active intake' },
  });
});

it('honors Retry-After before another explicit confirm request', async () => {
  const f = make();
  await f.ready();
  let posts = 0;
  f.submits.handler = (path) => {
    if (!path.endsWith('/confirm')) return null;
    posts++;
    const response = error(429, 'RATE_LIMITED');
    response.headers.set('Retry-After', '60');
    return Promise.resolve(response);
  };
  await f.submission.confirm(f.intent());
  await f.submission.confirm(f.intent());
  expect(posts).toBe(1);
});

it('accepts a pending handoff action binding without mistaking it for a committed receipt', () => {
  expect(
    decodeAction({
      draft_id: DRAFT,
      state: 'pending',
      version: 1,
      expires_at: '2030-01-01T00:00:00Z',
      case_id: OTHER,
      result_ref: null,
    }),
  ).toMatchObject({ state: 'pending', case_id: OTHER });
});

it('a late successful confirm cannot restore a receipt after auth proof rotation', async () => {
  const f = make();
  await f.ready();
  const delayed = deferred();
  f.submits.handler = (path) =>
    path.endsWith('/confirm') ? delayed.promise : null;
  const sending = f.submission.confirm(f.intent());
  f.server.proof = 'z'.repeat(43);
  await f.auth.recheck();
  delayed.resolve(json(result(), 201));
  await sending;
  expect(f.submission.getState().receipt).toBeNull();
  expect(f.submission.getState().phase).toBe('idle');
});

it('unavailable status after unknown confirm cannot unlock a manual retry POST', async () => {
  const f = make();
  await f.ready();
  let posts = 0;
  f.submits.handler = (path) => {
    if (path.endsWith('/confirm')) {
      posts++;
      return Promise.reject(Error('Unknown result'));
    }
    return Promise.resolve(error(503, 'CASE_UNAVAILABLE'));
  };
  await f.submission.confirm(f.intent());
  await f.submission.retry();
  expect(posts).toBe(1);
  expect(f.submission.getState()).toMatchObject({
    phase: 'unknown',
    canRetry: false,
    receipt: null,
  });
});

it('429 wait survives status reopening and auth recheck without an automatic send when time advances', async () => {
  const f = make();
  await f.ready();
  const now = Date.now();
  vi.spyOn(Date, 'now').mockReturnValue(now);
  let posts = 0;
  f.submits.handler = (path) => {
    if (!path.endsWith('/confirm')) return null;
    posts++;
    const response = error(429, 'RATE_LIMITED');
    response.headers.set('Retry-After', '60');
    return Promise.resolve(response);
  };
  await f.submission.confirm(f.intent());
  await f.auth.recheck();
  await f.submission.open(DRAFT);
  await f.submission.confirm(f.intent());
  expect(posts).toBe(1);
  vi.spyOn(Date, 'now').mockReturnValue(now + 61000);
  expect(posts).toBe(1);
  await f.submission.confirm(f.intent());
  expect(posts).toBe(2);
});

it('a new authorized session can recover a committed receipt without accessing its old session-bound draft', async () => {
  const f = make();
  await f.ready();
  f.previewServer.draft.state = 'committed';
  f.server.proof = 'z'.repeat(43);
  await f.auth.recheck();
  let forbiddenDraftGets = 0;
  f.previewServer.handler = (method) => {
    if (method !== 'GET') return null;
    forbiddenDraftGets++;
    return Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  };
  await f.submission.open(DRAFT);
  expect(f.submission.getState().phase).toBe('receipt');
  expect(forbiddenDraftGets).toBe(0);
  expect(f.submits.requests).toHaveLength(0);
});

it('leaving and returning to an unknown request requires fresh review without reconstructing executable consent', async () => {
  const f = make();
  await f.ready();
  f.submits.loseBeforeCommit = true;
  await f.submission.confirm(f.intent());
  const first = f.submits.requests[0];
  f.submission.detach(DRAFT);
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
  expect(f.submits.requests).toHaveLength(2);
  expect(f.submits.requests[1]?.key).not.toBe(first?.key);
  expect(f.submits.commits).toBe(1);
});
