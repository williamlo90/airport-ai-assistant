import { afterEach, expect, it } from 'vitest';
import { caseFixture, DRAFT, preview } from './fixture';
import { detailFixture, CASE } from './detail-fixture';
import { submissionFixture } from './submission-fixture';
import { fixture, ID, json, error } from '../chat/fixture';

const owned: (() => void)[] = [];
afterEach(() => owned.splice(0).forEach((fn) => fn()));
it('source outage erases the independent submission command and receipt context and permits a fresh status read', async () => {
  const f = submissionFixture();
  owned.push(f.dispose);
  await f.ready();
  expect(f.submission.getState().phase).toBe('review');
  await outage(f);
  expect(f.submission.getState()).toMatchObject({
    phase: 'unavailable',
    receipt: null,
    receiptContext: null,
    canRetry: false,
  });
  await f.submission.open(DRAFT);
  expect(f.submission.getState().phase).toBe('review');
});
async function outage(f: ReturnType<typeof fixture>) {
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await expect(
    f.auth.request({
      method: 'GET',
      path: '/source/check',
      boundary: f.auth.capture(),
      isRelevant: () => true,
      successStatus: 200,
      decode: (v) => v,
    }),
  ).rejects.toMatchObject({ code: 'SOURCE_UNAVAILABLE' });
  f.server.override = null;
}
it('an outage from another resource erases case detail and draft materializations and editable input', async () => {
  const a = caseFixture(),
    b = detailFixture();
  owned.push(a.dispose, b.dispose);
  await a.ready();
  await a.cases.create();
  await b.ready();
  b.prepare('Private continuation');
  expect(a.cases.getState().draft).not.toBeNull();
  expect(b.details.getState().message).toBe('Private continuation');
  await outage(a);
  await outage(b);
  expect(a.cases.getState().draft).toBeNull();
  expect(a.cases.getState().form.customer_report).toBe('');
  expect(b.details.getState()).toMatchObject({
    detail: null,
    message: '',
    unknown: false,
  });
  expect(a.cases.queries.getQueryCache().getAll()).toHaveLength(0);
  expect(b.details.queries.getQueryCache().getAll()).toHaveLength(0);
  await b.details.open(CASE);
  expect(b.details.getState().detail?.id).toBe(CASE);
});
it('late draft success after cross-resource source failure cannot restore private content', async () => {
  const f = caseFixture();
  owned.push(f.dispose);
  await f.ready();
  let resolve!: (value: Response) => void;
  f.fetcher.mockImplementationOnce(
    () =>
      new Promise<Response>((r) => {
        resolve = r;
      }),
  );
  const loading = f.cases.open(DRAFT);
  for (let i = 0; i < 20 && !resolve; i++) await Promise.resolve();
  await outage(f);
  resolve(json(preview()));
  await loading;
  expect(f.cases.getState().draft).toBeNull();
  await f.cases.open(DRAFT);
  expect(f.cases.getState().draft?.draft_id).toBe(DRAFT);
});
it('metadata source failure does not leave a settled open promise blocking same-owner recovery', async () => {
  const f = fixture();
  owned.push(f.dispose);
  await f.auth.start();
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await f.store.open(ID);
  expect(f.store.getState().meta).toBeNull();
  f.server.override = null;
  await f.store.open(ID);
  expect(f.store.getState().meta?.conversation_id).toBe(ID);
});
it('late case detail after cross-resource source failure cannot restore the former materialization', async () => {
  const f = detailFixture();
  owned.push(f.dispose);
  await f.ready();
  let resolve!: (value: Response) => void;
  f.fetcher.mockImplementationOnce(
    () =>
      new Promise<Response>((r) => {
        resolve = r;
      }),
  );
  const loading = f.details.open(CASE);
  for (let i = 0; i < 20 && !resolve; i++) await Promise.resolve();
  await outage(f);
  resolve(json(f.detailServer.detail));
  await loading;
  expect(f.details.getState().detail).toBeNull();
  await f.details.open(CASE);
  expect(f.details.getState().detail?.id).toBe(CASE);
});
